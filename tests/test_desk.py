import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from store_finder.desk_store import DeskStore
from store_finder.desk import Desk
from store_finder.personal_demos import save
from store_finder.sheets import HEADERS


class DeskStoreTests(unittest.TestCase):
    def test_import_preserves_notes_contact_status_and_ready_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DeskStore(tmp)
            store.add('my_shop')
            store.update('my_shop', note='Личная заметка', contact_status='Написал', stage='ready', demo_url='https://example.com/demo', message='My message')
            row = [''] * len(HEADERS)
            row[0:3] = ['my_shop', 'https://www.instagram.com/my_shop/', 'My shop']
            row[5], row[16], row[19] = '55', 'Новый', 'Old sheet note'
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(HEADERS)
            writer.writerow(row)
            self.assertEqual(store.import_csv(output.getvalue())['added'], 0)
            actual = store.get('my_shop')
            self.assertEqual(actual['note'], 'Личная заметка')
            self.assertEqual(actual['contact_status'], 'Написал')
            self.assertEqual(actual['stage'], 'ready')
            self.assertEqual(actual['message'], 'My message')
            self.assertEqual(actual['demo_url'], 'https://example.com/demo')

    def test_restart_recovers_interrupted_jobs_without_losing_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DeskStore(tmp)
            store.add('@my_shop')
            store.update('my_shop', stage='checking')
            store.new_job('process')
            restored = DeskStore(tmp)
            self.assertEqual(restored.get('my_shop')['stage'], 'error')
            self.assertEqual(restored.jobs()[0]['status'], 'interrupted')
            self.assertEqual(restored.path.stat().st_mode & 0o777, 0o600)

    def test_bad_rows_and_invalid_accounts_do_not_clear_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DeskStore(tmp)
            store.add('my_shop')
            for value in ('https://evil.example/shop', 'https://instagram.com/p/POST/', 'bad name'):
                with self.assertRaises(ValueError):
                    store.add(value)
            with self.assertRaises(ValueError):
                store.import_csv('<html>Login required</html>')
            self.assertEqual(len(store.all()), 1)


class DeskTests(unittest.TestCase):
    def desk(self, tmp):
        with patch.object(DeskStore, 'scan_demos'):
            return Desk(tmp)

    def test_key_remains_only_in_memory_and_is_never_returned_or_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.desk(tmp)
            key = 'private-example-platform-key-that-is-long-enough'
            app.settings({'api_key': key})
            self.assertTrue(app.state()['has_key'])
            self.assertNotIn(key, json.dumps(app.state()))
            self.assertNotIn(key, app.store.path.read_bytes().decode(errors='ignore'))
            self.assertEqual(self.desk(tmp).api_key, '')

    def test_published_demo_uses_saved_photos_and_prepares_message_without_chrome(self):
        from tests.test_personal_demos import FakeAPI, PersonalDemosTests
        with tempfile.TemporaryDirectory() as tmp:
            app = self.desk(Path(tmp) / 'desk')
            directory = Path(tmp) / 'bundle'
            directory.mkdir()
            bundle = PersonalDemosTests().bundle(directory)
            for code in ('TWO', 'THREE'):
                bundle['source']['photo_posts'].append({**bundle['source']['photo_posts'][0], 'code': code, 'url': 'https://www.instagram.com/p/' + code + '/'})
            save(directory / 'bundle.json', bundle)
            app.store.add('shop')
            app.store.update('shop', bundle_path=str(directory))
            app.api_key = 'example-key-with-more-than-32-characters'
            job = app.store.new_job('publish')
            with patch('store_finder.personal_demos.PlatformAPI', return_value=FakeAPI()), patch('store_finder.personal_demos.requests.head', return_value=type('Response', (), {'status_code': 200, 'headers': {'Content-Type': 'image/jpeg'}})()), patch('store_finder.browser.BrowserProfiles') as browser:
                app.process(job, ['shop'], publish=True)
            browser.assert_not_called()
            row = app.store.get('shop')
            self.assertEqual(row['stage'], 'ready')
            self.assertIn('https://demo.example', row['message'])
            self.assertTrue((directory / 'first-contact.txt').exists())

    def test_instagram_restriction_stops_remaining_queue(self):
        from store_finder.demo_browser import InstagramRestricted
        with tempfile.TemporaryDirectory() as tmp:
            app = self.desk(tmp)
            app.store.add('first_shop')
            app.store.add('second_shop')
            job = app.store.new_job('process')
            with patch('store_finder.browser.BrowserProfiles'), patch('store_finder.demo_browser.collect_demo', side_effect=InstagramRestricted('Stop')):
                with self.assertRaises(InstagramRestricted):
                    app.process(job)
            self.assertEqual(app.store.get('first_shop')['stage'], 'restricted')
            self.assertEqual(app.store.get('second_shop')['stage'], 'queued')

    def test_parallel_jobs_and_keyless_publication_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.desk(tmp)
            app.store.add('shop')
            with self.assertRaisesRegex(ValueError, 'ключ'):
                app.submit('publish', ['shop'])
            app.busy.acquire()
            with self.assertRaisesRegex(ValueError, 'выполняется'):
                app.submit('sync')
            app.busy.release()
