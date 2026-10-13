"""Cloud profile queue, with a daily billing guard and private Sheet persistence."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
import time
from pathlib import Path
from .apify_profiles import ApifyProfiles
from .browser import BrowserProfiles
from .models import now
from .network import Fetcher
from .observations import MANUAL_EXCLUSIONS, fresh_probe
from .profiles import apply_profile_record
from .qualification import settings
from .sheets import SheetsStore, configured_sheet
from .site_browser import BrowserSiteFetcher
from .websites import check_websites, retry_restricted_sites


def queue(state, limit):
    config, current = settings(), datetime.now(timezone.utc)
    selected = []
    for lead in state['leads'].values():
        if lead.get('review_status') in MANUAL_EXCLUSIONS: continue
        from_apify = any(lead.get(key) == 'apify' for key in ['followers_source', 'website_source', 'activity_source'])
        checked = lead.get('enrichment_checked_at')
        if from_apify and checked:
            complete = (lead.get('followers') is not None and lead.get('links_complete') and
                        (lead.get('last_post_at') or lead.get('is_private')) and
                        lead.get('website_status') not in {'unknown', 'restricted', 'temporary', 'unreachable'})
            days = config['recheck_after_days'] if complete else 1
            if (current - datetime.fromisoformat(checked)).total_seconds() < days * 86400: continue
        selected.append(lead)
    selected.sort(key=lambda lead: (lead.get('enrichment_checked_at') or '', -lead.get('score', 0), lead['username']))
    return selected[:limit]


def save_summary(path, summary):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    target = os.environ.get('GITHUB_STEP_SUMMARY')
    if target:
        with open(target, 'a', encoding='utf-8') as file:
            file.write('\n### Cloud profile checks\n\n```json\n' + json.dumps(summary, indent=2) + '\n```\n')


def cloud_enrich(limit=20, output=Path('output/cloud-summary.json'), force=False, max_charge=0.10):
    if not 1 <= limit <= 20: raise ValueError('Лимит облачной партии: 1–20 аккаунтов.')
    token = os.environ.get('APIFY_API_TOKEN', '').strip()
    provider = ApifyProfiles(token, max_charge=max_charge)
    credentials = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
    if not credentials: raise ValueError('Не задан GOOGLE_SERVICE_ACCOUNT_JSON.')
    store = SheetsStore(credentials, os.environ.get('GOOGLE_SHEET_ID', '').strip() or configured_sheet())
    summary = {'selected': 0, 'returned': 0, 'updated': 0, 'skipped': 0, 'missing_results': 0,
               'foreign_results': 0, 'provider': 'apify', 'sheets_written': False}
    last = store.cloud_last_attempt()
    if last and not force and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < 20 * 3600:
        summary['status'] = 'daily_run_already_attempted'
        save_summary(output, summary)
        return 0
    selected = queue(store.load(), limit)
    summary['selected'] = len(selected)
    if not selected:
        summary['status'] = 'no_candidates'
        save_summary(output, summary)
        return 0
    provider.ensure_free()
    # Persist before the billable request. Cron retries must not create another run.
    store.mark_cloud_attempt(now())
    started = time.monotonic()
    try:
        records, provider_summary = provider.fetch([lead['username'] for lead in selected])
        summary['apify'] = provider_summary
        by_username = {}
        wanted = {lead['username'] for lead in selected}
        for record in records:
            name = str(record.get('username', '')).lower()
            if name in wanted and name not in by_username:
                by_username[name] = record
            else: summary['foreign_results'] += 1
        summary['returned'] = len(by_username)
        fetcher = Fetcher()
        with ExitStack() as stack:
            browser = None
            for index, previous in enumerate(selected):
                if time.monotonic() - started >= 480:
                    summary['time_budget_exhausted'] = True
                    break
                probe = fresh_probe(previous)
                probe['enrichment_checked_at'] = now()
                record = by_username.get(probe['username'])
                if record:
                    apply_profile_record(probe, record, 'apify')
                else:
                    summary['missing_results'] += 1
                if not probe.get('links_complete') and not probe.get('website_urls'):
                    probe['website_urls'] = previous.get('website_urls', [])[:]
                check_websites(probe, fetcher)
                if probe.get('website_status') == 'restricted':
                    if browser is None:
                        try: browser = stack.enter_context(BrowserProfiles())
                        except Exception: browser = False
                    if browser:
                        retry_restricted_sites(probe, BrowserSiteFetcher(browser.browser))
                result = store.save_enrichment([probe])
                summary['updated'] += result['updated']
                summary['skipped'] += result['skipped']
                summary['sheets_written'] |= bool(result['updated'])
                # Public logs contain counters only, never profile names or biographies.
                print(f'Проверено {index + 1}/{len(selected)}', flush=True)
        summary['status'] = 'completed' if provider_summary['status'] == 'SUCCEEDED' and not summary['missing_results'] and not summary.get('time_budget_exhausted') else 'provider_partial'
        return 0 if summary['status'] == 'completed' else 2
    except Exception:
        summary['status'] = 'error'
        raise
    finally:
        save_summary(output, summary)
