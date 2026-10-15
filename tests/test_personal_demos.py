import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from store_finder.demo_media import apply_demo_payload, apply_demo_html, image_url
from store_finder.personal_demos import make_bundle, publish, contact_draft, save, locked, PlatformAPI, mark_sheet_ready

IMAGE = 'https://scontent.cdninstagram.com/product.jpg?token=temporary'
JPEG = b'\xff\xd8\xff' + b'\x00' * 16


def photo(code='PHOTO', **overrides):
    return {'code': code, 'user': {'username': 'shop'}, 'media_type': 1,
            'taken_at': 1760000000, 'caption': {'text': 'Silver ring\nHandmade jewelry'},
            'image_versions2': {'candidates': [{'url': IMAGE, 'width': 1080}]}, **overrides}


class FakeAPI:
    origin = 'https://demo.example'

    def __init__(self):
        self.store = None
        self.products = []
        self.creates = self.product_creates = self.uploads = 0
        self.lose_create_response = False
        self.lose_product_response = False

    def request(self, path, method='GET', data=None, **kwargs):
        if path == '/api/platform/config':
            return {'features': {'priceOnRequest': True}}
        if path == '/api/platform/stores':
            self.creates += 1
            self.store = {**copy.deepcopy(data), 'id': 'abc', 'version': 1}
            if self.lose_create_response:
                self.lose_create_response = False
                raise ValueError('network failure after successful creation')
            return {'id': 'abc', 'slug': data['slug'], 'url': self.origin + '/s/' + data['slug']}
        if path.startswith('/api/stores/'):
            return {'store': copy.deepcopy(self.store), 'products': copy.deepcopy(self.products)} if self.store else None
        if path.endswith('/images'):
            self.uploads += 1
            return {'url': '/media/abc/image.jpg'}
        if path.endswith('/settings'):
            self.store['settings'] = copy.deepcopy(data['settings'])
            self.store['version'] += 1
            return {'ok': True}
        if path.endswith('/products'):
            if method == 'POST':
                self.product_creates += 1
                self.products.append({**copy.deepcopy(data), 'id': 'p1', 'version': 1})
                if self.lose_product_response:
                    self.lose_product_response = False
                    raise ValueError('network failure after product insert')
                return {'id': 'p1'}
            return copy.deepcopy(self.products)
        return copy.deepcopy(self.store)


