import json
import os
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from store_finder.local_test import local_test, save_session, usernames
from store_finder.browser import chrome_endpoint, local_cdp_endpoint, connection_error_detail, BrowserProfiles


class LocalTestTests(unittest.TestCase):
    def test_connection_error_preserves_cause_without_debugger_id(self):
        detail = connection_error_detail(Exception('Unexpected status 404 at ws://127.0.0.1:9222/devtools/browser/private-id\nCall log: verbose'))
        self.assertIn('Unexpected status 404', detail)
        self.assertNotIn('private-id', detail)
        self.assertNotIn('verbose', detail)

    def test_direct_endpoint_uses_loopback_without_profile_discovery(self):
        self.assertEqual(local_cdp_endpoint('http://localhost:9222'), 'http://127.0.0.1:9222')
        self.assertEqual(local_cdp_endpoint('http://127.0.0.1:9222'), 'http://127.0.0.1:9222')
        profile = BrowserProfiles(cdp_endpoint='http://127.0.0.1:9222')
        self.assertTrue(profile.attach_chrome)
        for endpoint in ['http://other.test:9222', 'http://127.0.0.1:0', 'http://user:pass@127.0.0.1:9222', 'http://127.0.0.1:9222/other', 'ws://127.0.0.1:9222/other']:
            with self.assertRaises(ValueError): local_cdp_endpoint(endpoint)

    def test_existing_chrome_endpoint_is_loopback_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'DevToolsActivePort'
            path.write_text('12345\n/devtools/browser/test-id\n')
            self.assertEqual(chrome_endpoint(directory), 'ws://127.0.0.1:12345/devtools/browser/test-id')
            for text in ['0\n/devtools/browser/test', '65536\n/devtools/browser/test', '12345\nhttps://other.test', '12345\n/devtools/browser/test?x=1']:
                path.write_text(text)
                with self.assertRaises(ValueError): chrome_endpoint(directory)

    def test_attached_exit_does_not_close_users_browser(self):
        from unittest.mock import Mock
        browser = BrowserProfiles(attach_chrome=True)
        browser.browser = Mock(); browser.runtime = Mock()
        browser.__exit__(None, None, None)
        browser.browser.close.assert_not_called()
        browser.runtime.stop.assert_called_once()

    def test_chrome_mode_does_not_require_a_session_file(self):
        from unittest.mock import Mock
        context = Mock()
        context.__enter__ = Mock(return_value=context)
        context.__exit__ = Mock(return_value=None)
        def fetch(shop): shop.update(profile_status='restricted', browser_status='restricted')
        context.fetch = fetch
        with tempfile.TemporaryDirectory() as directory, patch('store_finder.local_test.BrowserProfiles', return_value=context) as constructor, patch('store_finder.local_test.print'):
            path = Path(directory)
            self.assertEqual(local_test(['example_shop'], path/'missing.json', path/'report.json', attach_chrome=True), 0)
            self.assertIsNone(constructor.call_args.kwargs['storage_state'])
            self.assertTrue(constructor.call_args.kwargs['attach_chrome'])
            self.assertEqual(json.loads((path/'report.json').read_text())['summary']['mode'], 'existing_chrome')

    def test_cli_local_commands_can_start(self):
        for command in ['login', 'local-test']:
            result = subprocess.run([sys.executable, '-m', 'store_finder', command, '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(command, result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-m', 'store_finder', 'local-test', '--accounts', 'example_shop', '--session', str(Path(directory)/'missing.json')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('Сначала выполните', result.stdout)

    def test_accounts_are_profiles_and_budget_is_bounded(self):
        self.assertEqual(usernames(['@example_shop', 'https://www.instagram.com/example_shop/']), ['example_shop'])
        for inputs in [['https://www.instagram.com/p/post/'], ['https://other.test/shop/'], [f'shop{i}' for i in range(11)]]:
            with self.assertRaises(ValueError): usernames(inputs)

    def test_session_file_is_private_and_replaced(self):
        class Context:
            def storage_state(self): return {'cookies': [], 'origins': []}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'auth/session.json'
            save_session(Context(), target)
            save_session(Context(), target)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(target.read_text()), Context().storage_state())
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_local_run_uses_session_stops_on_denial_and_exports_only_profiles(self):
        class Browser:
            def __init__(self, **kwargs): self.kwargs = kwargs
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def fetch(self, lead): lead.update(browser_status='restricted', profile_status='restricted')
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)/'session.json'; session.write_text('{"cookies": ["SECRET"]}')
            output = Path(directory)/'result.json'
            with patch('store_finder.local_test.BrowserProfiles', Browser), patch('store_finder.local_test.print'):
                self.assertEqual(local_test(['example_shop', 'second_shop'], session, output), 0)
            report = json.loads(output.read_text())
            self.assertEqual(report['summary']['tested'], 1)
            self.assertFalse(report['summary']['sheets_written'])
            self.assertNotIn('SECRET', output.read_text())
