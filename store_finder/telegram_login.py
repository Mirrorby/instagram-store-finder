"""One-time interactive cloud-terminal login; never run inside Actions."""
import asyncio
import getpass
import os
from pathlib import Path
import re
import sys


async def login():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    api_id = os.environ.get('TELEGRAM_API_ID', '').strip() or input('api_id: ').strip()
    api_hash = os.environ.get('TELEGRAM_API_HASH', '').strip() or getpass.getpass('api_hash: ').strip()
    if not api_id.isdigit() or int(api_id) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
        raise ValueError('Некорректный api_id или api_hash.')
    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        me = await client.get_me()
        if me.bot:
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


def main():
    if os.environ.get('GITHUB_ACTIONS') == 'true' or not sys.stdin.isatty():
        print('Первый вход выполняется в интерактивном терминале Codespaces, не в Actions.', file=sys.stderr)
        return 2
    os.umask(0o077)
    try:
        asyncio.run(login())
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'Вход не завершён ({type(exc).__name__}). Секреты не выводятся.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
