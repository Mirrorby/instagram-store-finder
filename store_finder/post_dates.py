"""Dates from owned media responses and visible post pages; bounded, no date guessing."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from .profiles import ProfileDocument, timestamp


def post_url(value, username=None):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.hostname not in {'www.instagram.com', 'instagram.com'}:
        return None
    match = re.fullmatch(r'/(?:(?P<owner>[A-Za-z0-9_.]+)/)?(?P<kind>p|reel)/(?P<code>[A-Za-z0-9_-]+)/?', parsed.path)
    if not match or (match['owner'] and username and match['owner'].lower() != username):
        return None
    return f'https://www.instagram.com/{match["kind"]}/{match["code"]}/'


def remember_post(lead, url, date, source, caption=''):
    date = timestamp(date)
    url = post_url(url, lead['username'])
    if not date or not url:
        return False
    samples = {sample['url'].rstrip('/').rsplit('/', 1)[-1]: sample for sample in lead.get('post_samples', [])}
    samples[url.rstrip('/').rsplit('/', 1)[-1]] = {'url': url, 'date': date, 'source': source, 'caption': str(caption)[:1000]}
    ordered = sorted(samples.values(), key=lambda sample: sample['date'], reverse=True)[:12]
    lead['post_samples'] = ordered
    latest = ordered[0]
    if not lead.get('last_post_at') or latest['date'] >= lead['last_post_at']:
        lead['last_post_at'], lead['latest_post_url'] = latest['date'], latest['url']
        lead['activity_source'] = latest['source']
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    lead['recent_posts_sample'] = sum(datetime.fromisoformat(sample['date']) >= cutoff for sample in ordered)
    captions = [sample['caption'] for sample in ordered if sample.get('caption')]
    if captions:
        lead['recent_captions'] = captions
    return True


def owned_media(lead, item):
    owner = item.get('user') or item.get('owner') or {}
    if not isinstance(owner, dict):
        return False
    name = item.get('ownerUsername') or owner.get('username')
    if name:
        return str(name).lower() == lead['username']
    owner_id = owner.get('id', owner.get('pk', item.get('ownerId')))
    return bool(lead.get('profile_id') and owner_id is not None and str(owner_id) == lead['profile_id'])


def apply_media_payload(lead, payload, profile_scope=False):
    stack, visited, applied = [payload], 0, False
    timeline_seen = False
    while stack and visited < 12000:
        item = stack.pop(); visited += 1
        if isinstance(item, dict):
            timeline_seen |= any('user_timeline' in key or key == 'edge_owner_to_timeline_media' for key in item)
            code = item.get('code') or item.get('shortcode') or item.get('shortCode')
            url = post_url(str(item.get('url', '')), lead['username'])
            if not url and isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]+', code):
                url = 'https://www.instagram.com/p/' + code + '/'
            # Story media also has codes and taken_at; it must not count as a post.
            if url and not item.get('expiring_at') and owned_media(lead, item):
                owners = list(dict.fromkeys(lead.get('post_owner_urls', []) + [url]))[:24]
                lead['post_owner_urls'] = owners
                caption = item.get('caption') or ''
                if isinstance(caption, dict): caption = caption.get('text', '')
                date = item.get('taken_at', item.get('taken_at_timestamp', item.get('timestamp')))
                applied |= remember_post(lead, url, date, 'chromium', caption)
            stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            stack.extend(item)
    if profile_scope and timeline_seen and len(lead.get('post_samples', [])) > 3:
        lead['activity_complete'] = True
    return applied


def parse_post_document(lead, url, text, author_links, times):
    document = ProfileDocument(); document.feed(text)
    marker = re.search(r'\(@' + re.escape(lead['username']) + r'\)', document.meta.get('og:title', ''), re.I)
    authors = set()
    for href in author_links:
        parsed = urlparse(href)
        if parsed.hostname in {'www.instagram.com', 'instagram.com'}:
            authors.add(parsed.path.strip('/').lower())
    code = url.rstrip('/').rsplit('/', 1)[-1]
    owned_codes = {value.rstrip('/').rsplit('/', 1)[-1] for value in lead.get('post_owner_urls', [])}
    verified = bool(marker or lead['username'] in authors or code in owned_codes)
    if not verified:
        return False
    # Comment timestamps may appear before the publication footer in the DOM.
    # Prefer a time whose enclosing link is this exact post permalink.
    for value in times:
        if isinstance(value, dict):
            linked_post = post_url(value.get('href', ''), lead['username'])
            if not linked_post or linked_post.rstrip('/').rsplit('/', 1)[-1] != code:
                continue
            value = value.get('date')
        if timestamp(value):
            return remember_post(lead, url, value, 'chromium_post_page')
    return False
