import time
from collections import Counter
from datetime import datetime, timezone
from contextlib import ExitStack
from .browser import BrowserProfiles
from .models import now
from .network import Fetcher
from .profiles import apply_indexed_data, fetch_profile, parse_indexed_post
from .qualification import qualify, settings
from .scoring import score
from .search import SearchUnavailable
from .websites import check_websites, retry_restricted_sites
from .site_browser import BrowserSiteFetcher


def candidates(state: dict, config: dict, budget: int) -> list[dict]:
    current = datetime.now(timezone.utc)
    selected = []
    for lead in state["leads"].values():
        if lead.get("review_status") in {"Не подходит", "Купил"}:
            continue
        checked = lead.get("enrichment_checked_at")
        if checked:
            age = (current - datetime.fromisoformat(checked)).total_seconds() / 86400
            threshold = config["recheck_after_days"] if lead.get("profile_status") == "verified" else 1
            if age < threshold:
                continue
        selected.append(lead)
    selected.sort(key=lambda lead: (lead.get("enrichment_checked_at") or "", -lead.get("score", 0), lead["username"]))
    return selected[:budget]


def indexed_activity(lead: dict, client, fetcher):
    query = f'site:instagram.com "{lead["username"]}" "on Instagram"'
    try:
        rows, _, _ = client.search(query, 5)
    except SearchUnavailable:
        return
    checked = 0
    for row in rows:
        url = row.get("href", "")
        if not url.startswith(("https://www.instagram.com/", "https://instagram.com/")):
            continue
        if not any(part in url for part in ["/p/", "/reel/"]):
            continue
        page = fetcher.get(url)
        if page.status == 200:
            parse_indexed_post(lead, page, url)
        checked += 1
        if checked >= 2:
            break


def enrich(state: dict, client, budget: int | None = None, max_seconds=420) -> dict:
    config = settings()
    selected = candidates(state, config, budget or config["enrichment_budget"])
    started = time.monotonic()
    fetcher = Fetcher()
    for lead in state["leads"].values():
        apply_indexed_data(lead)
    stats = {"selected": len(selected), "checked": 0, "processed": 0, "skipped_after_restriction": 0, "profiles_with_followers": 0}
    stack = ExitStack()
    try:
        browser = stack.enter_context(BrowserProfiles()) if selected else None
        stats["chromium"] = "ready" if browser else "not_needed"
    except Exception:
        browser = None
        stats["chromium"] = "unavailable"
    denied = 0
    for lead in selected:
        if time.monotonic() - started > max_seconds:
            stats["time_budget_exhausted"] = True
            break
        attempted = False
        if browser and denied < 2:
            attempted = True
            browser.fetch(lead)
            denied += int(lead.get("profile_status") == "restricted")
        if lead.get("profile_status") != "verified" and denied < 2:
            attempted = True
            fetch_profile(lead, fetcher)
            denied += int(lead.get("profile_status") == "restricted")
        # Known bio links can still be checked when Instagram restricts profile access.
        check_websites(lead, fetcher)
        if browser and lead.get('website_status') == 'restricted':
            retry_restricted_sites(lead, BrowserSiteFetcher(browser.browser))
        stats["processed"] += 1
        stats["checked"] += int(attempted)
        stats["skipped_after_restriction"] += int(not attempted and denied >= 2)
        if lead.get('instagram_rate_limited') or lead.get('post_access_restricted'):
            stats['stopped_on_instagram_restriction'] = True
            break
        time.sleep(1)
    stack.close()
    for lead in state["leads"].values():
        score(lead)
        qualify(lead, config)
    stats["profiles_with_followers"] = sum(lead.get("followers") is not None for lead in state["leads"].values())
    stats["decisions"] = dict(Counter(lead.get("qualification", "review") for lead in state["leads"].values()))
    stats["websites"] = dict(Counter(lead.get("website_status", "unknown") for lead in state["leads"].values()))
    stats["browser_results"] = dict(Counter(lead.get("browser_status", "not_checked") for lead in state["leads"].values()))
    return stats
