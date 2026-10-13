from __future__ import annotations
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

RESERVED = {"p", "reel", "reels", "stories", "explore", "accounts", "about", "legal",
            "developer", "developers", "directory", "direct", "tv", "web", "challenge"}
USERNAME = re.compile(r"[a-zA-Z0-9_](?:[a-zA-Z0-9_.]{0,28}[a-zA-Z0-9_])?")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def profile_username(url: str) -> str | None:
    """Only actual profile URLs, never infer a seller from an arbitrary @mention."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            parsed = urlparse(target)
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in {
        "instagram.com", "www.instagram.com", "m.instagram.com"}:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if not parts or parts[0].lower() in RESERVED:
        return None
    if len(parts) > 1 and parts[1:] not in (["reels"], ["tagged"]):
        return None
    username = parts[0].lower()
    return username if USERNAME.fullmatch(username) else None


def own_snippet(snippet: str, username: str) -> str:
    markers = list(re.finditer(r"\(@([a-zA-Z0-9_.]+)\)", snippet))
    if markers and markers[0].group(1).lower() != username:
        return ""
    if len(markers) > 1:
        other = next((m for m in markers[1:] if m.group(1).lower() != username), None)
        if other:
            headers = list(re.finditer(r"[\d,.]+[KMB]?\s+Followers", snippet[:other.start()], re.I))
            cutoff = headers[-1].start() if headers and headers[-1].start() > markers[0].end() else other.start()
            return snippet[:cutoff]
    return snippet


def merge_result(leads: dict, row: dict, query: dict, timestamp: str) -> bool:
    username = profile_username(row.get("href", ""))
    if not username:
        return False
    fresh = username not in leads
    lead = leads.setdefault(username, {
        "username": username, "profile_url": f"https://www.instagram.com/{username}/",
        "title": "", "evidence": [], "query_languages": [], "niche_hints": [],
        "first_seen": timestamp, "last_seen": timestamp, "review_status": "new",
        "followers": None, "last_post_at": None, "account_language": None,
        "country": None, "website_status": "unknown", "website_urls": [],
    })
    lead["last_seen"] = timestamp
    title = re.split(r"(?:- |• )Instagram", row.get("title", ""), maxsplit=1)[0].strip()
    snippet = own_snippet(row.get("body", ""), username)
    # Some search results concatenate adjacent profile cards. Do not attribute
    # another shop's caption or statistics to the first profile in the card.
    markers = list(re.finditer(r"\(@([a-zA-Z0-9_.]+)\)", snippet))
    if markers and markers[0].group(1).lower() != username:
        snippet = ""
    elif len(markers) > 1:
        other = next((m for m in markers[1:] if m.group(1).lower() != username), None)
        if other:
            headers = list(re.finditer(r"[\d,.]+[KMB]?\s+Followers", snippet[:other.start()], re.I))
            snippet = snippet[:headers[-1].start() if len(headers) > 1 else other.start()]
    if title and title != "Link to instagram.com":
        lead["title"] = title[:500]
    evidence = {"url": row.get("href", "")[:2000], "title": title[:500],
                "snippet": snippet[:2500], "query": query["query"], "engine": row.get("engine", ""),
                "query_language": query["language"], "observed_at": timestamp}
    identity = (evidence["url"], evidence["title"], evidence["snippet"])
    if not any((e["url"], e["title"], e["snippet"]) == identity for e in lead["evidence"]):
        lead["evidence"].append(evidence)
        lead["evidence"] = lead["evidence"][-12:]
    for field, value in [("query_languages", query["language"]), ("niche_hints", query["niche"])]:
        if value not in lead[field]:
            lead[field].append(value)
    return fresh
