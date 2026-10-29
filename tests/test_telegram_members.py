import unittest
import os
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from store_finder.telegram_members import group_reference, tab_title, plan_rows, HEADERS, sync_sheet, MARKER, settings, main


class TelegramMembersTests(unittest.TestCase):
    def test_actions_missing_secrets_fail_without_prompts_or_auth_files(self):
        args = SimpleNamespace(non_interactive=True, spreadsheet=None)
        with patch.dict(os.environ, {}, clear=True), patch('builtins.input', side_effect=AssertionError('No input')):
            with self.assertRaisesRegex(ValueError, 'TELEGRAM_SESSION'):
                settings(args)

    def test_actions_config_uses_env_without_auth_files(self):
        args = SimpleNamespace(non_interactive=True, spreadsheet='explicit-book')
        env = {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32,
               'TELEGRAM_SESSION': 'secret-session', 'GOOGLE_SERVICE_ACCOUNT_JSON': '{}'}
        with patch.dict(os.environ, env, clear=True), patch('pathlib.Path.mkdir', side_effect=AssertionError('No local state')):
            self.assertEqual(settings(args), (12345, 'a' * 32, None, 'explicit-book'))

    def test_workflow_group_input_is_data_not_shell(self):
        captured = []
        async def fake_run(args):
            captured.extend(args.groups)
            return 0
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'TELEGRAM_GROUPS': '@yardsalemd,\nhttps://t.me/example'}, clear=True), \
                patch('sys.argv', ['collector']), patch('store_finder.telegram_members.run', fake_run):
            self.assertEqual(main(), 0)
        self.assertEqual(captured, ['@yardsalemd', 'https://t.me/example'])

    def test_login_refuses_actions_before_loading_telethon(self):
        from store_finder.telegram_login import main as login_main
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            self.assertEqual(login_main(), 2)

    def test_group_links(self):
        for source in ['@yardsalemd', 'https://t.me/yardsalemd', 't.me/yardsalemd/']:
            self.assertEqual(group_reference(source), '@yardsalemd')
        self.assertEqual(group_reference('https://t.me/+abc_DEF-123'), 'https://t.me/+abc_DEF-123')
        for source in ['https://evil.test/yardsalemd', 'https://t.me/yardsalemd/123', 'https://t.me/c/123/5', 'https://t.me/share/url']:
            with self.assertRaises(ValueError):
                group_reference(source)

    def test_sheet_names_collisions_and_sanitization(self):
        self.assertEqual(tab_title('YardSale', -100123, []), 'YardSale')
        self.assertEqual(tab_title('YardSale', -100123, ['yardsale']), 'YardSale · -100123')
        title = tab_title('a' * 110 + '[]', -100123, ['a' * 100])
        self.assertLessEqual(len(title), 100)
        self.assertNotIn('[', title)
        self.assertTrue(title.endswith(' · -100123'))

    def test_repeat_preserves_operator_fields_and_first_seen(self):
        members = [{'id': 42, 'name': '=IMPORTXML("evil")', 'username': 'old_name'}, {'id': 43, 'name': 'No username'}]
        updates, updated, added = plan_rows([], members, -1001, 'https://t.me/test', 'first', 'partial')
        rows = updates[0]['values']
        self.assertEqual((updated, added), (0, 2))
        self.assertEqual(rows[0][12], 'Новый')
        self.assertEqual(rows[1][3], '')
        rows[0][12:] = ['Написал', 'Свечи', 'yesterday', 'tomorrow', 'demo', 'important']
        members[0]['username'] = 'new_name'
        updates, updated, added = plan_rows(rows, members + [members[0]], -1001, '', 'second', 'checked')
        self.assertEqual((updated, added), (2, 0))
        self.assertEqual(updates[0]['range'], 'A2:L2')
        self.assertEqual(len(updates[0]['values'][0]), 12)
        self.assertEqual(updates[0]['values'][0][3], 'https://t.me/new_name')
        self.assertEqual(updates[0]['values'][0][9], 'first')
        self.assertEqual(rows[0][12], 'Написал')

    def test_missing_contacts_retained_and_append_after_blanks(self):
        row = ['42', 'Name', '', '', '', '', '', '-1001', '', 'first', '', ''] + ['Отказ'] * 6
        updates, updated, added = plan_rows([row, [], ['43', '', '', '', '', '', '', '-1001']],
            [{'id': 44, 'name': 'New'}], -1001, '', 'now', 'partial')
        self.assertEqual((updated, added), (0, 1))
        self.assertEqual(updates[0]['range'], 'A5:R5')

    def test_refuse_ambiguous_existing_identity(self):
        for rows in [[['42'], ['42']], [['', 'name']], [['42', '', '', '', '', '', '', '-1002']]]:
            with self.assertRaises(ValueError):
                plan_rows(rows, [], -1001, '', 'now', 'partial')

    def test_sheet_updates_use_raw_and_never_overwrite_manual_columns(self):
        class Sheet:
            id = 5
            row_count = 5
            writes = []
            def update_title(self, title):
                self.title = title
            def get(self, range_name, **kwargs):
                if range_name == 'A1:R1':
                    return [HEADERS]
                return [['42', 'Old', '@old', '', '', '', '', '-1001', '', 'first', '', '', 'Написал', 'Свечи']]
            def batch_update(self, updates, **kwargs):
                self.writes.append((updates, kwargs))
        class Book:
            id = 'book'
            sheet = Sheet()
            def fetch_sheet_metadata(self, **kwargs):
                return {'sheets': [{'properties': {'sheetId': 5, 'title': 'Old title'},
                    'developerMetadata': [{'metadataKey': MARKER, 'metadataValue': '-1001'}]}]}
            def get_worksheet_by_id(self, sheet_id):
                return self.sheet
            def batch_update(self, body):
                self.formatting = body
        book = Book()
        result = sync_sheet(book, -1001, 'New title', '', [{'id': 42, 'name': '=formula', 'username': 'new'}], 'now', 'partial')
        self.assertEqual(result['updated'], 1)
        self.assertEqual(book.sheet.title, 'New title')
        writes, options = book.sheet.writes[-1]
        self.assertEqual(options['value_input_option'], 'RAW')
        self.assertEqual(writes[0]['range'], 'A2:L2')
        self.assertEqual(writes[0]['values'][0][9], 'first')


