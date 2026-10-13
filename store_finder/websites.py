from __future__ import annotations
import html
import re
from urllib.parse import parse_qs, urlparse, urlunparse
from .models import now

SOCIAL = {"instagram.com", "facebook.com", "fb.com", "t.me", "telegram.me", "wa.me", "whatsapp.com",
          "youtube.com", "youtu.be", "tiktok.com", "pinterest.com", "x.com", "twitter.com"}
HUBS = {"linktr.ee", "taplink.cc", "taplink.ru", "beacons.ai", "linkin.bio", "bio.site", "solo.to", "msha.ke", "lnk.bio"}


def root_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def is_social(url: str) -> bool:
    host = root_host(url)
    return any(host == domain or host.endswith("." + domain) for domain in SOCIAL)


def normalize_site(value: str) -> str | None:
    value = html.unescape(str(value)).strip().rstrip('.,;)\"\'')
    parsed = urlparse(value if "://" in value else "https://" + value)
    if parsed.hostname == "l.instagram.com":
        value = parse_qs(parsed.query).get("u", [""])[0]
        parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    if is_social(urlunparse(parsed)):
        return None
    return urlunparse(parsed._replace(fragment=""))


def links_in_bio(text: str) -> list[str]:
    # Only explicit URLs/domains. An email address never proves a website exists.
    pattern = r"(?<![\w@])(?:https?://|www\.)[^\s<>\"]+|(?<![\w@.-])(?:[a-zA-Z0-9-]+\.)+(?:com|net|org|shop|store|ru|by|uz|ua|co|io|de|fr|it|es|pt|jp|kr|id|tr|uk|ca|au|in|br|cc|eu|gg)(?:/[^\s<>\"]*)?(?![\w@.-])"
    result = []
    for match in re.finditer(pattern, text):
        site = normalize_site(match.group())
        if site and site not in result:
            result.append(site)
    return result[:5]


def check_site(url: str, fetcher) -> dict:
    page = fetcher.get(url, max_bytes=400_000)
    result = {"url": url, "final_url": page.url, "http_status": page.status,
              "checked_at": now(), "status": "unknown", "error": page.error}
    if page.error:
        result["status"] = {"dns_error": "unreachable", "connection_error": "unreachable",
                            "tls_error": "tls_error", "timeout": "temporary", "redirect_loop": "broken"}.get(page.error, "unknown")
        return result
    code = page.status or 0
    if code in {404, 410}:
        result["status"] = "broken"
    elif code in {401, 403, 429}:
        result["status"] = "restricted"
    elif code >= 500 or code in {408, 425}:
        result["status"] = "temporary"
    elif code == 204 or (200 <= code < 300 and not page.text.strip()):
        result["status"] = "empty"
    elif 200 <= code < 300:
        lowered = page.text.lower()
        title_match = re.search(r"<title[^>]*>(.*?)</title>", lowered, re.S)
        title = html.unescape(re.sub(r"<[^>]+>", " ", title_match.group(1))) if title_match else ""
        visible = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", lowered, flags=re.S)))
        if any(marker in lowered for marker in ["cf-chl-", "g-recaptcha", "verify you are human", "captcha-container"]):
            result["status"] = "restricted"
        elif re.search(r"\b404\b|site not found|website not found|domain for sale|account suspended|сайт не найден", title):
            result["status"] = "empty"
        elif len(visible.strip()) < 600 and any(marker in visible for marker in ["this domain is for sale", "site is suspended", "website is not available"]):
            result["status"] = "empty"
        else:
            result["status"] = "working_hub" if root_host(page.url) in HUBS else "working"
    return result


def check_websites(lead: dict, fetcher):
    links = list(dict.fromkeys(filter(None, (normalize_site(x) for x in lead.get("website_urls", [])))))[:5]
    checks = [check_site(url, fetcher) for url in links]
    lead["website_checks"] = checks
    lead["website_checked_at"] = now() if checks else lead.get("website_checked_at")
    if not checks:
        lead["website_status"] = "missing" if lead.get("links_complete") else "unknown"
        return
    order = ["working", "working_hub", "restricted", "temporary", "unknown", "unreachable", "tls_error", "broken", "empty"]
    status = next((status for status in order if any(c["status"] == status for c in checks)), "unknown")
    lead["website_status"] = status
    selected = next(c for c in checks if c["status"] == status)
    lead["website_final_url"] = selected["final_url"]
    lead["website_http_status"] = selected["http_status"]
