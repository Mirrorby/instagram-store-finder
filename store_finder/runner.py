import json
import time
from pathlib import Path
from .models import merge_result, now, profile_username
from .queries import load_queries, select_queries
from .scoring import score
from .search import SearchUnavailable


def discover(state: dict, queries: list[dict], client, budget: int, limit: int, delay: float, max_seconds: float = 600) -> dict:
    selected, _ = select_queries(queries, state["cursor"], budget)
    started = time.monotonic()
    page = 1 + state.get("cycle", 0) % 3
    stats = {"queries_attempted": 0, "queries_succeeded": 0, "search_results": 0,
             "profile_results": 0, "new_profiles": 0, "likely_stores": 0,
             "engines": {}, "errors": [], "languages": [], "page": page}
    touched = set()
    timestamp = now()
    for index, query in enumerate(selected):
        if index and time.monotonic() - started >= max_seconds:
            stats["time_budget_exhausted"] = True
            break
        stats["queries_attempted"] += 1
        if query["language"] not in stats["languages"]:
            stats["languages"].append(query["language"])
        try:
            rows, engine, errors = client.search(query["query"], limit, page=page)
            stats["queries_succeeded"] += 1
            stats["engines"][engine] = stats["engines"].get(engine, 0) + 1
            stats["errors"].extend(errors)
            stats["search_results"] += len(rows)
            for row in rows:
                row = {**row, "engine": engine}
                username = profile_username(row.get("href", ""))
                if not username:
                    continue
                stats["profile_results"] += 1
                if merge_result(state["leads"], row, query, timestamp):
                    stats["new_profiles"] += 1
                touched.add(username)
        except SearchUnavailable as exc:
            stats["errors"].append(str(exc))
        if index + 1 < len(selected):
            time.sleep(delay)
    for username in touched:
        lead = score(state["leads"][username])
        if lead["classification"] == "likely_store":
            stats["likely_stores"] += 1
    # A complete outage retries the same batch instead of silently losing it.
    if stats["queries_succeeded"]:
        if state["cursor"] + stats["queries_attempted"] >= len(queries):
            state["cycle"] = state.get("cycle", 0) + 1
        state["cursor"] = (state["cursor"] + stats["queries_attempted"]) % len(queries)
    state["runs"] += 1
    state["last_run"] = timestamp
    state["last_stats"] = stats
    return stats


def write_summary(path: Path, stats: dict, state: dict, persisted: bool):
    # Public Actions logs/artifacts contain counters only, never leads or search snippets.
    summary = {**stats, "total_profiles": len(state["leads"]),
               "total_likely_stores": sum(l.get("classification") == "likely_store" for l in state["leads"].values()),
               "persisted": persisted}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