class TelegramQrTests(unittest.IsolatedAsyncioTestCase):
    async def check_qr(self, mode):
        from store_finder.telegram_login import authorize_qr
        class PasswordNeeded(Exception):
            pass
        side_effect = {'success': None, 'timeout': asyncio.TimeoutError(), '2fa': PasswordNeeded()}[mode]
        qr = SimpleNamespace(url='tg://login?token=test', wait=AsyncMock(side_effect=side_effect))
        client = SimpleNamespace(qr_login=AsyncMock(return_value=qr), sign_in=AsyncMock())
        image = SimpleNamespace(save=lambda path: Path(path).write_bytes(b'fake-png'))
        modules = {'qrcode': SimpleNamespace(make=lambda url: image),
                   'telethon.errors': SimpleNamespace(SessionPasswordNeededError=PasswordNeeded)}
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / '.auth'
            with patch.dict('sys.modules', modules), patch('store_finder.telegram_login.Path', return_value=auth), \
                    patch('getpass.getpass', return_value='test-password'):
                if mode == 'timeout':
                    with self.assertRaisesRegex(ValueError, 'QR истёк'):
                        await authorize_qr(client)
                else:
                    await authorize_qr(client)
            self.assertFalse((auth / 'telegram-login.png').exists())
            qr.wait.assert_awaited_once_with(timeout=120)
            if mode == '2fa':
                client.sign_in.assert_awaited_once_with(password='test-password')
            else:
                client.sign_in.assert_not_awaited()

    async def test_qr_success_removes_token_image(self):
        await self.check_qr('success')

    async def test_qr_timeout_removes_token_image(self):
        await self.check_qr('timeout')

    async def test_qr_two_factor_password_and_cleanup(self):
        await self.check_qr('2fa')


if __name__ == '__main__':
    unittest.main()
