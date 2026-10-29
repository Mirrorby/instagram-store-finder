"""One-time interactive cloud-terminal login; never run inside Actions."""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import re
import sys


async def authorize_qr(client):
    import qrcode
    from telethon.errors import SessionPasswordNeededError
    auth = Path('.auth')
    auth.mkdir(mode=0o700, exist_ok=True)
    path = auth / 'telegram-login.png'
    qr = await client.qr_login()
    qrcode.make(qr.url).save(path)
    os.chmod(path, 0o600)
    print('Откройте .auth/telegram-login.png в файловой панели Codespaces.')
    print('На телефоне: Telegram → Настройки → Устройства → Подключить устройство.')
    print('Отсканируйте QR. Не публикуйте изображение. Ожидание: до 120 секунд.')
    try:
        try:
            await qr.wait(timeout=120)
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass('Пароль двухэтапной защиты Telegram: '))
        except asyncio.TimeoutError:
            raise ValueError('QR истёк или не подтверждён. Запустите команду заново для нового QR.')
    finally:
        path.unlink(missing_ok=True)


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
