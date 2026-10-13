import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from store_finder.apify_profiles import ApifyError, ApifyProfiles
from store_finder.cloud import attempted_today, cloud_enrich, queue
from store_finder.models import merge_result
from store_finder.observations import fresh_probe, merge_enrichment
from store_finder.profiles import apply_profile_record
from store_finder.sheets import SheetsStore, sheet_row


def shop(name='one'):
    leads = {}
    merge_result(leads, {'href': f'https://www.instagram.com/{name}/', 'title': 'Jewelry shop', 'body': 'Jewelry shop DM to order'},
                 {'query': '', 'language': 'en', 'niche': 'jewelry'}, '2026-10-01T00:00:00+00:00')
    return leads[name]


def record(name='one'):
    return {'username': name, 'followersCount': 2100, 'externalUrl': '', 'private': False,
            'biography': 'Jewelry shop. DM to order', 'postsCount': 1,
            'latestPosts': [{'ownerUsername': name, 'timestamp': datetime.now(timezone.utc).isoformat(),
                             'url': 'https://www.instagram.com/p/example/'}]}


def observed(lead):
    result = fresh_probe(lead)
    apply_profile_record(result, record(lead['username']), 'apify')
    result.update(website_status='missing', enrichment_checked_at=datetime.now(timezone.utc).isoformat())
    return result


def response(data, status=200):
    result = Mock(status_code=status, content=b'{}')
    result.json.return_value = data
    return result


