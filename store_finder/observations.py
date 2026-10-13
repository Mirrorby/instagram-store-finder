"""Merge profile observations into freshly read Sheet rows without erasing history."""
import copy
from .qualification import qualify, settings
from .scoring import score

MANUAL_EXCLUSIONS = {'Купил', 'Не подходит'}

SITE_FIELDS = {'website_urls', 'website_status', 'website_final_url', 'website_http_status',
               'website_source', 'website_checked_at', 'website_checks', 'links_complete'}

PROFILE_FIELDS = {'biography', 'is_private', 'profile_status', 'profile_data_source',
                  'browser_status', 'browser_api_status', 'enrichment_checked_at', 'profile_id'}

ACTIVITY_FIELDS = {'last_post_at', 'latest_post_url', 'activity_source', 'activity_complete',
                   'recent_posts_sample', 'recent_captions', 'post_samples'}

def fresh_probe(lead):
    # A previous complete timeline must not suppress today's post collection.
    keys = ['username', 'profile_url', 'title', 'evidence', 'query_languages', 'niche_hints',
            'first_seen', 'last_seen', 'review_status']
    probe = {key: copy.deepcopy(lead[key]) for key in keys if key in lead}
    probe.update(profile_status='unknown', website_status='unknown',
                 website_urls=[], links_complete=False, activity_complete=False)
    return probe

def merge_enrichment(current, result):
    """Merge new observations without erasing history on an incomplete response."""
    if current['username'] != result['username'] or current['profile_url'] != result['profile_url']:
        raise ValueError('Аккаунт результата не совпадает со строкой таблицы.')
    lead = copy.deepcopy(current)
    for key in PROFILE_FIELDS:
        if key in result:
            lead[key] = copy.deepcopy(result[key])
    if result.get('followers') is not None:
        for key in ['followers', 'followers_source', 'followers_approximate']:
            if key in result: lead[key] = result[key]
    if result.get('profile_status') == 'verified' and result.get('title'):
        lead['title'] = result['title']
    if result.get('last_post_at'):
        if not lead.get('last_post_at') or result['last_post_at'] >= lead['last_post_at']:
            for key in ACTIVITY_FIELDS:
                if key in result: lead[key] = copy.deepcopy(result[key])
        else:
            lead['activity_complete'] = False
    else:
        lead['activity_complete'] = False
    if result.get('links_complete'):
        for key in SITE_FIELDS:
            # Confirmed absence clears the old site's final URL and HTTP code.
            lead[key] = copy.deepcopy(result.get(key))
        lead['website_urls'] = result.get('website_urls', [])
    elif result.get('website_urls'):
        for key in SITE_FIELDS - {'links_complete', 'website_source'}:
            if key in result: lead[key] = copy.deepcopy(result[key])
    lead['links_complete'] = bool(result.get('links_complete'))
    score(lead)
    qualify(lead, settings())
    return lead
