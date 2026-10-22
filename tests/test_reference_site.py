import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from store_finder.reference_site import public_url, public_addresses, fetch_public, analyze_reference
from store_finder.railway_service import normalize
from store_finder.railway_job import run


class ReferenceTests(unittest.TestCase):
    def test_only_public_https_urls(self):
        self.assertEqual(public_url('https://example.com/path#x'), 'https://example.com/path')
        for url in ['http://example.com', 'https://localhost', 'https://a.local',
                    'https://127.0.0.1', 'https://[::1]', 'https://169.254.169.254',
                    'https://user:password@example.com', 'https://example.com:8080']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)
        with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.1.2.3', 443))]):
            with self.assertRaises(ValueError):
                public_addresses('public-looking.example')

    def test_connection_pins_public_ip_and_keeps_tls_hostname(self):
        response = MagicMock(status=200, headers={'Content-Type': 'text/html'})
        response.read.return_value = b'<h1>Shop</h1>'
        with patch('store_finder.reference_site.public_addresses', return_value=['93.184.216.34']), \
             patch('store_finder.reference_site.urllib3.HTTPSConnectionPool') as pool:
            pool.return_value.request.return_value = response
            _, html = fetch_public('https://example.com/')
            self.assertIn('Shop', html)
            self.assertEqual(pool.call_args.args, ('93.184.216.34',))
            self.assertEqual(pool.call_args.kwargs['server_hostname'], 'example.com')
            self.assertEqual(pool.call_args.kwargs['cert_reqs'], 'CERT_REQUIRED')
            self.assertFalse(pool.return_value.request.call_args.kwargs['redirect'])

    def test_redirect_to_metadata_is_blocked_before_second_connection(self):
        response = MagicMock(status=302, headers={'Location': 'https://169.254.169.254/latest/'})
        with patch('store_finder.reference_site.public_addresses', return_value=['93.184.216.34']), \
             patch('store_finder.reference_site.urllib3.HTTPSConnectionPool') as pool:
            pool.return_value.request.return_value = response
            with self.assertRaises(ValueError):
                fetch_public('https://example.com/')
            self.assertEqual(pool.call_count, 1)

    def test_source_structure_and_styles_are_explicitly_not_rendered(self):
        html = '<title>Example shop</title><meta name="description" content="Clothes"><link rel="stylesheet" href="/style.css"><h1>Collection</h1><h2>Delivery</h2><p>Sample information</p><img src="/coat.jpg" alt="Coat"><script>secret script text</script>'
        with patch('store_finder.reference_site.fetch_public', side_effect=[('https://example.com/', html), ('https://example.com/style.css', 'body{color:#253047;font-family:Manrope,sans-serif}')]):
            result = analyze_reference('https://example.com/')
        self.assertEqual(result['title'], 'Example shop')
        self.assertEqual(result['headings'][1]['text'], 'Delivery')
        self.assertEqual(result['images'][0]['url'], 'https://example.com/coat.jpg')
        self.assertIn('#253047', result['colors'])
        self.assertEqual(result['analysis'], 'static_html_css')
        self.assertFalse(result['javascriptExecuted'])
        self.assertNotIn('secret script text', result['textExcerpt'])

    def test_website_job_uses_reference_collector_not_instagram(self):
        self.assertEqual(normalize({'kind': 'website', 'url': 'https://example.com/#x'})['url'], 'https://example.com/')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'request.json').write_text(json.dumps({'kind': 'website', 'url': 'https://example.com/'}))
            with patch('store_finder.reference_site.analyze_reference', return_value={'title': 'Example'}), \
                 patch('store_finder.railway_job.BrowserProfiles') as browser:
                run(directory)
            browser.assert_not_called()
            self.assertEqual(json.loads((directory / 'result.json').read_text())['status'], 'ready')

    def test_collection_only_never_publishes_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'request.json').write_text(json.dumps({'username': 'shop'}))
            (directory / 'bundle.json').write_text(json.dumps({'issues': [], 'username': 'shop', 'store': {'name': 'Shop', 'settings': {'description': 'Clothing'}, 'products': []}}))
            with patch.dict(os.environ, {'IMPORT_PUBLISH_ENABLED': '0'}), \
                 patch('store_finder.railway_job.PlatformAPI') as api:
                run(directory)
            api.assert_not_called()
            result = json.loads((directory / 'result.json').read_text())
            self.assertFalse(result['published'])
            self.assertEqual(result['profile']['username'], 'shop')