class CloudTests(unittest.TestCase):
    def test_evening_manual_test_allows_next_morning_daily_run(self):
        yesterday_evening = '2026-10-13T15:51:00+00:00'
        next_morning = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
        self.assertFalse(attempted_today(yesterday_evening, next_morning))
        self.assertTrue(attempted_today('2026-10-14T08:30:00+00:00', next_morning + timedelta(hours=4)))

    def test_daily_guard_uses_minsk_date_at_midnight(self):
        after_midnight = datetime(2026, 10, 13, 21, 5, tzinfo=timezone.utc)
        self.assertFalse(attempted_today('2026-10-13T20:59:00+00:00', after_midnight))
        self.assertTrue(attempted_today('2026-10-13T21:01:00+00:00', after_midnight))

    def test_provider_starts_one_capped_run_and_reads_its_dataset(self):
        provider = ApifyProfiles('SECRET')
        provider.session = Mock()
        provider.session.request.side_effect = [response({'data': {'plan': {'tier': 'FREE', 'monthlyBasePriceUsd': 0}, 'isPaying': False}}),
            response({'data': {'id': 'run123', 'status': 'RUNNING'}}),
            response({'data': {'status': 'SUCCEEDED', 'defaultDatasetId': 'dataset123', 'usageTotalUsd': 0.0052}}),
            response([record()])]
        with patch('store_finder.apify_profiles.time.sleep'):
            items, summary = provider.fetch(['one'])
        calls = provider.session.request.call_args_list
        self.assertEqual([c.args[0] for c in calls], ['GET', 'POST', 'GET', 'GET'])
        self.assertEqual(calls[1].kwargs['params']['maxTotalChargeUsd'], 0.10)
        self.assertEqual(calls[1].kwargs['json'], {'usernames': ['one'], 'includeAboutSection': False})
        self.assertFalse(calls[1].kwargs['allow_redirects'])
        self.assertNotIn('SECRET', str(calls))
        self.assertEqual(summary['usage_usd'], 0.0052)
        self.assertEqual(items[0]['username'], 'one')

    def test_billable_start_is_not_retried_and_remote_errors_are_redacted(self):
        provider = ApifyProfiles('SECRET'); provider.session = Mock()
        provider.session.request.side_effect = [response({'data': {'plan': {'tier': 'FREE', 'monthlyBasePriceUsd': 0}, 'isPaying': False}}), requests.Timeout('SECRET')]
        with self.assertRaises(ApifyError) as error: provider.fetch(['one'])
        self.assertNotIn('SECRET', str(error.exception))
        self.assertEqual(provider.session.request.call_count, 2)

    def test_paid_or_unconfirmed_plan_never_starts_an_actor(self):
        for plan, paying in [({'tier': 'STARTER', 'monthlyBasePriceUsd': 19}, True), ({}, False),
                             ({'tier': 'FREE', 'monthlyBasePriceUsd': 0}, True)]:
            provider = ApifyProfiles('TOKEN'); provider.session = Mock()
            provider.session.request.return_value = response({'data': {'plan': plan, 'isPaying': paying}})
            with self.assertRaises(ApifyError): provider.fetch(['one'])
            self.assertEqual(provider.session.request.call_count, 1)
            self.assertEqual(provider.session.request.call_args.args[0], 'GET')

    def test_charge_cap_and_inputs_are_bounded(self):
        for cap in [0, -1, float('nan'), float('inf'), 0.11]:
            with self.assertRaises(ValueError): ApifyProfiles('TOKEN', max_charge=cap)
        provider = ApifyProfiles('TOKEN')
        for names in [[], ['one', 'one'], ['https://other.test/'], ['one'] * 21]:
            with self.assertRaises(ValueError): provider.fetch(names)
        with self.assertRaises(ApifyError): provider.identifier('../other')

    def test_queue_skips_recent_cloud_results_and_manual_exclusions(self):
        complete = observed(shop('fresh'))
        excluded = shop('manual'); excluded['review_status'] = 'Не подходит'
        pending = shop('pending')
        state = {'leads': {p['username']: p for p in [complete, excluded, pending]}}
        self.assertEqual([p['username'] for p in queue(state, 5)], ['pending'])

    def test_partial_result_preserves_history_without_false_qualification(self):
        old = observed(shop())
        old['last_post_at'] = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()
        probe = fresh_probe(old)
        result = merge_enrichment(old, probe)
        self.assertEqual(result['followers'], 2100)
        self.assertEqual(result['last_post_at'], old['last_post_at'])
        self.assertFalse(result['activity_complete'])
        self.assertFalse(result['links_complete'])
        self.assertEqual(result['qualification'], 'review')

    def test_confirmed_link_removal_clears_old_website_details(self):
        old = shop(); old.update(website_urls=['https://example.com'], website_http_status=404, website_final_url='https://example.com/')
        result = merge_enrichment(old, observed(old))
        self.assertEqual(result['website_urls'], [])
        self.assertIsNone(result['website_http_status'])
        self.assertIsNone(result['website_final_url'])
        self.assertEqual(result['qualification'], 'suitable')

    def test_sorted_sheet_updates_only_tested_automatic_cells(self):
        one, two = shop(), shop('two')
        store = SheetsStore.__new__(SheetsStore); store.sheet = Mock(); store.meta = Mock()
        row_one = sheet_row(one); row_one[16:20] = ['Написал', 'CONTACT', 'DEMO', 'NOTE']
        def load():
            store.rows = [sheet_row(two), row_one]
            return {'leads': {'one': one, 'two': two}}
        store.load = load
        self.assertEqual(store.save_enrichment([observed(one)]), {'updated': 1, 'skipped': 0})
        updates = store.sheet.batch_update.call_args.args[0]
        self.assertEqual([u['range'] for u in updates], ['C3:C3', 'E3:F3', 'H3:I3', 'P3:P3', 'U3:AS3'])
        self.assertNotIn('CONTACT', str(updates))
        store.sheet.append_rows.assert_not_called(); store.meta.update.assert_not_called()

    def test_removed_or_now_excluded_row_is_not_recreated(self):
        one = shop(); excluded = shop('two'); excluded['review_status'] = 'Купил'
        store = SheetsStore.__new__(SheetsStore); store.sheet = Mock()
        store.load = lambda: {'leads': {'two': excluded}}
        store.rows = [sheet_row(excluded)]
        self.assertEqual(store.save_enrichment([observed(one), observed(excluded)]), {'updated': 0, 'skipped': 2})
        store.sheet.batch_update.assert_not_called()

    def test_daily_guard_does_not_start_another_actor(self):
        store = Mock(); store.cloud_last_attempt.return_value = datetime.now(timezone.utc).isoformat()
        provider = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APIFY_API_TOKEN': 'SECRET', 'GOOGLE_SERVICE_ACCOUNT_JSON': '{}'}), \
             patch('store_finder.cloud.SheetsStore', return_value=store), patch('store_finder.cloud.ApifyProfiles', return_value=provider), patch('store_finder.cloud.print'):
            output = Path(directory)/'summary.json'
            self.assertEqual(cloud_enrich(output=output), 0)
            report = json.loads(output.read_text())
        self.assertEqual(report['status'], 'daily_run_already_attempted')
        provider.fetch.assert_not_called(); store.mark_cloud_attempt.assert_not_called()

    def test_cloud_flow_filters_foreign_results_and_saves_profiles_without_browser_login(self):
        store = Mock(); store.cloud_last_attempt.return_value = ''
        state = {'leads': {name: shop(name) for name in ['one', 'two']}}
        store.load.return_value = state; store.save_enrichment.return_value = {'updated': 1, 'skipped': 0}
        provider = Mock(); provider.fetch.return_value = ([record('one'), record('foreign')], {'status': 'SUCCEEDED'})
        provider.fetch.side_effect = lambda names: (self.assertTrue(store.mark_cloud_attempt.called) or [record('one'), record('foreign')], {'status': 'SUCCEEDED'})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APIFY_API_TOKEN': 'SECRET', 'GOOGLE_SERVICE_ACCOUNT_JSON': '{}'}), \
             patch('store_finder.cloud.SheetsStore', return_value=store), patch('store_finder.cloud.ApifyProfiles', return_value=provider), \
             patch('store_finder.cloud.print'), patch('store_finder.cloud.BrowserProfiles') as browser:
            output = Path(directory)/'summary.json'
            self.assertEqual(cloud_enrich(limit=2, output=output), 2)
            report = json.loads(output.read_text())
        self.assertEqual(report['updated'], 2)
        self.assertEqual(report['missing_results'], 1)
        self.assertEqual(report['foreign_results'], 1)
        self.assertEqual(store.save_enrichment.call_count, 2)
        browser.assert_not_called()
        self.assertNotIn('SECRET', json.dumps(report))
        self.assertNotIn('Jewelry', json.dumps(report))

    def test_empty_queue_is_free(self):
        store = Mock(); store.cloud_last_attempt.return_value = ''; store.load.return_value = {'leads': {}}
        provider = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APIFY_API_TOKEN': 'SECRET', 'GOOGLE_SERVICE_ACCOUNT_JSON': '{}'}), \
             patch('store_finder.cloud.SheetsStore', return_value=store), patch('store_finder.cloud.ApifyProfiles', return_value=provider), patch('store_finder.cloud.print'):
            self.assertEqual(cloud_enrich(output=Path(directory)/'summary.json'), 0)
        provider.fetch.assert_not_called(); store.mark_cloud_attempt.assert_not_called()

    def test_failure_retains_daily_guard_and_redacts_public_report(self):
        store = Mock(); store.cloud_last_attempt.return_value = ''; store.load.return_value = {'leads': {'one': shop()}}
        provider = Mock(); provider.fetch.side_effect = ApifyError('Apify HTTP 402')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APIFY_API_TOKEN': 'SECRET', 'GOOGLE_SERVICE_ACCOUNT_JSON': '{}'}), \
             patch('store_finder.cloud.SheetsStore', return_value=store), patch('store_finder.cloud.ApifyProfiles', return_value=provider), patch('store_finder.cloud.print'):
            output = Path(directory)/'summary.json'
            with self.assertRaises(ApifyError): cloud_enrich(output=output)
            report = json.loads(output.read_text())
        store.mark_cloud_attempt.assert_called_once()
        self.assertEqual(report['status'], 'error')
        self.assertFalse(report['sheets_written'])
        self.assertNotIn('one', json.dumps(report))