class PersonalDemosTests(unittest.TestCase):
    def test_photos_exclude_video_reels_stories_unknown_and_foreign_owners(self):
        lead = {'username': 'shop'}
        apply_demo_payload(lead, {'items': [photo(), photo('VIDEO', media_type=2),
            photo('REEL', product_type='clips'), photo('STORY', expiring_at=1800000000),
            photo('FOREIGN', user={'username': 'someone'}),
            photo('UNKNOWN', media_type=None), photo('POSTED_REEL', url='https://www.instagram.com/reel/REEL/')]})
        self.assertEqual([p['code'] for p in lead['photo_posts']], ['PHOTO'])
        self.assertEqual(lead['photo_posts'][0]['caption'], 'Silver ring\nHandmade jewelry')

    def test_mixed_carousel_retains_only_confirmed_photo_children(self):
        lead = {'username': 'shop'}
        apply_demo_payload(lead, photo('ALBUM', media_type=8, carousel_media=[
            photo('ONE'), photo('TWO', media_type=2), {'display_url': IMAGE}]))
        self.assertEqual(lead['photo_posts'][0]['images'], [IMAGE])
        self.assertEqual(lead['photo_posts'][0]['kind'], 'carousel')

    def test_profile_html_preserves_identity_logo_links_and_owned_timeline(self):
        lead = {'username': 'shop'}
        p = photo()
        p.pop('user')
        payload = {'user': {'username': 'shop', 'full_name': 'Ring Shop', 'biography': 'Handmade rings',
            'profile_pic_url_hd': IMAGE, 'external_url': 'https://example.com',
            'edge_owner_to_timeline_media': {'edges': [{'node': p}]}},
            'suggested_users': [{'username': 'other', 'profile_pic_url_hd': IMAGE + 'other'}]}
        apply_demo_html(lead, '<script type="application/json">' + json.dumps(payload) + '</script>')
        self.assertEqual(lead['logo_url'], IMAGE)
        self.assertEqual(lead['title'], 'Ring Shop')
        self.assertEqual(lead['biography'], 'Handmade rings')
        self.assertEqual(lead['website_urls'], ['https://example.com'])
        self.assertEqual(len(lead['photo_posts']), 1)

    def test_profile_ownership_does_not_leak_into_suggestions_or_foreign_posts(self):
        lead = {'username': 'shop'}
        p = photo(); p.pop('user')
        apply_demo_payload(lead, {'username': 'shop', 'suggestions': [p],
            'edge_owner_to_timeline_media': {'edges': [{'node': photo(user={'username': 'another'})}]}})
        self.assertFalse(lead.get('photo_posts'))

    def test_latest_order_dedup_and_captions_are_not_truncated_to_activity_snippets(self):
        lead = {'username': 'shop'}
        apply_demo_payload(lead, {'items': [photo('OLD', taken_at=1700000000), photo('NEW', caption='A' * 2200), photo('NEW', caption='')]})
        self.assertEqual([p['code'] for p in lead['photo_posts']], ['NEW', 'OLD'])
        self.assertEqual(len(lead['photo_posts'][0]['caption']), 2200)

    def test_cdn_validation_rejects_credentials_and_similar_domains(self):
        for url in ['https://cdninstagram.com.evil.com/a.jpg', 'https://u:p@scontent.cdninstagram.com/a.jpg',
                    'http://scontent.cdninstagram.com/a.jpg', 'https://127.0.0.1/a.jpg', 'https://scontent.cdninstagram.com:8443/a.jpg']:
            self.assertFalse(image_url(url))
        self.assertEqual(image_url(IMAGE), IMAGE)
        with self.assertRaises(ValueError):
            PlatformAPI('https://demo.example/path', 'a' * 32)

    def bundle(self, directory):
        import hashlib
        assets = directory / 'assets'; assets.mkdir()
        (assets / 'photo.jpg').write_bytes(JPEG)
        asset = {'file': 'assets/photo.jpg', 'sha256': hashlib.sha256(JPEG).hexdigest(), 'mime': 'image/jpeg'}
        lead = {'username': 'shop', 'title': 'Ring shop', 'biography': 'Handmade rings', 'logo_asset': asset,
                'photo_posts': [{'code': 'ONE', 'date': '2026-10-01T10:00:00Z', 'url': 'https://www.instagram.com/p/ONE/',
                    'caption': 'Silver ring\nOriginal description', 'assets': [asset]}]}
        return make_bundle(lead, minimum=1)

    def test_missing_assets_caption_or_bio_prevents_publication(self):
        bundle = make_bundle({'username': 'shop', 'photo_posts': []})
        self.assertEqual(bundle['status'], 'needs_review')
        self.assertEqual(len(bundle['issues']), 4)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                publish(bundle, Path(tmp), FakeAPI())

    @patch('store_finder.personal_demos.requests.head', return_value=SimpleNamespace(status_code=200, headers={'Content-Type': 'image/jpeg'}))
    def test_publish_archives_photos_and_resumes_lost_store_and_product_responses(self, head):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            bundle, api = self.bundle(directory), FakeAPI()
            api.lose_create_response = True
            with self.assertRaises(ValueError):
                publish(bundle, directory, api)
            api.lose_product_response = True
            with self.assertRaises(ValueError):
                publish(bundle, directory, api)
            state = publish(bundle, directory, api)
            self.assertEqual(state['status'], 'ready')
            self.assertEqual((api.creates, api.product_creates, api.uploads), (1, 1, 1))
            self.assertEqual(api.products[0]['photos'], ['/media/abc/image.jpg'])
            self.assertTrue(api.products[0]['priceOnRequest'])
            self.assertEqual(publish(bundle, directory, api), state)
            self.assertIn(state['store']['url'], contact_draft(bundle, state['store']['url']))

    def test_existing_untracked_store_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, api = self.bundle(Path(tmp)), FakeAPI()
            api.store = {**bundle['store'], 'id': 'abc', 'version': 1}
            with self.assertRaisesRegex(ValueError, 'занят'):
                publish(bundle, Path(tmp), api)
            self.assertEqual(api.uploads, 0)

    @patch('store_finder.personal_demos.requests.head', return_value=SimpleNamespace(status_code=200, headers={'Content-Type': 'image/jpeg'}))
    def test_old_api_cannot_mark_demo_ready_without_price_mode(self, head):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, api = self.bundle(Path(tmp)), FakeAPI()
            original = api.request
            def old_api(path, method='GET', data=None, **kwargs):
                if method == 'POST' and path.endswith('/products'):
                    data = {**data}; data.pop('priceOnRequest')
                return original(path, method, data, **kwargs)
            api.request = old_api
            with self.assertRaisesRegex(ValueError, 'Обновите Delokay'):
                publish(bundle, Path(tmp), api)
            self.assertNotEqual(json.loads((Path(tmp) / 'publish.json').read_text()).get('status'), 'ready')

    def test_single_writer_lock_and_private_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with locked(directory):
                with self.assertRaises(ValueError):
                    with locked(directory):
                        pass
            path = directory / 'bundle.json'; save(path, {'private': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_sheet_update_preserves_contacts_comments_and_contacted_status(self):
        updates = []
        row = [''] * 20
        row[0], row[16], row[17], row[19] = 'shop', 'Новый', 'saved contact', 'saved comment'
        store = SimpleNamespace(rows=[row], load=lambda: None,
            sheet=SimpleNamespace(batch_update=lambda data, **kwargs: updates.extend(data)))
        mark_sheet_ready(store, 'shop', 'https://demo.example/s/shop')
        self.assertEqual([u['range'] for u in updates], ['Q2', 'S2'])
        row[16] = 'Написал'
        with self.assertRaises(ValueError):
            mark_sheet_ready(store, 'shop', 'https://demo.example/s/shop')


if __name__ == '__main__':
    unittest.main()
