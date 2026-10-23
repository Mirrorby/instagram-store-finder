"""One bounded cloud import. Run only as a child of railway_service.

Uses a fresh headless Chromium, never the local Mac Chrome or Apify.
"""
import json
import os
import sys
from pathlib import Path

from .browser import BrowserProfiles
from .demo_browser import InstagramRestricted, collect_demo
from .personal_demos import PlatformAPI, archive_media, make_bundle, publish, save


def run(directory):
    request = json.loads((directory / 'request.json').read_text())
    result = directory / 'result.json'
    try:
        if request.get('kind') == 'website':
            from .reference_site import analyze_reference
            reference = analyze_reference(request['url'])
            save(directory / 'bundle.json', {'kind': 'website', 'reference': reference})
            save(result, {'status': 'ready', 'kind': 'website', 'reference': reference})
            return
        path = directory / 'bundle.json'
        if path.exists():
            bundle = json.loads(path.read_text())
        else:
            session = directory.parent.parent / 'instagram-session.json'
            with BrowserProfiles(headless=True, storage_state=session if session.exists() else None) as browser:
                lead = collect_demo(browser, request['username'], request['products'],
                                    max_pages=501 if request.get('period_days') else 18,
                                    max_scrolls=200 if request.get('period_days') else 12,
                                    period_days=request.get('period_days'),
                                    on_progress=lambda value: save(directory / 'progress.json', value))
            save(directory / 'source.json', lead)
            if request.get('period_days') and not lead.get('period', {}).get('complete'):
                save(result, {'status': 'failed', 'error': 'period_collection_incomplete'})
                return
            archive_media(lead, directory, on_progress=lambda value: save(directory / 'progress.json', value))
            bundle = make_bundle(lead, request['language'], request['currency'], request['theme'], 1)
            # Separate drafts for separate jobs, even for the same Instagram account.
            bundle['store']['slug'] = 'ig-' + request['username'].replace('.', '-').replace('_', '-') + '-' + directory.name[:12]
            save(path, bundle)
        if os.environ.get('IMPORT_PUBLISH_ENABLED', '1') == '0':
            save(result, {'status': 'needs_review' if bundle['issues'] else 'ready',
                          'kind': 'instagram', 'published': False, 'issues': bundle['issues'],
                          'products': len(bundle['store']['products']),
                          'profile': {'username': bundle['username'], 'name': bundle['store']['name'],
                                      'description': bundle['store']['settings']['description']}})
            return
        if bundle['issues']:
            save(result, {'status': 'needs_review', 'issues': bundle['issues']})
            return
        api = PlatformAPI(os.environ['STORE_API_ORIGIN'], os.environ['PLATFORM_API_KEY'])
        state = publish(bundle, directory, api)
        save(result, {'status': 'ready', 'store': state['store'],
                      'products': len(bundle['store']['products'])})
    except InstagramRestricted as error:
        save(result, {'status': 'restricted', 'error': 'instagram_access_restricted',
                      'reason': error.reason, 'httpStatus': error.status})
    except Exception:
        # Remote errors and browser traces may contain credentials or signed URLs.
        save(result, {'status': 'failed', 'error': 'import_failed'})


if __name__ == '__main__':
    run(Path(sys.argv[1]))
