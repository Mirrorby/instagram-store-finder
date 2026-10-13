import unittest
from datetime import datetime, timedelta, timezone
from store_finder.browser import apply_payload, decode_payloads
from store_finder.post_dates import apply_media_payload, parse_post_document, post_url, remember_post


class PostDatesTests(unittest.TestCase):
    def test_modern_media_response_uses_owned_user_and_taken_at(self):
        lead = {'username': 'shop', 'profile_id': '123'}
        date = datetime.now(timezone.utc)-timedelta(days=2)
        payload = {'data': {'xdt_api__v1__feed__user_timeline_graphql_connection': {'edges': [
            {'node': {'code': 'OWN', 'taken_at': int(date.timestamp()), 'user': {'pk': '123'}, 'caption': {'text': 'Jewelry shop'}}},
            {'node': {'code': 'FOREIGN', 'taken_at': int(datetime.now(timezone.utc).timestamp()), 'user': {'username': 'another'}}},
            {'node': {'code': 'STORY', 'taken_at': int(datetime.now(timezone.utc).timestamp()), 'expiring_at': 9999999999, 'user': {'username': 'shop'}}}
        ]}}}
        self.assertTrue(apply_media_payload(lead, payload, profile_scope=True))
        self.assertEqual(len(lead['post_samples']), 1)
        self.assertEqual(lead['latest_post_url'], 'https://www.instagram.com/p/OWN/')
        self.assertEqual(lead['last_post_at'], date.isoformat(timespec='seconds'))
        self.assertEqual(lead['recent_captions'], ['Jewelry shop'])

    def test_profile_identity_can_match_media_with_id_only(self):
        lead = {'username': 'shop'}
        payload = {'data': {'user': {'username': 'shop', 'id': '123', 'follower_count': 20, 'external_url': None}}}
        self.assertTrue(apply_payload(lead, payload))
        self.assertEqual(lead['profile_id'], '123')
        self.assertTrue(apply_media_payload(lead, {'items': [{'user': {'pk': 123}, 'code': 'CODE', 'taken_at': 1750000000}]}))

    def test_publication_date_precedes_comment_date_and_requires_author(self):
        lead = {'username': 'shop'}
        html = '<meta property="og:title" content="Instagram">'
        dates = ['2026-01-01T12:00:00Z', '2026-10-01T12:00:00Z']
        self.assertFalse(parse_post_document(lead, 'https://www.instagram.com/p/CODE/', html, ['https://www.instagram.com/another/'], dates))
        self.assertTrue(parse_post_document(lead, 'https://www.instagram.com/p/CODE/', html, ['https://www.instagram.com/shop/'], dates))
        self.assertEqual(lead['last_post_at'], '2026-01-01T12:00:00+00:00')

    def test_comment_before_publication_footer_is_not_post_date(self):
        lead = {'username': 'shop'}
        times = [{'date': '2026-10-01T12:00:00Z', 'href': 'https://www.instagram.com/p/CODE/c/comment/'},
                 {'date': '2026-01-01T12:00:00Z', 'href': 'https://www.instagram.com/p/CODE/'}]
        self.assertTrue(parse_post_document(lead, 'https://www.instagram.com/p/CODE/', '', ['https://www.instagram.com/shop/'], times))
        self.assertEqual(lead['last_post_at'], '2026-01-01T12:00:00+00:00')

    def test_pinned_order_and_duplicate_reel_do_not_change_latest(self):
        lead = {'username': 'shop'}
        remember_post(lead, 'https://www.instagram.com/p/NEW/', '2026-10-01T12:00:00Z', 'chromium')
        remember_post(lead, 'https://www.instagram.com/reel/NEW/', '2026-10-01T12:00:00Z', 'chromium_post_page')
        remember_post(lead, 'https://www.instagram.com/p/PINNED/', '2024-01-01T12:00:00Z', 'chromium_post_page')
        self.assertEqual(len(lead['post_samples']), 2)
        self.assertEqual(lead['last_post_at'], '2026-10-01T12:00:00+00:00')
        self.assertEqual(post_url('https://www.instagram.com/shop/p/NEW/?img_index=1', 'shop'), 'https://www.instagram.com/p/NEW/')
        self.assertIsNone(post_url('https://www.instagram.com/another/p/NEW/', 'shop'))

    def test_xssi_and_multiple_json_chunks_are_read_as_data(self):
        self.assertEqual(decode_payloads(b'for (;;);{"data":{"user":1}}\n{"data":{"media":2}}'), [{'data': {'user': 1}}, {'data': {'media': 2}}])
        self.assertEqual(decode_payloads('<html>Sign in</html>'), [])
