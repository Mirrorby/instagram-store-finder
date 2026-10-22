"""Bounded public HTML/CSS reference analysis; never uses Instagram cookies.

HTTPS connections are pinned to a validated public IP while preserving TLS SNI
and certificate verification. Every redirect and CSS resource is revalidated.
JavaScript is not executed: results explicitly describe static source only.
"""
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
import urllib3

MAX_BYTES = 1_000_000


def public_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError('invalid_reference_url')
    try:
        u = urlsplit(value.strip())
        host = u.hostname
        if u.scheme != 'https' or not host or u.username or u.password or u.port not in (None, 443):
            raise ValueError('invalid_reference_url')
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError('private_reference_url')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host:
                raise ValueError('invalid_reference_url')
        else:
            if not address.is_global:
                raise ValueError('private_reference_url')
        return urlunsplit(('https', u.netloc, u.path or '/', u.query, ''))
    except (TypeError, AttributeError):
        raise ValueError('invalid_reference_url')


def public_addresses(host):
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('private_reference_address')
    return addresses


def fetch_public(url, content_types=('text/html',), redirects=4):
    for _ in range(redirects + 1):
        url = public_url(url)
        parsed = urlsplit(url)
        ip = public_addresses(parsed.hostname)[0]
        pool = urllib3.HTTPSConnectionPool(ip, port=443, assert_hostname=parsed.hostname,
                                          server_hostname=parsed.hostname, cert_reqs='CERT_REQUIRED')
        response = None
        try:
            response = pool.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''),
                                    headers={'Host': parsed.netloc, 'User-Agent': 'DelokayReferenceCollector/1.0',
                                             'Accept-Encoding': 'identity'},
                                    timeout=urllib3.Timeout(connect=10, read=15), retries=False,
                                    redirect=False, preload_content=False)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                if not location:
                    raise ValueError('reference_redirect_missing')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError('reference_http_error')
            mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
            if mime not in content_types:
                raise ValueError('reference_content_type')
            # No compression accepted: bound both network and decoded data.
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('reference_encoding')
            body = response.read(MAX_BYTES + 1, decode_content=False)
            if len(body) > MAX_BYTES:
                raise ValueError('reference_too_large')
            return url, body.decode('utf-8', errors='replace')
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise ValueError('reference_redirect_limit')


class ReferenceHTML(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.title = ''
        self.description = ''
        self.headings, self.links, self.images, self.stylesheets = [], [], [], []
        self.inline_css, self.text = [], []
        self.capture = None
        self.parts = []
        self.skip = 0
        self.in_style = False
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.skip += 1
            self.scripts += 1
        if tag == 'style':
            self.in_style = True
        if attrs.get('style'):
            self.inline_css.append(attrs['style'][:2000])
        if tag in ('title', 'h1', 'h2', 'h3'):
            self.capture, self.parts = tag, []
        if tag == 'meta' and (attrs.get('name', '').lower() == 'description' or attrs.get('property') == 'og:description'):
            if not self.description:
                self.description = attrs.get('content', '')[:1000]
        if tag == 'link' and 'stylesheet' in attrs.get('rel', '').split() and len(self.stylesheets) < 6:
            self.stylesheets.append(urljoin(self.url, attrs.get('href', '')))
        if tag == 'a' and attrs.get('href') and len(self.links) < 40:
            url = urljoin(self.url, attrs['href'])
            if url.startswith('https://'):
                self.links.append(url[:2000])
        if tag == 'img' and len(self.images) < 12:
            url = urljoin(self.url, attrs.get('src', ''))
            if url.startswith('https://'):
                self.images.append({'url': url[:2000], 'alt': attrs.get('alt', '')[:200]})

    def handle_data(self, value):
        if self.in_style:
            self.inline_css.append(value[:MAX_BYTES])
        if self.skip or self.in_style:
            return
        value = ' '.join(value.split())
        if self.capture:
            self.parts.append(value)
        if value and sum(map(len, self.text)) < 8000:
            self.text.append(value)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.skip = max(0, self.skip - 1)
        if tag == 'style':
            self.in_style = False
        if tag == self.capture:
            value = ' '.join(self.parts).strip()[:300]
            if tag == 'title':
                self.title = value
            elif len(self.headings) < 50:
                self.headings.append({'level': int(tag[1]), 'text': value})
            self.capture, self.parts = None, []


def analyze_reference(url):
    url, html = fetch_public(url)
    parser = ReferenceHTML(url)
    parser.feed(html)
    css = list(parser.inline_css)
    failures = 0
    for stylesheet in dict.fromkeys(parser.stylesheets):
        try:
            _, source = fetch_public(stylesheet, ('text/css',))
            css.append(source)
        except Exception:
            failures += 1
    source = '\n'.join(css)
    colors = list(dict.fromkeys(re.findall(r'#[0-9a-fA-F]{3,8}\b', source)))[:16]
    fonts = list(dict.fromkeys(f.strip()[:200] for f in re.findall(r'font-family\s*:\s*([^;}]+)', source, re.I)))[:12]
    return {'url': url, 'title': parser.title, 'description': parser.description,
            'headings': parser.headings, 'links': parser.links, 'images': parser.images,
            'colors': colors, 'fonts': fonts, 'textExcerpt': '\n'.join(parser.text)[:8000],
            'analysis': 'static_html_css', 'javascriptExecuted': False,
            'stylesheetFailures': failures, 'scriptTags': parser.scripts,
            'needsRenderedReview': not parser.headings or len(''.join(parser.text)) < 200}
