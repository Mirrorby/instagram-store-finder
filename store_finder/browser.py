"""Public Chromium context: page responses and one same-origin profile fetch."""
import json
import os
import re
import sys
from pathlib import Path
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


def chrome_endpoint(user_data_dir=None):
    if user_data_dir is None:
        if sys.platform != 'darwin':
            raise ValueError('Укажите --chrome-user-data-dir для этой системы')
        user_data_dir = Path.home() / 'Library/Application Support/Google/Chrome'
    path = Path(user_data_dir).expanduser() / 'DevToolsActivePort'
    try:
        lines = path.read_text().strip().splitlines()
        port = int(lines[0])
        route = lines[1].strip()
        if not 1 <= port <= 65535 or not re.fullmatch(r'/devtools/browser(?:/[A-Za-z0-9_-]+)?/?', route):
            raise ValueError('Invalid local endpoint')
        return f'ws://127.0.0.1:{port}{route}'
    except (OSError, ValueError, IndexError) as exc:
        raise ValueError('Откройте обычный Chrome 144+, перейдите в chrome://inspect/#remote-debugging и включите разрешение удалённой отладки. Если используется другой каталог Chrome, укажите --chrome-user-data-dir.') from exc


def local_cdp_endpoint(value):
    try:
        parsed = urlparse(value)
        if (parsed.scheme not in {'http', 'ws'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username or parsed.password or not parsed.port or parsed.query or parsed.fragment):
            raise ValueError('Invalid local endpoint')
        if parsed.scheme == 'http' and parsed.path not in {'', '/'}:
            raise ValueError('Invalid HTTP endpoint path')
        if parsed.scheme == 'ws' and not re.fullmatch(r'/devtools/browser(?:/[A-Za-z0-9_-]+)?/?', parsed.path):
            raise ValueError('Invalid WebSocket endpoint path')
        host = '[::1]' if parsed.hostname == '::1' else '127.0.0.1'
        return parsed._replace(netloc=f'{host}:{parsed.port}').geturl()
    except (ValueError, TypeError) as exc:
        raise ValueError('Укажите локальный адрес Chrome, например --cdp-endpoint http://127.0.0.1:9222') from exc


def connection_error_detail(error):
    # Only used for a local CDP connection; omit its temporary debugger ID.
    detail = str(error).split('Call log:', 1)[0].strip()
    detail = re.sub(r'/devtools/browser/[A-Za-z0-9_-]+', '/devtools/browser/[скрыто]', detail)
    return detail[:1000] or type(error).__name__


def connect_existing_chrome(browser_type, endpoint):
    from playwright.sync_api import Error
    try:
        return browser_type.connect_over_cdp(endpoint, timeout=60000)
    except Error as exc:
        parsed = urlparse(endpoint)
        # Chrome's permission-based UI exposes WebSocket CDP without HTTP discovery.
        # Try that documented route once, only on a discovery 404; no retries on denial.
        if parsed.scheme != 'http' or 'Unexpected status 404' not in str(exc):
            raise
        websocket = local_cdp_endpoint(parsed._replace(scheme='ws', path='/devtools/browser').geturl())
        print('HTTP discovery недоступен. Подключаюсь через WebSocket; подтвердите запрос Chrome.', flush=True)
        return browser_type.connect_over_cdp(websocket, timeout=60000)


class BrowserProfiles:
    def __init__(self, headless=True, storage_state=None, attach_chrome=False, chrome_user_data_dir=None, cdp_endpoint=None):
        self.headless = headless
        self.storage_state = storage_state
        self.attach_chrome = bool(attach_chrome or cdp_endpoint)
        self.chrome_user_data_dir = chrome_user_data_dir
        self.cdp_endpoint = local_cdp_endpoint(cdp_endpoint) if cdp_endpoint else None

    def __enter__(self):
        from playwright.sync_api import sync_playwright, Error
        self.runtime = sync_playwright().start()
        if self.attach_chrome:
            try:
                endpoint = self.cdp_endpoint or chrome_endpoint(self.chrome_user_data_dir)
                print('Подключаюсь к открытому Chrome. Если браузер покажет запрос, разрешите подключение.', flush=True)
                self.browser = connect_existing_chrome(self.runtime.chromium, endpoint)
                if not self.browser.contexts:
                    raise ValueError('В подключённом Chrome нет доступного контекста')
                self.context = self.browser.contexts[0]
            except Error as exc:
                self.runtime.stop()
                raise ValueError('Не удалось подключиться к Chrome.\nПричина: ' + connection_error_detail(exc) +
                    '\nСкопируйте строку «Причина» для диагностики. Если Chrome показал запрос подключения, разрешите его.') from exc
            except Exception:
                self.runtime.stop()
                raise
            self.denied = False
            return self
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
        try:
            if not self.attach_chrome:
                self.browser.close()
        finally:
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
        if not self.attach_chrome:
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
