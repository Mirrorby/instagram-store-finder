"""Small sequential batches, no Instagram login, proxy rotation or CAPTCHA solving."""
import logging
import os
import time

logging.getLogger("ddgs").setLevel(logging.CRITICAL)


class SearchUnavailable(RuntimeError):
    pass


class SearchClient:
    def __init__(self, engines: list[str], timeout: int = 12):
        from ddgs import DDGS
        # Honor an environment's standard outbound transport; do not rotate proxies.
        self.client = DDGS(timeout=timeout, proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
        self.engines = engines

    def search(self, query: str, limit: int, page: int = 1) -> tuple[list[dict], str, list[str]]:
        errors = []
        for engine in self.engines:
            try:
                rows = self.client.text(query, backend=engine, region="wt-wt",
                                        safesearch="moderate", max_results=limit, page=page)
                if rows:
                    return rows[:limit], engine, errors
                errors.append(engine + ":empty")
            except Exception as exc:
                # Never log raw exception messages: they can contain URLs/snippets/secrets.
                errors.append(engine + ":" + type(exc).__name__)
            time.sleep(1)
        raise SearchUnavailable(",".join(errors))
