"""Read current public profile HTML; parse structured provider data when available."""
from __future__ import annotations
import html
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from .models import now, profile_username, own_snippet
from .websites import links_in_bio, normalize_site


class ProfileDocument(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.scripts = []
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key:
                self.meta[key] = attrs.get("content", "")
        if tag == "script" and attrs.get("type") in {"application/json", "application/ld+json"}:
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.active = False


def number_count(value: str) -> tuple[int | None, bool]:
    value = value.strip().replace("\u00a0", "").replace(" ", "")
    match = re.fullmatch(r"([\d.,]+)([KMB]|тыс\.?|млн\.?)?", value, flags=re.I)
    if not match:
        return None, False
    raw, suffix = match.groups()
    if suffix:
        suffix = suffix.lower().rstrip(".")
        factor = {"k": 1000, "тыс": 1000, "m": 1_000_000, "млн": 1_000_000, "b": 1_000_000_000}[suffix]
        return round(float(raw.replace(",", ".")) * factor), True
    # Locale thousands separators in metadata: 2,116 or 2.116.
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        raw = re.sub(r"[.,]", "", raw)
    elif not raw.isdecimal():
        return None, False
    return int(raw), False


def followers_in_text(text: str) -> tuple[int | None, bool]:
    pattern = r"([\d][\d\s.,\u00a0]*(?:[KMB]|тыс\.?|млн\.?)?)\s*(?:Followers|подписчик\w*|seguidores|abonnés|follower)\b"
    match = re.search(pattern, html.unescape(text), flags=re.I)
    return number_count(match.group(1)) if match else (None, False)


def timestamp(value):
    try:
        if isinstance(value, (int, float)):
            date = datetime.fromtimestamp(value, timezone.utc)
        else:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        if date.year < 2010 or date > datetime.now(timezone.utc) + timedelta(days=1):
            return None
        return date.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def apply_profile_record(lead: dict, record: dict, source: str) -> bool:
    requested = lead["username"]
    if str(record.get("username", "")).lower() != requested:
        return False
    if record.get("error"):
        lead["profile_status"] = "unavailable"
        return False
    profile_id = record.get('id', record.get('pk'))
    if profile_id is not None and str(profile_id).isdecimal():
        lead['profile_id'] = str(profile_id)
    followers = record.get("followersCount", record.get("follower_count", (record.get("edge_followed_by") or {}).get("count")))
    if isinstance(followers, int) and not isinstance(followers, bool) and followers >= 0:
        lead["followers"] = followers
        lead["followers_approximate"] = False
        lead["followers_source"] = source
    name = record.get("fullName", record.get("full_name"))
    if name:
        lead["title"] = str(name)[:500]
    biography = record.get("biography")
    if isinstance(biography, str):
        lead["biography"] = biography[:4000]
    privacy = record.get("private", record.get("is_private"))
    if isinstance(privacy, bool):
        lead["is_private"] = privacy
    external_keys = {"externalUrl", "external_url", "externalUrls", "bio_links"}
    if any(key in record for key in external_keys):
        urls = [record.get("externalUrl", record.get("external_url", ""))]
        for item in record.get("externalUrls", record.get("bio_links", [])) or []:
            urls.append(item.get("url", "") if isinstance(item, dict) else str(item))
        urls.extend(links_in_bio(lead.get("biography", "")))
        lead["website_urls"] = list(dict.fromkeys(filter(None, (normalize_site(value) for value in urls if value))))[:5]
        lead["links_complete"] = True
        lead["website_source"] = source
    posts = record.get("latestPosts")
    timeline = record.get("edge_owner_to_timeline_media") or {}
    total = record.get("postsCount", record.get("media_count", timeline.get("count")))
    if posts is None:
        posts = [edge.get("node", {}) for edge in timeline.get("edges", [])]
    dated = []
    captions = []
    for post in (posts or [])[:12]:
        owner = post.get("ownerUsername", (post.get("owner") or {}).get("username"))
        if owner and str(owner).lower() != requested:
            continue
        date = timestamp(post.get("timestamp", post.get("taken_at_timestamp", post.get("taken_at"))))
        if date:
            url = post.get("url", "") or ("https://www.instagram.com/p/" + post["shortcode"] + "/" if post.get("shortcode") else "")
            dated.append((date, url))
        caption = post.get("caption")
        if not caption:
            edges = post.get("edge_media_to_caption", {}).get("edges", [])
            caption = edges[0].get("node", {}).get("text", "") if edges else ""
        if caption:
            captions.append(str(caption)[:1000])
    if dated:
        lead["last_post_at"], lead["latest_post_url"] = max(dated)
        lead["activity_source"] = source
        lead["activity_complete"] = len(dated) > 3 or (isinstance(total, int) and total <= len(dated))
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        lead["recent_posts_sample"] = sum(datetime.fromisoformat(date) >= cutoff for date, _ in dated)
    if captions:
        lead["recent_captions"] = captions
    lead["profile_status"] = "private" if lead.get("is_private") else "verified"
    lead["profile_data_source"] = source
    return True


def parse_profile_html(lead: dict, text: str) -> bool:
    document = ProfileDocument()
    document.feed(text)
    matched_json = False
    for script in document.scripts:
        try:
            stack = [json.loads(script)]
        except (ValueError, TypeError):
            continue
        visited = 0
        while stack and visited < 12000:
            item = stack.pop()
            visited += 1
            if isinstance(item, dict):
                if (item.get("username") == lead["username"] and
                        any(key in item for key in ["edge_followed_by", "follower_count", "biography", "external_url"])):
                    matched_json |= apply_profile_record(lead, item, "profile_html")
                stack.extend(value for value in item.values() if isinstance(value, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    if matched_json:
        return True
    title = document.meta.get("og:title", "")
    description = document.meta.get("og:description", "")
    marker = re.search(r"\(@([a-zA-Z0-9_.]+)\)", title + " " + description)
    if not marker or marker.group(1).lower() != lead["username"]:
        return False
    followers, approximate = followers_in_text(description)
    if followers is not None:
        lead["followers"] = followers
        lead["followers_approximate"] = approximate
        lead["followers_source"] = "profile_html"
    if title:
        lead["title"] = re.split(r"(?:- |• )Instagram", title, maxsplit=1)[0].strip()
    # og:description often omits bio/links/posts: these fields remain unknown.
    lead["profile_status"] = "partial"
    lead["profile_data_source"] = "profile_html"
    return True


def apply_indexed_data(lead: dict):
    snippets = [own_snippet(e["snippet"], lead["username"]) for e in lead["evidence"] if e.get("snippet")]
    if lead.get("followers") is None:
        for snippet in reversed(snippets):
            count, approximate = followers_in_text(snippet)
            if count is not None:
                lead["followers"], lead["followers_approximate"] = count, approximate
                lead["followers_source"] = "search_index"
                break
    if not lead.get("website_urls") and not lead.get("links_complete"):
        links = list(dict.fromkeys(url for text in snippets for url in links_in_bio(text)))[:5]
        if links:
            lead["website_urls"] = links
            lead["website_source"] = "search_index"


def fetch_profile(lead: dict, fetcher):
    page = fetcher.get(lead["profile_url"])
    lead["enrichment_checked_at"] = now()
    if page.status == 200 and parse_profile_html(lead, page.text):
        return
    if page.status in {401, 403, 429} or "/accounts/login" in page.url:
        lead["profile_status"] = "restricted"
    elif page.status in {404, 410}:
        # A generic Instagram 404 alone is not proof an account was deleted.
        lead["profile_status"] = "unavailable"
    else:
        lead["profile_status"] = "unknown"


def parse_indexed_post(lead: dict, page, url: str) -> bool:
    document = ProfileDocument()
    document.feed(page.text)
    title = document.meta.get("og:title", "")
    own = bool(re.search(r"(?:\(@|\b)" + re.escape(lead["username"]) + r"(?:\)|\s+on Instagram)", title, re.I))
    path = url.split("instagram.com/", 1)[-1].lower()
    own |= path.startswith(lead["username"] + "/")
    if not own:
        return False
    for script in document.scripts:
        try:
            stack = [json.loads(script)]
        except ValueError:
            continue
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                date = timestamp(item.get("datePublished"))
                if date:
                    previous = lead.get("last_post_at")
                    if not previous or date > previous:
                        lead["last_post_at"] = date
                        lead["latest_post_url"] = url
                        lead["activity_source"] = "indexed_post_page"
                        lead["activity_complete"] = False
                    return True
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    return False
