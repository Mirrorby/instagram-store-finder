"""Local Instagram -> durable media -> Delokay demo -> first-contact draft.

No message sending. Checkpoints allow safe resume after network failures.
"""
import contextlib
import fcntl
import hashlib
import html
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse
import requests
from .browser import BrowserProfiles
from .demo_browser import InstagramRestricted, collect_demo
from .demo_media import image_url
from .local_test import usernames
from .models import now


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value):
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


@contextlib.contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / '.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Другой запуск уже использует эту папку демо') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def media_type(data):
    if data.startswith(b'\xff\xd8\xff') and len(data) >= 12:
        return 'image/jpeg', 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 12:
        return 'image/png', 'png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    raise ValueError('Instagram вернул не JPEG/PNG/WebP')


def download_image(url):
    if not image_url(url):
        raise ValueError('Недопустимый адрес изображения Instagram')
    # Separate requests session: never send Chrome cookies or platform credentials to a CDN.
    with requests.get(url, timeout=25, stream=True, allow_redirects=False,
                      headers={'User-Agent': 'Mozilla/5.0'}) as response:
        if response.status_code == 429:
            raise InstagramRestricted('CDN Instagram ограничил доступ. Партия остановлена.')
        if response.status_code != 200:
            raise ValueError('Фото недоступно: обновите сбор Instagram')
        if int(response.headers.get('Content-Length', '0')) > 3 * 1024 * 1024:
            raise ValueError('Фото превышает лимит загрузки 3 МБ')
        data = bytearray()
        for chunk in response.iter_content(16384):
            data.extend(chunk)
            if len(data) > 3 * 1024 * 1024:
                raise ValueError('Фото превышает лимит загрузки 3 МБ')
    mime, ext = media_type(bytes(data))
    return bytes(data), mime, ext


def archive_media(lead, directory):
    assets = directory / 'assets'
    assets.mkdir(exist_ok=True, mode=0o700)
    cached = {}

    def archive(url):
        if url in cached:
            return cached[url]
        data, mime, ext = download_image(url)
        digest = hashlib.sha256(data).hexdigest()
        filename = digest + '.' + ext
        destination = assets / filename
        if not destination.exists():
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
        cached[url] = {'file': 'assets/' + filename, 'sha256': digest, 'mime': mime}
        return cached[url]

    lead['logo_asset'] = archive(lead['logo_url']) if lead.get('logo_url') else None
    for post in lead['photo_posts']:
        post['assets'] = [archive(url) for url in post['images']]


def make_bundle(lead, language='en', currency='USD', theme='minimal', minimum=3):
    issues = []
    if not lead.get('title') or lead['title'] == lead['username']:
        issues.append('Не получено название магазина')
    if not lead.get('biography'):
        issues.append('Не получено описание профиля')
    if not lead.get('logo_asset'):
        issues.append('Не сохранён логотип')
    ready = [p for p in lead.get('photo_posts', []) if p.get('caption') and p.get('date') and p.get('assets')]
    if len(ready) < minimum:
        issues.append(f'Нужно минимум {minimum} фотопубликации с датой и подписью; получено {len(ready)}')
    name = re.sub(r'\s*\(@[^)]+\).*$', '', lead.get('title', '')).strip()[:200] or lead['username']
    slug = 'ig-' + lead['username'].replace('.', '-') + '-' + hashlib.sha256(lead['username'].encode()).hexdigest()[:8]
    products = []
    sources = []
    for post in ready:
        lines = [line.strip() for line in post['caption'].splitlines() if line.strip() and not line.lstrip().startswith(('#', '@', 'http'))]
        title = (lines[0] if lines else ('Товар' if language == 'ru' else 'Product'))[:150]
        products.append({'name': title, 'description': post['caption'],
            'category': 'Каталог' if language == 'ru' else 'Catalog', 'brand': name[:100],
            'sku': 'IG-' + post['code'], 'prices': {currency: 0}, 'priceOnRequest': True,
            'availability': 'preorder', 'photos': [],
            'variants': [{'label': 'Уточнить вариант' if language == 'ru' else 'Confirm option', 'stock': None}]})
        sources.append({'postUrl': post['url'], 'date': post['date'], 'assets': post['assets']})
    return {'schemaVersion': 1, 'username': lead['username'], 'collectedAt': now(),
        'status': 'needs_review' if issues else 'collected', 'issues': issues,
        'source': lead, 'productSources': sources,
        'store': {'name': name, 'slug': slug, 'status': 'demo', 'settings': {
            'language': language, 'theme': theme, 'description': lead.get('biography', '')[:2000],
            'instagram': lead['username'], 'currencies': [currency], 'heroTitle': name,
            'footerText': '© ' + name, 'accentColor': '#5865E8'}, 'products': products}}


