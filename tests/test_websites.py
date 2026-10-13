import unittest
from store_finder.network import Page
from store_finder.websites import check_site


class WebsitesTests(unittest.TestCase):
    def check(self, body, code=200):
        class Fetcher:
            def get(self, *args, **kwargs):
                return Page('https://example.com/', code, body)
        return check_site('https://example.com/', Fetcher())['status']

    def test_store_with_captcha_in_form_is_working(self):
        body = '''<title>Jewelry Shop</title><main>Necklaces $125 Add to cart</main>
        <form>Contact us<div class="g-recaptcha"></div></form>
        <script>const message = "verify you are human";</script>'''
        self.assertEqual(self.check(body), 'working')

    def test_captcha_script_template_and_comment_are_not_challenges(self):
        body = '''<title>Shop</title><main>Products and orders</main>
        <script>"cf-chl-"; "captcha-container";</script>
        <template><div class="captcha-container">verify you are human</div></template>
        <!-- verify you are human cf-chl- -->'''
        self.assertEqual(self.check(body), 'working')

    def test_real_challenges_remain_restricted(self):
        for body in ['<title>Just a moment...</title><script>challenge()</script>',
                     '<main>Verify you are human</main>',
                     '<div id="cf-chl-widget">Please wait</div>',
                     '<div class="captcha-container">Continue</div>']:
            with self.subTest(body=body):
                self.assertEqual(self.check(body), 'restricted')
        self.assertEqual(self.check('<title>Shop</title>', 403), 'restricted')

    def test_store_with_security_widget_and_catalog_is_working(self):
        body = '<title>Shop</title><main>' + ('Necklace $125 Add to cart ' * 40) + '</main><aside class="captcha-container">Contact form</aside>'
        self.assertEqual(self.check(body), 'working')
