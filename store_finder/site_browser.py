"""One bounded public-site visit in an isolated context, without Instagram cookies."""
import socket
from urllib.parse import urlparse
from .network import Page, public_url


class BrowserSiteFetcher:
    def __init__(self, browser):
        self.browser = browser

    def get(self, url, max_bytes=400000):
        from playwright.sync_api import Error, TimeoutError
        try:
            if not public_url(url): return Page(url, error='unsafe_url')
        except socket.gaierror:
            return Page(url, error='dns_error')
        context = None
        try:
            context = self.browser.new_context(ignore_https_errors=False, accept_downloads=False)
            page = context.new_page()
        except Error:
            if context:
                try: context.close()
                except Error: pass
            return Page(url, error='request_error')
        blocked_navigation = False

        def guard(route):
            nonlocal blocked_navigation
            request = route.request
            try:
                allowed = urlparse(request.url).scheme in {'http', 'https'} and public_url(request.url)
            except socket.gaierror:
                allowed = False
            if not allowed:
                blocked_navigation |= request.is_navigation_request() and request.frame == page.main_frame
                route.abort()
            elif request.resource_type in {'image', 'font', 'media'}:
                route.abort()
            else:
                route.continue_()

        page.route('**/*', guard)
        try:
            response = page.goto(url, wait_until='domcontentloaded', timeout=15000)
            page.wait_for_timeout(1500)
            if blocked_navigation: return Page(page.url, error='unsafe_url')
            return Page(page.url, response.status if response else None, page.content()[:max_bytes], content_type='text/html')
        except TimeoutError:
            return Page(page.url or url, error='timeout')
        except Error as exc:
            text = str(exc)
            error = ('unsafe_url' if blocked_navigation else 'tls_error' if 'ERR_CERT' in text
                     else 'dns_error' if 'ERR_NAME_NOT_RESOLVED' in text
                     else 'connection_error' if 'ERR_CONNECTION' in text else 'request_error')
            return Page(page.url if page.url.startswith(('https://', 'http://')) else url, error=error)
        finally:
            try: context.close()
            except Error: pass