class PlatformAPI:
    def __init__(self, origin, key):
        url = urlparse(origin)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment:
            raise ValueError('STORE_API_ORIGIN: нужен HTTPS origin без пути')
        if not key or len(key) < 32:
            raise ValueError('Задайте PLATFORM_API_KEY (ключ панели разработчика, минимум 32 символа)')
        self.origin, self.key = origin.rstrip('/'), key

    def request(self, path, method='GET', data=None, binary=None, mime=None, missing=False):
        headers = {'Authorization': 'Bearer ' + self.key}
        if mime:
            headers['Content-Type'] = mime
        response = requests.request(method, self.origin + path, headers=headers,
            json=data, data=binary, timeout=40, allow_redirects=False)
        if missing and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            # Avoid raw remote messages which may include credentials or response bodies.
            raise ValueError(f'Delokay: HTTP {response.status_code}; этап сохранён, повторите команду после проверки')
        return response.json()


def publish(bundle, directory, api):
    state_path = directory / 'publish.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'uploads': {}, 'origin': api.origin}
    if state.get('origin') != api.origin:
        raise ValueError('Демо привязано к другому STORE_API_ORIGIN')
    if state.get('status') == 'ready':
        return state
    if bundle['issues']:
        raise ValueError('Демо требует проверки: ' + '; '.join(bundle['issues']))
    config = api.request('/api/platform/config')
    if not config.get('features', {}).get('priceOnRequest'):
        raise ValueError('Обновите Delokay: сервер ещё не поддерживает цены по запросу')
    store = bundle['store']
    save(state_path, state)  # Save creation intent BEFORE the mutating request.
    if not state.get('store'):
        existing = api.request('/api/stores/' + store['slug'], missing=True)
        if existing:
            remote = existing['store']
            if not state.get('creating') or remote['settings'].get('instagram') != bundle['username'] or remote['status'] != 'demo':
                raise ValueError('Slug уже занят. Существующий магазин не изменён')
            created = {'id': remote['id'], 'slug': remote['slug'], 'url': api.origin + '/s/' + remote['slug']}
        else:
            state['creating'] = True
            save(state_path, state)
            created = api.request('/api/platform/stores', 'POST', {**store, 'products': []})
            if created['slug'] != store['slug']:
                state['store'] = created
                save(state_path, state)
                raise ValueError('Slug занят параллельным созданием; проверьте новый пустой магазин')
        state['store'] = created
        save(state_path, state)
    base = '/api/platform/stores/' + state['store']['id']
    current = api.request(base)
    if current['status'] != 'demo' or current.get('settings', {}).get('instagram') != bundle['username']:
        raise ValueError('Магазин активирован или изменён вручную; автоматический импорт остановлен')
    snapshot = {k: current[k] for k in ('name', 'settings', 'version')}
    if state.get('remote') and snapshot != state['remote']:
        if snapshot != state.get('settingsIntent'):
            raise ValueError('Настройки магазина изменены вручную; импорт остановлен')
        # The previous PUT succeeded, but its response/checkpoint was lost.
    state['remote'] = snapshot
    state.pop('settingsIntent', None)
    save(state_path, state)

    def upload(asset):
        digest = asset['sha256']
        if digest not in state['uploads']:
            path = (directory / asset['file']).resolve()
            if directory.resolve() not in path.parents:
                raise ValueError('Фото находится вне папки демо')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest or len(data) > 3 * 1024 * 1024:
                raise ValueError('Локальное фото изменено или превышает лимит')
            mime, _ = media_type(data)
            result = api.request(base + '/images', 'POST', binary=data, mime=mime)
            if not result['url'].startswith('/media/' + current['id'] + '/'):
                raise ValueError('API вернул неверный адрес фото')
            state['uploads'][digest] = result['url']
            save(state_path, state)
        return state['uploads'][digest]

    settings = {**current['settings'], **store['settings'], 'logo': upload(bundle['source']['logo_asset'])}
    state['settingsIntent'] = {'name': store['name'], 'settings': settings, 'version': current['version'] + 1}
    save(state_path, state)
    api.request(base + '/settings', 'PUT', {'name': store['name'], 'settings': settings, 'version': current['version']})
    state['remote'] = state.pop('settingsIntent')
    save(state_path, state)
    expected = []
    for product, source in zip(store['products'], bundle['productSources']):
        photos = [upload(asset) for asset in source['assets']]
        known = api.request(base + '/products')
        matches = [p for p in known if p.get('sku') == product['sku']]
        if not matches:
            api.request(base + '/products', 'POST', {**product, 'photos': photos})
        elif len(matches) != 1 or matches[0]['description'] != product['description'] or matches[0]['photos'] != photos:
            raise ValueError('Товар изменён вручную; импорт остановлен')
        expected.append((product, photos))
    public = api.request('/api/stores/' + state['store']['slug'])
    if public['store']['status'] != 'demo' or public['store']['settings'].get('logo') != settings['logo'] or len(public['products']) != len(expected):
        raise ValueError('Публичная проверка демо не пройдена')
    for product, photos in expected:
        actual = next((p for p in public['products'] if p.get('sku') == product['sku']), {})
        if actual.get('priceOnRequest') is not True or actual.get('photos') != photos or actual.get('description') != product['description']:
            raise ValueError('Обновите Delokay: цены по запросу/товары не прошли проверку')
    # Check every permanent image using the same public route a customer will use.
    for url in set(state['uploads'].values()):
        response = requests.head(api.origin + url, timeout=20, allow_redirects=False)
        if response.status_code != 200 or not response.headers.get('Content-Type', '').startswith('image/'):
            raise ValueError('Фото демо не прошло публичную проверку')
    state['status'], state['verifiedAt'] = 'ready', now()
    save(state_path, state)
    return state


