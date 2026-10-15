"""Loopback-only local dashboard and sequential Instagram queue; no Apify."""
import contextlib
import csv
import fcntl
import io
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
from .desk_store import DeskStore, CONTACT_STATUSES
from .models import now

PROJECT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / 'desk_web'


class Desk:
    def __init__(self, root):
        self.store = DeskStore(root)
        self.store.scan_demos(PROJECT)
        self.token = secrets.token_urlsafe(32)
        self.api_key = ''  # Never stored in SQLite or returned by the API.
        self.busy = threading.Lock()
        self.stop = threading.Event()
        self.shutdown = threading.Event()
        self.paused = False
        destination = json.loads((PROJECT / 'config/destination.json').read_text())
        defaults = {'sheet_id': destination['spreadsheet_id'], 'cdp_endpoint': 'ws://127.0.0.1:9222/devtools/browser',
                    'origin': 'https://catalog-factory.nikita-karpof.workers.dev', 'language': 'auto',
                    'currency': 'USD', 'theme': 'minimal', 'batch_size': 3, 'auto': False,
                    'publish': False, 'sync_minutes': 60, 'last_sync': 0}
        config = {**defaults, **self.store.config()}
        if config['publish']:
            config['auto'] = False  # A new process does not have the publishing key yet.
        self.store.set_config(config)

    def settings(self, data):
        from .browser import local_cdp_endpoint
        from .personal_demos import PlatformAPI
        allowed = {'sheet_id', 'cdp_endpoint', 'origin', 'language', 'currency', 'theme', 'batch_size', 'auto', 'publish', 'sync_minutes', 'api_key'}
        if not set(data) <= allowed:
            raise ValueError('Неизвестная настройка')
        cfg = {**self.store.config(), **{k: v for k, v in data.items() if k != 'api_key'}}
        from .account_languages import SUPPORTED
        if cfg['language'] not in {'auto', *SUPPORTED} or cfg['currency'] not in {'USD', 'BYN', 'EUR', 'RUB', 'KZT'} or cfg['theme'] not in {'minimal', 'fashion', 'home', 'beauty'}:
            raise ValueError('Проверьте язык, валюту и шаблон')
        if type(cfg['batch_size']) is not int or not 1 <= cfg['batch_size'] <= 10 or type(cfg['sync_minutes']) is not int or not 5 <= cfg['sync_minutes'] <= 1440:
            raise ValueError('Партия: 1–10; интервал: 5–1440 минут')
        if type(cfg['auto']) is not bool or type(cfg['publish']) is not bool:
            raise ValueError('Недопустимый режим')
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', cfg['sheet_id']):
            raise ValueError('Укажите ID Google-таблицы')
        local_cdp_endpoint(cfg['cdp_endpoint'])
        new_key = data.get('api_key', self.api_key)
        PlatformAPI(cfg['origin'], new_key or 'validation-placeholder-key-32-characters')
        if cfg['origin'] != self.store.config()['origin']:
            new_key = data.get('api_key', '')  # Do not send an old key to a new server.
        self.store.set_config(cfg)
        self.api_key = new_key

    def state(self):
        from .account_languages import language_statistics, SUPPORTED, NAMES
        accounts = self.store.all()
        statistics = language_statistics(accounts)
        return {'accounts': accounts, 'jobs': self.store.jobs(), 'settings': self.store.config(),
                'language_stats': statistics, 'supported_languages': [{'code':c,'name':NAMES[c]} for c in SUPPORTED],
                'busy': self.busy.locked(), 'paused': self.paused, 'has_key': bool(self.api_key), 'token': self.token}

    def submit(self, action, names=None):
        if action not in {'sync', 'process', 'publish', 'discover', 'refresh'}:
            raise ValueError('Неизвестная операция')
        if names is not None:
            if not isinstance(names, list) or not 1 <= len(names) <= 10:
                raise ValueError('Выберите от 1 до 10 аккаунтов')
            for name in names:
                self.store.get(name)
        if (action == 'publish' or action in {'process', 'refresh'} and self.store.config()['publish']) and not self.api_key:
            raise ValueError('Добавьте ключ Delokay в настройках панели')
        if not self.busy.acquire(blocking=False):
            raise ValueError('Другая операция ещё выполняется')
        self.stop.clear()
        self.paused = False
        job = self.store.new_job(action)
        def work():
            try:
                if action == 'sync':
                    result = self.sync_sheet()
                    self.store.job(job, f"Добавлено: {result['added']}. Строк: {result['rows']}. Некорректных: {result['invalid']}", 'done')
                elif action == 'discover':
                    self.discover(job)
                else:
                    self.process(job, names, publish=action == 'publish', refresh=action == 'refresh')
            except Exception as exc:
                from .demo_browser import InstagramRestricted
                self.paused = True
                self.store.set_config({'auto': False})
                message = str(exc) if isinstance(exc, (ValueError, InstagramRestricted)) else 'Операция остановлена: ' + type(exc).__name__ + '. Проверьте Chrome, вход и соединение.'
                self.store.job(job, message, 'error')
            finally:
                self.busy.release()
        threading.Thread(target=work, daemon=True).start()
        return job

    def sync_sheet(self):
        from .browser import BrowserProfiles
        from playwright.sync_api import Error
        cfg = self.store.config()
        with BrowserProfiles(attach_chrome=True, cdp_endpoint=cfg['cdp_endpoint']) as browser:
            # Uses the existing Google login in Chrome. No cookies or tokens exported.
            page = browser.context.new_page()
            try:
                with page.expect_download(timeout=45000) as pending:
                    try:
                        page.goto('https://docs.google.com/spreadsheets/d/' + cfg['sheet_id'] + '/export?format=csv&sheet=' + '%D0%9C%D0%B0%D0%B3%D0%B0%D0%B7%D0%B8%D0%BD%D1%8B', timeout=45000)
                    except Error:
                        pass  # Normal download navigation is aborted by Chrome.
                download = pending.value
                if download.failure():
                    raise ValueError('Google не отдал таблицу. Откройте её в Chrome и проверьте доступ.')
                path = download.path()
                if Path(path).stat().st_size > 20_000_000:
                    raise ValueError('Таблица превышает 20 МБ; импортируйте меньший CSV')
                result = self.store.import_csv(Path(path).read_text(encoding='utf-8-sig'))
                self.store.set_config({'last_sync': time.time()})
                return result
            finally:
                page.close()

    def discover(self, job):
        from .search import SearchClient
        from .runner import discover
        from .storage import empty_state
        from .queries import load_queries
        state = empty_state()
        state.update(self.store.config().get('search_cursor', {}))
        for row in self.store.all():
            if row['data'].get('evidence') is not None:
                state['leads'][row['username']] = row['data']
        stats = discover(state, load_queries(PROJECT / 'config/queries.json'), SearchClient(['bing', 'brave', 'duckduckgo']), 4, 20, 3, max_seconds=180)
        added = sum(self.store.add(n, lead.get('title', ''), lead) for n, lead in state['leads'].items())
        self.store.set_config({'search_cursor': {k: state[k] for k in ('cursor', 'cycle', 'runs')}})
        self.store.job(job, f"Найдено новых: {added}. Успешных запросов: {stats['queries_succeeded']}", 'done' if stats['queries_succeeded'] else 'error')

    def process(self, job, names=None, publish=False, refresh=False):
        from .browser import BrowserProfiles
        from .demo_browser import collect_demo, InstagramRestricted
        from .personal_demos import archive_media, make_bundle, save, write_preview, PlatformAPI, publish as publish_bundle, contact_draft, private_write, update_demo_profile, locked
        from .demo_identity import account_language
        from .network import Fetcher
        from .websites import check_websites
        from .scoring import score
        from .qualification import qualify, settings as qualification_settings
        cfg = self.store.config()
        selected = [self.store.get(n) for n in names] if names else [r for r in self.store.all() if r['stage'] == 'queued' and r['contact_status'] not in {'Написал', 'Ответил', 'Купил', 'Не подходит'}][:cfg['batch_size']]
        root = self.store.root / 'demos'
        count = 0
        failures = 0
        api = PlatformAPI(cfg['origin'], self.api_key) if publish or cfg['publish'] else None
        with locked(root), contextlib.ExitStack() as stack:
            browser = None
            for row in selected:
                if self.stop.is_set():
                    break
                name = row['username']
                directory = Path(row['bundle_path']) if row['bundle_path'] else root / name
                if refresh:
                    if (directory / 'publish.json').exists():
                        raise ValueError('Для опубликованного демо используйте обновление языка и контактов. Исходные фото сохранены.')
                    directory = root / name / 'runs' / (str(time.time_ns()))
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.store.update(name, stage='checking', error='')
                self.store.job(job, f"@{name}: проверка и подготовка ({count + 1}/{len(selected)})")
                try:
                    path = directory / 'bundle.json'
                    if path.exists():
                        bundle = json.loads(path.read_text())
                        if not (directory / 'publish.json').exists():
                            bundle = make_bundle(bundle['source'], cfg['language'], cfg['currency'], cfg['theme'])
                            save(path, bundle)
                    else:
                        if browser is None:
                            browser = stack.enter_context(BrowserProfiles(attach_chrome=True, cdp_endpoint=cfg['cdp_endpoint']))
                        lead = {**row['data'], **collect_demo(browser, name)}
                        lead.setdefault('evidence', [])
                        lead['recent_captions'] = [p['caption'] for p in lead.get('photo_posts', [])]
                        score(lead)
                        check_websites(lead, Fetcher())
                        qualify(lead, qualification_settings())
                        lead['account_language'] = account_language(lead)
                        save(directory / 'source.json', lead)
                        self.store.update(name, title=lead.get('title', name), data=lead)
                        archive_media(lead, directory)
                        bundle = make_bundle(lead, cfg['language'], cfg['currency'], cfg['theme'])
                        save(path, bundle)
                    if bundle['username'] != name:
                        raise ValueError('Сохранённый сбор относится к другому аккаунту')
                    write_preview(bundle, directory)
                    self.store.update(name, bundle_path=str(directory.resolve()), data=bundle['source'], title=bundle['store']['name'])
                    if bundle['issues']:
                        self.store.update(name, stage='review', error='; '.join(bundle['issues']))
                    elif api:
                        update_demo_profile(bundle, directory, api, cfg['language'])
                        result = publish_bundle(bundle, directory, api)
                        message = contact_draft(bundle, result['store']['url'])
                        private_write(directory / 'first-contact.txt', message + '\n')
                        self.store.update(name, stage='ready', demo_url=result['store']['url'], message=message, error='')
                    else:
                        self.store.update(name, stage='ready' if row['demo_url'] else 'collected', error='')
                    count += 1
                except InstagramRestricted:
                    self.store.update(name, stage='restricted', error='Instagram ограничил доступ. Партия остановлена; проверьте Chrome.')
                    raise
                except Exception as exc:
                    failures += 1
                    message = str(exc) if isinstance(exc, ValueError) else 'Ошибка: ' + type(exc).__name__
                    self.store.update(name, stage='error', error=message[:1000])
        self.store.job(job, f'Обработано: {count} из {len(selected)} · ошибок: {failures}' + (' · остановлено пользователем' if self.stop.is_set() else ''), 'error' if failures else 'done')

    def schedule(self):
        while not self.shutdown.wait(30):
            cfg = self.store.config()
            if not cfg['auto'] or self.busy.locked() or self.paused:
                continue
            try:
                if time.time() - cfg['last_sync'] >= cfg['sync_minutes'] * 60:
                    self.submit('sync')
                elif any(r['stage'] == 'queued' and r['contact_status'] == 'Новый' for r in self.store.all()):
                    self.submit('process')
            except ValueError:
                pass


