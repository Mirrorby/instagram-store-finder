import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from store_finder.browser import apply_payload
from store_finder.network import Page, public_url
from store_finder.profiles import parse_profile_html, apply_profile_record, apply_indexed_data
from store_finder.qualification import qualify
from store_finder.scoring import score
from store_finder.sheets import sheet_row, lead_from_row, plan_updates, HEADERS
from store_finder.websites import check_site, check_websites, links_in_bio
from test_finder import QUERY, row
from store_finder.models import merge_result


def lead():
    leads = {}
    merge_result(leads, row(), QUERY, '2026-10-12T18:00:00+00:00')
    return leads['example_shop']


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path('config/qualification.json').read_text())

    def test_metadata_is_partial_and_count_can_be_rounded(self):
        shop = lead()
        text = '<meta property="og:title" content="Example (@example_shop)"><meta property="og:description" content="13K Followers, 25 Posts">'
        self.assertTrue(parse_profile_html(shop, text))
        self.assertEqual(shop['followers'], 13000)
        self.assertTrue(shop['followers_approximate'])
        self.assertEqual(shop['profile_status'], 'partial')
        check_websites(shop, None)
        self.assertEqual(shop['website_status'], 'unknown')

    def test_json_owner_latest_date_and_links(self):
        shop = lead()
        current = datetime.now(timezone.utc)
        posts = [{'ownerUsername': 'example_shop', 'timestamp': (current-timedelta(days=n)).isoformat(), 'url': f'https://www.instagram.com/p/test{n}/'} for n in [800, 20, 5, 1]]
        posts.append({'ownerUsername': 'another', 'timestamp': current.isoformat()})
        record = {'username': 'example_shop', 'follower_count': 4321, 'external_url': 'https://shop.example/', 'biography': 'Jewelry shop. DM to order.', 'latestPosts': posts}
        self.assertTrue(apply_payload(shop, {'data': {'user': record}}))
        self.assertEqual(shop['followers'], 4321)
        self.assertEqual(shop['recent_posts_sample'], 3)
        self.assertEqual(shop['last_post_at'], (current-timedelta(days=1)).isoformat(timespec='seconds'))
        self.assertTrue(shop['links_complete'])
        self.assertTrue(shop['activity_complete'])
        self.assertFalse(apply_profile_record(shop, {**record, 'username': 'another'}, 'chromium'))

    def test_pinned_only_old_posts_do_not_prove_inactive(self):
        shop = lead()
        old = (datetime.now(timezone.utc)-timedelta(days=500)).isoformat()
        apply_profile_record(shop, {'username': 'example_shop', 'latestPosts': [{'timestamp': old}]*3, 'postsCount': 300}, 'chromium')
        score(shop); qualify(shop, self.config)
        self.assertEqual(shop['activity_status'], 'unknown')

    def test_website_failures_and_access_denial(self):
        class Fake:
            def __init__(self, page): self.page = page
            def get(self, *args, **kwargs): return self.page
        cases = [(404, 'Not found', 'broken'), (403, 'Forbidden', 'restricted'), (503, 'Unavailable', 'temporary'), (200, '<title>404 - Site not found</title>', 'empty'), (200, 'verify you are human', 'restricted'), (200, '<title>Shop</title>Products and orders', 'working')]
        for code, body, expected in cases:
            with self.subTest(code=code, body=body):
                self.assertEqual(check_site('https://example.com', Fake(Page('https://example.com', code, body)))['status'], expected)
        self.assertFalse(public_url('http://127.0.0.1/'))
        self.assertFalse(public_url('http://169.254.169.254/'))
        self.assertFalse(public_url('http://localhost/'))

    def test_email_and_partial_domain_not_websites(self):
        self.assertEqual(links_in_bio('Email info@thejewelryshop.com or user@gmail.com'), [])
        self.assertEqual(links_in_bio('Store shop.example.com.ua'), ['https://shop.example.com.ua'])
        self.assertEqual(links_in_bio('Visit cataphora.eu'), ['https://cataphora.eu'])

    def test_broken_site_positive_working_site_negative_and_roundtrip(self):
        shop = lead()
        recent = datetime.now(timezone.utc).isoformat(timespec='seconds')
        apply_profile_record(shop, {'username': 'example_shop', 'followersCount': 2116, 'externalUrl': 'https://example.com', 'private': False, 'biography': 'Jewelry shop. DM to order.', 'latestPosts': [{'timestamp': recent}], 'postsCount': 1}, 'chromium')
        shop.update(website_status='broken', enrichment_checked_at=recent, browser_status='verified')
        score(shop); qualify(shop, self.config)
        self.assertEqual(shop['qualification'], 'suitable')
        self.assertIn('Сайт указан, не работает', shop['qualification_reason'])
        restored = lead_from_row(sheet_row(shop))
        self.assertEqual(restored['website_status'], 'broken')
        self.assertEqual(restored['followers'], 2116)
        self.assertEqual(restored['last_post_at'], recent)
        self.assertEqual(restored['links_complete'], True)
        updates, _ = plan_updates([sheet_row(shop)], {'example_shop': shop})
        self.assertEqual([u['range'] for u in updates], ['A2:P2', 'U2:AS2'])
        self.assertEqual(len(HEADERS), 45)
        shop['website_status'] = 'working'
        score(shop); qualify(shop, self.config)
        self.assertEqual(shop['qualification'], 'rejected')

    def test_service_store_name_is_not_product_shop(self):
        shop = lead()
        shop['biography'] = 'An innovative hair salon in Guernsey'
        shop['title'] = 'Bijoux Boutique'
        shop['profile_status'] = 'verified'
        self.assertEqual(score(shop)['classification'], 'needs_review')

    def test_foreign_count_in_merged_card_is_ignored(self):
        shop = lead()
        shop['evidence'][0]['snippet'] = 'Example (@example_shop) accessories.453 Followers - Other (@other)'
        apply_indexed_data(shop)
        self.assertIsNone(shop['followers'])

    def test_restricted_profiles_are_not_counted_as_checked(self):
        from store_finder.enrichment import enrich
        state = {'leads': {'example_shop': lead(), 'second': {**lead(), 'username': 'second'}}}
        class Browser:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def fetch(self, shop): shop['profile_status'] = 'restricted'
        def denied(shop, fetcher): shop['profile_status'] = 'restricted'
        with patch('store_finder.enrichment.BrowserProfiles', Browser), patch('store_finder.enrichment.fetch_profile', denied), patch('store_finder.enrichment.time.sleep'):
            stats = enrich(state, None, budget=2)
        self.assertEqual(stats['checked'], 1)
        self.assertEqual(stats['processed'], 2)
        self.assertEqual(stats['skipped_after_restriction'], 1)

    def test_post_rate_limit_stops_remaining_enrichment_queue(self):
        from store_finder.enrichment import enrich
        state = {'leads': {'example_shop': lead(), 'second': {**lead(), 'username': 'second'}}}
        class Browser:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def fetch(self, shop): shop.update(profile_status='verified', instagram_rate_limited=True)
        with patch('store_finder.enrichment.BrowserProfiles', Browser):
            stats = enrich(state, None, budget=2)
        self.assertEqual(stats['checked'], 1)
        self.assertTrue(stats['stopped_on_instagram_restriction'])