def contact_draft(bundle, url):
    name = bundle['store']['name']
    if bundle['store']['settings']['language'] == 'ru':
        return f'Здравствуйте! Подготовил для {name} пример сайта-каталога с вашим логотипом и товарами из Instagram: {url}\n\nЭто демонстрация; реальные заказы отключены. Названия, цены и наличие можно отредактировать. Если вам интересно, покажу, как управлять каталогом и получать заказы в Telegram.'
    return f'Hi! I put together a sample product catalog for {name}, using your logo and products from Instagram: {url}\n\nThis is a demo; real orders are disabled. Product names, prices and availability can be edited. If you are interested, I can show you how to manage the catalog and receive orders in Telegram.'


def write_preview(bundle, directory):
    esc = html.escape
    cards = ''.join('<article><img src="' + esc(source['assets'][0]['file']) + '"><h2>' + esc(p['name']) + '</h2><p>' + esc(p['description']) + '</p><a href="' + esc(source['postUrl']) + '">Instagram</a></article>' for p, source in zip(bundle['store']['products'], bundle['productSources']))
    logo = bundle['source'].get('logo_asset')
    private_write(directory / 'preview.html', '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Demo review</title><style>body{font:16px system-ui;background:#f7f8fc;color:#253047;margin:32px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:24px}article{padding:18px;background:white;border-radius:20px}img{width:100%;height:280px;object-fit:cover;border-radius:12px}p{white-space:pre-wrap}header img{width:80px;height:80px}</style><header>' + ('<img src="' + esc(logo['file']) + '">' if logo else '') + '<h1>' + esc(bundle['store']['name']) + '</h1><p>' + esc(bundle['store']['settings']['description']) + '</p><p>' + esc('; '.join(bundle['issues'])) + '</p></header><main>' + cards + '</main>')


def sheet_queue(limit):
    from .sheets import SheetsStore, configured_sheet
    credentials = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    sheet_id = os.environ.get('GOOGLE_SHEET_ID', '') or configured_sheet()
    if not credentials or not sheet_id:
        raise ValueError('Для --from-sheet нужны GOOGLE_SERVICE_ACCOUNT_JSON и ID таблицы')
    store = SheetsStore(credentials, sheet_id)
    state = store.load()
    # Only qualified, uncontacted candidates. Existing demo URLs are never replaced.
    names = [str(row[0]).strip().lstrip('@').lower() for row in store.rows
             if row and len(row) > 18 and row[16] in {'', 'Новый', 'Проверить'} and not row[18]
             and state['leads'][str(row[0]).strip().lstrip('@').lower()].get('qualification') == 'suitable']
    return names[:limit], store


