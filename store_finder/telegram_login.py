"""One-time interactive cloud-terminal login; never run inside Actions."""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import re
import sys


async def wait_for_qr(client, publish, max_refreshes=20):
    from telethon.errors import SessionPasswordNeededError
    qr = await client.qr_login()
    for attempt in range(max_refreshes):
        publish(qr.url)
        try:
            # Default timeout is the server token expiration, not a fixed 120s.
            await qr.wait()
            return
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass('Пароль двухэтапной защиты Telegram: '))
            return
        except asyncio.TimeoutError:
            if attempt + 1 == max_refreshes:
                raise ValueError('Время входа закончилось. Запустите команду заново.')
            await qr.recreate()
            print('QR обновлён. Сканируйте изображение на странице входа.')


async def authorize_qr(client):
    import io
    import qrcode
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    state = {'png': b'', 'status': 'waiting'}
    page = b'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="referrer" content="no-referrer"><title>Telegram login</title>
<style>body{font:18px system-ui;text-align:center;background:#f7f8fc;color:#253047}
main{max-width:620px;margin:40px auto;padding:24px;background:white;border-radius:18px}
img{width:320px;height:320px;image-rendering:pixelated}</style><main>
<h1>Telegram</h1><p id="status">Scan in Telegram: Settings - Devices - Link Desktop Device</p>
<img id="qr" alt="Telegram login QR"><p>QR refreshes automatically. Keep this page private.</p>
</main><script>async function refresh(){try{
const r=await fetch('/status',{cache:'no-store'});const s=await r.text();
if(s!=='waiting'){document.getElementById('qr').hidden=true;
document.getElementById('status').textContent=s==='success'?'Login completed. Return to terminal.':'Login stopped. Return to terminal.';return;}
document.getElementById('qr').src='/qr.png?t='+Date.now();
}catch(e){}setTimeout(refresh,1000);}refresh();</script></html>'''

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split('?', 1)[0]
            if route == '/':
                payload, content_type = page, 'text/html; charset=utf-8'
            elif route == '/qr.png':
                payload, content_type = state['png'], 'image/png'
            elif route == '/status':
                payload, content_type = state['status'].encode(), 'text/plain'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store, max-age=0')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass  # Do not log login-token requests.

    server = ThreadingHTTPServer(('127.0.0.1', 8766), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print('Откройте вкладку ПОРТЫ в Codespaces, добавьте порт 8766, видимость PRIVATE.')
    print('Нажмите значок глобуса у порта 8766: откроется страница с обновляемым QR.')
    print('На телефоне: Telegram → Настройки → Устройства → Подключить устройство.')
    print('Изображение обновляется автоматически. Не открывайте старый telegram-login.png.')

    def publish(url):
        buffer = io.BytesIO()
        qrcode.make(url).save(buffer, format='PNG')
        state['png'] = buffer.getvalue()

    try:
        await wait_for_qr(client, publish)
        state['status'] = 'success'
    finally:
        state['png'] = b''
        if state['status'] != 'success':
            state['status'] = 'stopped'
        await asyncio.to_thread(server.shutdown)
        server.server_close()


async def authorize_phone(client):
    from telethon.errors import SessionPasswordNeededError
    phone = input('Номер телефона в международном формате (+375…): ').strip()
    if not re.fullmatch(r'\+[0-9]{7,15}', phone):
        raise ValueError('Номер нужен в формате +кодстраныномер, без пробелов.')
    sent = await client.send_code_request(phone)
    kind = type(getattr(sent, 'type', None)).__name__
    destinations = {'SentCodeTypeApp': 'в служебный чат Telegram на уже подключённых устройствах',
                    'SentCodeTypeSms': 'по SMS', 'SentCodeTypeCall': 'голосовым звонком',
                    'SentCodeTypeEmailCode': 'на почту для входа'}
    print('Telegram сообщил способ доставки: ' + destinations.get(kind, kind))
    if kind not in destinations:
        raise ValueError('Этот способ входа не поддержан помощником. Используйте --qr.')
    code = getpass.getpass('Код входа (ввод скрыт): ').strip()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except SessionPasswordNeededError:
        await client.sign_in(password=getpass.getpass('Пароль двухэтапной защиты Telegram: '))


async def login(use_qr=False):
    from telethon import TelegramClient
    api_id = os.environ.get('TELEGRAM_API_ID', '').strip() or input('api_id: ').strip()
    api_hash = os.environ.get('TELEGRAM_API_HASH', '').strip() or getpass.getpass('api_hash: ').strip()
    if not api_id.isdigit() or int(api_id) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
        raise ValueError('Некорректный api_id или api_hash.')
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(), int(api_id), api_hash, flood_sleep_threshold=0)
    try:
        await client.connect()
        if use_qr:
            await authorize_qr(client)
        else:
            await authorize_phone(client)
        me = await client.get_me()
        if not me or me.bot:
            raise ValueError('Войдите пользовательским аккаунтом, не ботом.')
        auth = Path('.auth')
        auth.mkdir(mode=0o700, exist_ok=True)
        os.chmod(auth, 0o700)
        for name, value in [('TELEGRAM_API_ID', api_id), ('TELEGRAM_API_HASH', api_hash),
                            ('TELEGRAM_SESSION', client.session.save())]:
            path = auth / (name + '.txt')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(value)
        print('Готово. Три файла .auth/TELEGRAM_*.txt содержат значения для GitHub Secrets.')
        print('Сессия не напечатана в терминал. Перенесите значения в Secrets и удалите Codespace.')
    finally:
        await client.disconnect()


def main():
    if os.environ.get('GITHUB_ACTIONS') == 'true' or not sys.stdin.isatty():
        print('Первый вход выполняется в интерактивном терминале Codespaces, не в Actions.', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description='Создать сессию Telegram в Codespaces')
    parser.add_argument('--qr', action='store_true', help='Вход по QR через Telegram на телефоне без кода сообщения')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        asyncio.run(login(args.qr))
        return 0
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        if type(exc).__name__ in {'FloodWaitError', 'PhoneNumberFloodError', 'PhonePasswordFloodError'}:
            seconds = getattr(exc, 'seconds', None)
            print('Telegram ограничил попытки входа. ' + (f'Повторите через {seconds} сек.' if seconds else 'Не повторяйте запросы подряд; используйте --qr позже.'), file=sys.stderr)
        else:
            print(f'Вход не завершён ({type(exc).__name__}). Секреты не выводятся.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
