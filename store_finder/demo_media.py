"""Owned photo posts from HTML/JSON. Never treat a video thumbnail as a photo."""
import json
import re
from urllib.parse import urlparse
from .profiles import ProfileDocument, apply_profile_record, timestamp
from .post_dates import owned_media, post_url


def image_url(value):
    if not isinstance(value, str) or len(value) > 4000:
        return ''
    try:
        url = urlparse(value)
        host = (url.hostname or '').lower()
        return value if (url.scheme == 'https' and not url.username and not url.password
                         and url.port in {None, 443}
                         and any(host.endswith('.' + domain) for domain in ('cdninstagram.com', 'fbcdn.net'))) else ''
    except ValueError:
        return ''


def photo_kind(item):
    if item.get('is_video') is True or item.get('media_type') == 2 or item.get('type') == 'Video' or item.get('__typename') == 'GraphVideo':
        return 'video'
    if item.get('product_type') in {'clips', 'igtv'} or item.get('video_url') or item.get('video_versions'):
        return 'video'
    if item.get('media_type') == 8 or item.get('type') == 'Sidecar' or item.get('__typename') == 'GraphSidecar':
        return 'carousel'
    if item.get('media_type') == 1 or item.get('is_video') is False or item.get('type') == 'Image' or item.get('__typename') == 'GraphImage':
        return 'photo'
    return 'unknown'


def best_image(item):
    candidates = (item.get('image_versions2') or {}).get('candidates') or item.get('display_resources') or []
    valid = [x for x in candidates if isinstance(x, dict) and image_url(x.get('url', x.get('src')))]
    # Prefer a usable full-size image; no need to download multi-megapixel originals for demos.
    if valid:
        valid.sort(key=lambda x: abs((x.get('width', x.get('config_width', 1080)) or 1080) - 1080))
        return image_url(valid[0].get('url', valid[0].get('src')))
    return image_url(item.get('display_url', item.get('displayUrl', '')))


def caption_text(item):
    caption = item.get('caption')
    if isinstance(caption, dict):
        caption = caption.get('text')
    if not isinstance(caption, str):
        edges = (item.get('edge_media_to_caption') or {}).get('edges') or []
        caption = (edges[0].get('node') or {}).get('text', '') if edges else ''
    return caption.strip()[:5000]


def remember_photo(lead, item, inherited_owner=False):
    if item.get('expiring_at') or not (owned_media(lead, item) or inherited_owner):
        return False
    kind = photo_kind(item)
    code = item.get('shortcode', item.get('code', item.get('shortCode')))
    url = post_url(str(item.get('url', '')), lead['username'])
    if not url and isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', code):
        url = f'https://www.instagram.com/p/{code}/'
    date = timestamp(item.get('taken_at', item.get('taken_at_timestamp', item.get('timestamp'))))
    if isinstance(code, str) and date and lead.get('period_days'):
        lead.setdefault('_timeline_dates', {})[code] = {'date': date, 'pinned': bool(item.get('is_pinned') or item.get('pinned_for_users'))}
    if kind == 'video' and isinstance(code, str) and lead.get('period_days'):
        lead.setdefault('_video_codes', set()).add(code)
    if not url or '/reel/' in url or kind not in {'photo', 'carousel'}:
        return False
    code = url.rstrip('/').rsplit('/', 1)[-1]
    children = item.get('carousel_media') or [x.get('node', {}) for x in (item.get('edge_sidecar_to_children') or {}).get('edges', [])]
    if kind == 'carousel':
        # Mixed carousels: retain only children explicitly identified as still photos.
        images = [best_image(x) for x in children if isinstance(x, dict) and photo_kind(x) == 'photo']
        if not children:  # A cover without the children cannot establish that it is a still image.
            return False
    else:
        images = [best_image(item)]
    images = list(dict.fromkeys(filter(None, images)))[:8]
    if not images:
        return False
    date = timestamp(item.get('taken_at', item.get('taken_at_timestamp', item.get('timestamp'))))
    child_page = (item.get('edge_sidecar_to_children') or {}).get('page_info') or {}
    expected = item.get('carousel_media_count') or item.get('sidecar_count') or 0
    complete = bool(children) and not child_page.get('has_next_page', False) and (not isinstance(expected, int) or not expected or len(children) >= expected)
    post = {'carousel_complete': complete, 'code': code, 'url': url, 'date': date, 'caption': caption_text(item),
            'images': images, 'kind': kind, 'source': 'page_json'}
    known = {p['code']: p for p in lead.get('photo_posts', [])}
    old = known.get(code, {})
    post['carousel_complete'] = post['carousel_complete'] or old.get('carousel_complete', False)
    post['caption'] = post['caption'] or old.get('caption', '')
    post['date'] = post['date'] or old.get('date')
    if len(old.get('images', [])) > len(images):
        post['images'] = old['images']
    known[code] = post
    lead['photo_posts'] = sorted(known.values(), key=lambda p: p['date'] or '', reverse=True)[:lead.get('photo_post_limit', 60)]
    return True


def apply_demo_payload(lead, payload):
    stack, visited = [(payload, False)], 0
    while stack and visited < 18000:
        item, inherited = stack.pop()
        visited += 1
        if isinstance(item, dict):
            own_profile = str(item.get('username', '')).lower() == lead['username']
            if own_profile:
                timeline = item.get('edge_owner_to_timeline_media')
                if isinstance(timeline, dict):
                    info = timeline.get('page_info')
                    if isinstance(info, dict) and type(info.get('has_next_page')) is bool:
                        lead['_timeline_exhausted'] = not info['has_next_page']
                apply_profile_record(lead, item, 'chromium')
                avatar = item.get('profile_pic_url_hd') or item.get('profilePicUrlHD') or item.get('profile_pic_url') or item.get('profilePicUrl')
                avatar = avatar or (item.get('hd_profile_pic_url_info') or {}).get('url')
                if image_url(avatar):
                    lead['logo_url'] = avatar
            # Ownership may be inherited only from this account's explicit timeline.
            remember_photo(lead, item, inherited and not (item.get('owner') or item.get('user') or item.get('ownerUsername')))
            for key, value in item.items():
                if isinstance(value, (dict, list)):
                    scope = (own_profile and key in {'edge_owner_to_timeline_media', 'latestPosts'}) or (inherited and key in {'edges', 'node'})
                    stack.append((value, scope))
        elif isinstance(item, list):
            stack.extend((value, inherited) for value in item)


def apply_demo_html(lead, html, url=None):
    document = ProfileDocument()
    document.feed(html)
    for script in document.scripts:
        try:
            apply_demo_payload(lead, json.loads(script))
        except (ValueError, TypeError):
            continue
    if not url:  # Profile avatar only when metadata explicitly identifies this account.
        marker = re.search(r'\(@' + re.escape(lead['username']) + r'\)', document.meta.get('og:title', ''), re.I)
        if marker and image_url(document.meta.get('og:image')) and not lead.get('logo_url'):
            lead['logo_url'] = document.meta['og:image']
        if marker and not lead.get('biography'):
            # This is the quoted bio, not the followers/following/post-count prefix.
            bio = re.search(r'(?:on Instagram|в Instagram):\s*["“](.*)["”]\s*$', document.meta.get('og:description', ''), re.S)
            if bio:
                lead['biography'] = bio.group(1).strip()[:4000]
    return document