def serve(root, port=8765):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / '.desk.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Панель уже запущена для этой базы')
        app = Desk(root)
        origin = f'http://127.0.0.1:{port}'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, status, content, mime='application/json; charset=utf-8'):
                body = json.dumps(content, ensure_ascii=False).encode() if mime.startswith('application/json') else content
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(body)

            def trusted(self):
                return self.headers.get('Host') == f'127.0.0.1:{port}' and self.headers.get('Origin', origin) == origin

            def do_GET(self):
                if not self.trusted():
                    return self.send(403, {'error': 'Локальный доступ'})
                path = urlparse(self.path).path
                if path == '/api/state':
                    return self.send(200, app.state())
                if path.startswith('/preview/'):
                    try:
                        name = unquote(path[len('/preview/'):])
                        row = app.store.get(name)
                        file = Path(row['bundle_path']) / 'preview.html'
                        if not row['bundle_path'] or not file.is_file():
                            raise ValueError('Локальный просмотр ещё не готов')
                        html = re.sub(r'<style>.*?</style>', '<link rel="stylesheet" href="/preview.css">', file.read_text(), flags=re.S)
                        html = re.sub(r'src="assets/([a-f0-9]{64}\.(?:jpg|png|webp))"', lambda m: 'src="/media/' + name + '/' + m[1] + '"', html)
                        return self.send(200, html.encode(), 'text/html; charset=utf-8')
                    except ValueError as exc:
                        return self.send(404, {'error': str(exc)})
                if path.startswith('/media/'):
                    parts = path.split('/')
                    if len(parts) != 4 or not re.fullmatch(r'[a-f0-9]{64}\.(?:jpg|png|webp)', parts[3]):
                        return self.send(404, {'error': 'Фото не найдено'})
                    try:
                        row = app.store.get(unquote(parts[2]))
                        file = Path(row['bundle_path']) / 'assets' / parts[3]
                        if not file.is_file():
                            raise ValueError('Фото не найдено')
                        return self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0])
                    except ValueError as exc:
                        return self.send(404, {'error': str(exc)})
                if path in {'/', '/app.js', '/style.css', '/logo.png', '/preview.css'}:
                    file = WEB / {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/logo.png': 'logo.png', '/preview.css': 'preview.css'}[path]
                    return self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] + '; charset=utf-8')
                return self.send(404, {'error': 'Не найдено'})

            def do_POST(self):
                if not self.trusted() or not secrets.compare_digest(self.headers.get('X-Desk-Token', ''), app.token):
                    return self.send(403, {'error': 'Обновите страницу панели'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 20_000_000:
                        raise ValueError('Запрос пустой или превышает 20 МБ')
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError('Нужен объект')
                    path = urlparse(self.path).path
                    if path == '/api/settings':
                        if app.busy.locked():
                            raise ValueError('Дождитесь завершения операции')
                        app.settings(data)
                    elif path == '/api/add':
                        names = re.split(r'[\s,;]+', data.get('accounts', '').strip())
                        if not names or len(names) > 500:
                            raise ValueError('От 1 до 500 аккаунтов')
                        for name in names:
                            app.store.add(name)
                    elif path == '/api/import':
                        app.store.import_csv(data['csv'])
                    elif path == '/api/account':
                        username = data['username']
                        app.store.get(username)
                        fields = {k: str(data[k])[:10000] for k in ('note', 'contact_status', 'message') if k in data}
                        app.store.update(username, **fields)
                    elif path == '/api/stop':
                        app.stop.set()
                        app.store.set_config({'auto': False})
                    elif path == '/api/job':
                        return self.send(202, {'job': app.submit(data['action'], data.get('names'))})
                    else:
                        return self.send(404, {'error': 'Не найдено'})
                    return self.send(200, {'ok': True})
                except (ValueError, KeyError, TypeError) as exc:
                    return self.send(400, {'error': str(exc)[:1000]})
                except Exception as exc:
                    return self.send(500, {'error': type(exc).__name__})

        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        threading.Thread(target=app.schedule, daemon=True).start()
        print('Локальная панель: ' + origin, flush=True)
        print('Apify не используется. Ctrl+C завершает приложение. Ключ Delokay хранится только в памяти.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown.set()
            app.stop.set()
            server.server_close()