def mark_sheet_ready(store, username, url):
    store.load()  # Re-find row after possible sorting, re-check current manual status.
    for number, row in enumerate(store.rows, 2):
        if row and str(row[0]).strip().lstrip('@').lower() == username:
            padded = row + [''] * max(0, 20 - len(row))
            if padded[18] == url and padded[16] == 'Демо готово':
                return
            if padded[18] or padded[16] not in {'', 'Новый', 'Проверить'}:
                raise ValueError('Статус/ссылка в таблице изменены; ручные данные сохранены')
            store.sheet.batch_update([{'range': f'Q{number}', 'values': [['Демо готово']]},
                                      {'range': f'S{number}', 'values': [[url]]}], value_input_option='RAW')
            return
    raise ValueError('Аккаунт больше не найден в таблице')


def run_demos(args):
    if not 1 <= args.limit <= 10 or not 1 <= args.products <= 12 or not 1 <= args.min_products <= args.products:
        raise ValueError('Партия: 1–10 аккаунтов; товаров: 1–12; минимум не больше числа товаров')
    sheet = None
    names = usernames(args.accounts) if args.accounts else []
    if args.from_sheet:
        names, sheet = sheet_queue(args.limit)
    elif args.accounts_file:
        lines = [line.strip() for line in args.accounts_file.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
        names = usernames(lines[:args.limit]) if lines else []
    names = names[:args.limit]
    if not names:
        print('В очереди нет подходящих аккаунтов')
        return 0
    api = PlatformAPI(os.environ.get('STORE_API_ORIGIN', 'https://catalog-factory.nikita-karpof.workers.dev'), os.environ.get('PLATFORM_API_KEY', '')) if args.publish else None
    failures = 0
    with locked(args.output):
        with contextlib.ExitStack() as stack:
            browser = None
            for name in names:
                directory = args.output / name
                directory.mkdir(exist_ok=True, mode=0o700)
                path = directory / 'bundle.json'
                try:
                    print(f'Готовлю @{name}…', flush=True)
                    if path.exists():
                        bundle = json.loads(path.read_text())
                        if bundle.get('username') != name:
                            raise ValueError('Аккаунт в сохранённом сборе не совпадает с очередью')
                        print('  Использую сохранённый сбор; Instagram повторно не открывается', flush=True)
                    else:
                        if browser is None:
                            browser = stack.enter_context(BrowserProfiles(headless=False, attach_chrome=True,
                                chrome_user_data_dir=args.chrome_user_data_dir, cdp_endpoint=args.cdp_endpoint))
                        lead = collect_demo(browser, name, args.products, max_pages=min(36, max(18, args.products * 3)))
                        save(directory / 'source.json', lead)
                        archive_media(lead, directory)
                        bundle = make_bundle(lead, args.language, args.currency, args.theme, args.min_products)
                        save(path, bundle)
                        write_preview(bundle, directory)
                    if bundle['issues']:
                        print('  Нужна проверка: ' + '; '.join(bundle['issues']), flush=True)
                        failures += 1
                        continue
                    if api:
                        state = publish(bundle, directory, api)
                        url = state['store']['url']
                        private_write(directory / 'first-contact.txt', contact_draft(bundle, url) + '\n')
                        if sheet:
                            mark_sheet_ready(sheet, name, url)
                        print('  Демо и текст первого сообщения готовы: ' + url, flush=True)
                    else:
                        print('  Собрано. Локальная проверка: ' + str(directory / 'preview.html'), flush=True)
                except InstagramRestricted as exc:
                    print(str(exc), flush=True)
                    return 2
                except (ValueError, requests.RequestException, OSError) as exc:
                    failures += 1
                    # requests exceptions can contain full CDN URLs or Authorization details.
                    message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    print('  Не завершено: ' + message, flush=True)
                    save(directory / 'error.json', {'at': now(), 'error': message})
    return 2 if failures else 0
