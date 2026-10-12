"""Interleave languages and niches; never restrict accounts by location/language."""
import json
import shlex
from pathlib import Path


def load_queries(path: Path) -> list[dict]:
    groups = json.loads(path.read_text(encoding="utf-8"))["languages"]
    queries = []
    # One query per language before moving to the next niche.
    for index in range(max(len(group["terms"]) for group in groups)):
        for group in groups:
            if index < len(group["terms"]):
                term = group["terms"][index]
                tokens = " ".join(json.dumps(token, ensure_ascii=False) for token in shlex.split(term["text"]))
                queries.append({"language": group["code"], "niche": term["niche"],
                                "query": "site:instagram.com " + tokens + ' "(@"'})
    if not queries:
        raise ValueError("Query configuration must not be empty")
    return queries


def select_queries(queries: list[dict], cursor: int, budget: int) -> tuple[list[dict], int]:
    count = min(budget, len(queries))
    return [queries[(cursor + i) % len(queries)] for i in range(count)], (cursor + count) % len(queries)
