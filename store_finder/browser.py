"""Public Chromium context: page responses and one same-origin profile fetch."""
import json
import os
from urllib.parse import urlparse
from .models import now
from .profiles import apply_profile_record, parse_profile_html


def apply_payload(lead, payload):
    stack, visited, applied = [payload], 0, False
    while stack and visited < 12000:
        item = stack.pop()
        visited += 1
        if isinstance(item, dict):
            if any(key in item for key in ("follower_count", "edge_followed_by", "biography", "external_url")):
                applied |= apply_profile_record(lead, item, "chromium")
            stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            stack.extend(item)
    return applied


class BrowserProfiles:
    def __init__(self, headless=True, storage_state=None):
        self.headless = headless
        self.storage_state = storage_state

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        options = {"headless": self.headless}
        transport = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if transport:
            proxy = urlparse(transport)
            options["proxy"] = {"server": f"{proxy.scheme}://{proxy.hostname}:{proxy.port or 80}"}
            if proxy.username:
                options["proxy"].update(username=proxy.username, password=proxy.password or "")
        executable = os.environ.get("CHROMIUM_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        try:
            self.browser = self.runtime.chromium.launch(**options)
            self.context = self.browser.new_context(locale="en-US", storage_state=str(self.storage_state) if self.storage_state else None,
                ignore_https_errors=os.environ.get("BROWSER_IGNORE_HTTPS_ERRORS") == "1")
        except Exception:
            self.runtime.stop()
            raise
        self.denied = False
        return self

    def __exit__(self, *args):
        self.browser.close()
        self.runtime.stop()

    def fetch(self, lead):
        from playwright.sync_api import Error
        page = self.context.new_page()
        lead["enrichment_checked_at"] = now()
        payloads, statuses = [], []

        def capture(response):
            parsed = urlparse(response.url)
            if parsed.hostname not in {"www.instagram.com", "instagram.com"}:
                return
            if "/api/" not in parsed.path and "/graphql/" not in parsed.path:
                return
            statuses.append(response.status)
            if len(payloads) >= 20 or response.status != 200:
                return
            try:
                body = response.body()
                if len(body) <= 3_000_000:
                    payloads.append(json.loads(body))
            except (Error, ValueError):
                pass

        page.on("response", capture)
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in {"image", "media", "font"} else route.continue_())
        try:
            response = page.goto(lead["profile_url"], wait_until="commit", timeout=25000)
            page.wait_for_timeout(3000)
            html_found = parse_profile_html(lead, page.content())
            applied = False
            for payload in payloads:
                applied |= apply_payload(lead, payload)
            login = "/accounts/login" in page.url or "/challenge/" in page.url
            restricted = login or (response and response.status in {401, 403, 429})
            if not applied and not restricted and not self.denied:
                result = page.evaluate("""async username => {
                  const controller = new AbortController();
                  const timer = setTimeout(() => controller.abort(), 10000);
                  try {
                    const response = await fetch('/api/v1/users/web_profile_info/?username=' + encodeURIComponent(username),
                      {credentials: 'same-origin', headers: {'X-IG-App-ID': '936619743392459'}, signal: controller.signal});
                    const text = await response.text();
                    return {status: response.status, body: text.length < 3000000 ? text : ''};
                  } catch {return {status: 0, body: ''};}
                  finally {clearTimeout(timer);}
                }""", lead["username"])
                statuses.append(result["status"])
                if result["status"] == 200:
                    try:
                        applied |= apply_payload(lead, json.loads(result["body"]))
                    except ValueError:
                        pass
                if result["status"] in {401, 403, 429}:
                    self.denied = True
            if applied:
                lead["profile_data_source"] = "chromium"
            elif restricted:
                lead["profile_status"] = "restricted"
            elif not html_found:
                lead["profile_status"] = "unknown"
            lead["browser_status"] = "verified" if applied else "partial" if html_found else "restricted" if restricted else "unknown"
            lead["browser_api_status"] = next((code for code in reversed(statuses) if code), None)
        except Error:
            lead["browser_status"] = "error"
        finally:
            page.close()
