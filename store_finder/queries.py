"""Build rotating discovery queries, with an optional temporary search scope."""
import json
import shlex
from pathlib import Path


def load_queries(path: Path) -> list[dict]:
    config = json.loads(path.read_text(encoding="utf-8"))
    groups = config["languages"]
    scope = config.get("scope", {})
    scoped = scope.get("enabled", False)
    if scoped:
        groups = [group for group in groups if group["code"] in scope["languages"]]
        if not groups or not scope.get("countries"):
            raise ValueError("Enabled search scope requires languages and countries")
    queries = []
    # One query per language before moving to the next niche.
    for index in range(max(len(group["terms"]) for group in groups)):
        for group in groups:
            if index < len(group["terms"]):
                term = group["terms"][index]
                tokens = " ".join(json.dumps(token, ensure_ascii=False) for token in shlex.split(term["text"]))
                base = "site:instagram.com " + tokens + ' "(@"'
                if scoped:
                    exclusions = " ".join("-" + json.dumps(value, ensure_ascii=False)
                                          for value in scope.get("excluded_terms", []))
                    for country in scope["countries"]:
                        places = " OR ".join(json.dumps(value, ensure_ascii=False)
                                             for value in country["terms"])
                        queries.append({"language": group["code"], "niche": term["niche"],
                                        "country_hint": country["code"],
                                        "query": f"{base} ({places}) {exclusions}".strip()})
                else:
                    queries.append({"language": group["code"], "niche": term["niche"], "query": base})
    if not queries:
        raise ValueError("Query configuration must not be empty")
    return queries


def select_queries(queries: list[dict], cursor: int, budget: int) -> tuple[list[dict], int]:
    count = min(budget, len(queries))
    return [queries[(cursor + i) % len(queries)] for i in range(count)], (cursor + count) % len(queries)
