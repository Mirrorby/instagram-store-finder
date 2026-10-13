import unittest
from unittest.mock import Mock, patch
from store_finder.network import Page
from store_finder.site_browser import BrowserSiteFetcher
from store_finder.websites import check_websites, retry_restricted_sites


class SiteBrowserTests(unittest.TestCase):
    def test_browser_can_confirm_working_page_after_http_denial(self):
        fetcher = Mock()
        fetcher.get.return_value = Page('https://example.com', 403, 'Forbidden')
        lead = {'website_urls': ['https://example.com'], 'links_complete': True}
        check_websites(lead, fetcher)
        browser = Mock(); browser.get.return_value = Page('https://example.com', 200, '<title>Shop</title>Products')
        retry_restricted_sites(lead, browser)
        self.assertEqual(lead['website_status'], 'working')
        self.assertEqual(lead['website_checks'][0]['previous_http_status'], 403)
        self.assertEqual(lead['website_http_status'], 200)

    def test_second_denial_or_timeout_does_not_make_site_broken(self):
        for page in [Page('https://example.com', 403, 'Forbidden'), Page('https://example.com', error='timeout')]:
            fetcher = Mock(); fetcher.get.return_value = Page('https://example.com', 403, 'Forbidden')
            lead = {'website_urls': ['https://example.com'], 'links_complete': True}
            check_websites(lead, fetcher)
            browser = Mock(); browser.get.return_value = page
            retry_restricted_sites(lead, browser)
            self.assertEqual(lead['website_status'], 'restricted')

    def test_browser_cannot_visit_private_urls(self):
        browser = Mock()
        page = BrowserSiteFetcher(browser).get('http://127.0.0.1/')
        self.assertEqual(page.error, 'unsafe_url')
        browser.new_context.assert_not_called()

    def test_context_failure_is_an_unknown_check_not_a_failed_profile_run(self):
        from playwright.sync_api import Error
        browser = Mock(); browser.new_context.side_effect = Error('No context')
        with patch('store_finder.site_browser.public_url', return_value=True):
            page = BrowserSiteFetcher(browser).get('https://example.com')
        self.assertEqual(page.error, 'request_error')
