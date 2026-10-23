"""Bounded local collection through normal profile/post navigation, without API replay."""
from urllib.parse import urlparse
from .browser import decode_payloads, apply_payload
from .profiles import parse_profile_html
from .post_dates import post_url
from .demo_media import apply_demo_html, apply_demo_payload, image_url


class InstagramRestricted(ValueError):
    def __init__(self, message, reason='access_restricted', status=None):
        super().__init__(message)
        self.reason = reason
        self.status = status


def content_endpoint(url):
    """Ignore telemetry/notifications failures; never retain signed URLs."""
    parsed = urlparse(url)
    if parsed.hostname not in {'www.instagram.com', 'instagram.com'}:
        return False
    path = parsed.path.lower()
    return '/graphql/' in path or any(x in path for x in ('/api/v1/feed/', '/api/v1/media/', '/api/v1/users/'))



def collect_demo(browser, username, count=6, max_pages=18, max_scrolls=12, preflight=None, period_days=None, on_progress=None):
    from playwright.sync_api import Error
    lead = {'username': username, 'profile_url': f'https://www.instagram.com/{username}/', 'photo_posts': []}
    from datetime import datetime, timezone, timedelta
    from .post_dates import timestamp
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=period_days)).isoformat() if period_days else None
    def in_period(post):
        date = timestamp(post.get('date'))
        return bool(date and (not cutoff or cutoff <= date <= now.isoformat()))
    if period_days:
        lead.update(period_days=period_days, photo_post_limit=501)
    boundary = False
    restricted = [None]
    debug = {'pages': 0, 'scrolls': 0, 'payloads': 0}
    lead['demo_debug'] = debug

    def capture(response):
        parsed = urlparse(response.url)
        if parsed.hostname not in {'www.instagram.com', 'instagram.com'} or not any(x in parsed.path for x in ('/api/', '/graphql/')):
            return
        if response.status in {401, 403, 429} and content_endpoint(response.url):
            restricted[0] = InstagramRestricted('Instagram ограничил загрузку публикаций.', 'content_http', response.status)
            return
        if response.status != 200 or not content_endpoint(response.url):
            return
        try:
            if int(response.headers.get('content-length', '0')) > 3_000_000:
                return
            body = response.body()
            if len(body) <= 3_000_000:
                for payload in decode_payloads(body):
                    apply_payload(lead, payload)
                    apply_demo_payload(lead, payload)
                    debug['payloads'] += 1
        except (Error, ValueError):
            pass

    def visit(page, url):
        page.on('response', capture)
        debug['pages'] += 1
        response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(2000)
        if restricted[0]:
            raise restricted[0]
        if any(x in page.url for x in ('/accounts/login', '/challenge/', '/checkpoint/')):
            raise InstagramRestricted('Instagram требует вход или проверку.', 'login_or_challenge')
        if response and response.status in {401, 403, 429}:
            raise InstagramRestricted('Страница Instagram ограничена.', 'page_http', response.status)
        if response and response.status >= 400:
            raise ValueError('Страница Instagram недоступна')
        return page.content()

    profile = browser.context.new_page()
    try:
        html = visit(profile, lead['profile_url'])
        parse_profile_html(lead, html)
        apply_demo_html(lead, html)
        if preflight is not None:
            # First check the profile/site; only inspect dates if still needed.
            if not preflight(lead, False):
                return lead
            browser.collect_posts(lead, profile, [], capture)
            if restricted[0] or lead.get('post_access_restricted'):
                raise restricted[0] or InstagramRestricted('Instagram ограничил доступ. Партия остановлена.')
            if not preflight(lead, True):
                return lead
        if lead.get('is_private'):
            raise ValueError('Закрытый аккаунт: демо не создаётся')
        urls = []
        stale = 0
        previous = None
        for scroll in range(max_scrolls + 1):
            # Instagram can render its grid outside main, and may need several
            # screens of mostly reels before another still photo is loaded.
            hrefs = profile.locator('a[href*="/p/"]').evaluate_all('(links) => links.slice(0, 120).map(a => a.href)')
            urls = list(dict.fromkeys(urls + [post_url(href, username) for href in hrefs if post_url(href, username)]))
            apply_demo_html(lead, profile.content())
            complete = [p for p in lead['photo_posts'] if p.get('images') and p.get('date')]
            if on_progress:
                on_progress({'phase': 'profile', 'posts': sum(in_period(p) for p in lead['photo_posts']), 'scanned': len(lead.get('_timeline_dates', {}))})
            progress = (len(urls), len(lead['photo_posts']), profile.evaluate('document.documentElement.scrollHeight'))
            stale = stale + 1 if progress == previous else 0
            previous = progress
            if period_days:
                old = [v for v in lead.get('_timeline_dates', {}).values() if not v['pinned'] and v['date'] < cutoff]
                boundary = len(old) >= 3 and len(lead.get('_timeline_dates', {})) >= 12
            if boundary or (not period_days and len(complete) >= count) or len(urls) >= max_pages or scroll == max_scrolls or stale >= 3:
                break
            profile.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
            profile.wait_for_timeout(1500)
            debug['scrolls'] += 1
            if restricted[0]:
                raise restricted[0] or InstagramRestricted('Instagram ограничил доступ. Партия остановлена.')
        # Capture JSON inserted after scrolling, too.
        apply_demo_html(lead, profile.content())
        debug['photo_links'] = len(urls)
        debug['stop_reason'] = 'period_boundary' if boundary else 'enough_photos' if len(complete) >= count else ('no_progress' if stale >= 3 else 'collection_limit')
        urls = list(dict.fromkeys(urls + [p['url'] for p in lead.get('photo_posts', [])]))[:max_pages]
        for url in urls:
            if on_progress:
                on_progress({'phase': 'posts', 'posts': sum(in_period(p) for p in lead['photo_posts']), 'pages': debug['pages']})
            if not period_days and sum(bool(p.get('images') and p.get('date')) for p in lead['photo_posts']) >= count:
                break
            if restricted[0]:
                raise restricted[0] or InstagramRestricted('Instagram ограничил доступ. Партия остановлена.')
            code = url.rstrip('/').rsplit('/', 1)[-1]
            known = next((p for p in lead['photo_posts'] if p['code'] == code), None)
            if period_days and known and known.get('date') and not in_period(known):
                continue
            if known and known.get('images') and known.get('date') and (not period_days or known.get('kind') != 'carousel' or len(known['images']) >= 8 or known.get('carousel_complete')):
                continue
            page = browser.context.new_page()
            try:
                html = visit(page, url)
                document = apply_demo_html(lead, html, url)
                # A conservative DOM fallback: explicit photo metadata + author + date.
                # Unknown types, videos, avatar images and comment text are not imported.
                title = document.meta.get('og:title', '')
                import re
                owned = re.search(r'\(@' + re.escape(username) + r'\)', title, re.I)
                if owned and document.meta.get('og:type') in {'instapp:photo', 'photo'} and not page.locator('article video, main video').count():
                    article = page.locator('article').first
                    caption = article.locator('h1').first.text_content(timeout=1000) if article.locator('h1').count() else ''
                    times = article.locator('time[datetime]').evaluate_all('(times) => times.map(t => ({date:t.getAttribute("datetime"),href:t.closest("a[href]")?.href||""}))')
                    date = next((t['date'] for t in times if post_url(t['href'], username) == url), None)
                    image = image_url(document.meta.get('og:image'))
                    if image and date:
                        apply_demo_payload(lead, {'shortcode': code, 'owner': {'username': username},
                            'type': 'Image', 'display_url': image, 'caption': caption, 'timestamp': date})
            except Error:
                debug['page_errors'] = debug.get('page_errors', 0) + 1
            finally:
                page.close()
        if period_days:
            lead['photo_posts'] = [p for p in lead['photo_posts'] if in_period(p)]
            lead['period'] = {'days': period_days, 'since': cutoff, 'until': now.isoformat(), 'complete': bool(boundary or stale >= 3) and len(lead['photo_posts']) <= 500 and all(p.get('kind') != 'carousel' or len(p['images']) >= 8 or p.get('carousel_complete') for p in lead['photo_posts'])}
            lead.pop('_timeline_dates', None)
        lead['photo_posts'] = sorted(lead['photo_posts'], key=lambda p: p['date'] or '', reverse=True)[:count]
        return lead
    finally:
        profile.close()
