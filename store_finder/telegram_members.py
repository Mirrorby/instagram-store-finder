"""Read accessible Telegram group members into separate Google Sheets tabs.

Run from the repository root: python -m store_finder.telegram_members @group
No messages, invitations, joining groups or contact imports are performed.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlparse

HEADERS = [
    'Telegram ID', 'Имя', 'Username', 'Ссылка на профиль', 'Доступ к профилю',
    'Бот', 'Удалённый аккаунт', 'ID группы', 'Ссылка на группу',
    'Впервые найден', 'Последний сбор', 'Полнота последнего сбора',
    'Статус', 'Что продаёт', 'Дата первого сообщения', 'Следующий контакт',
    'Ссылка на демо', 'Заметки',
]
STATUSES = ['Новый', 'Написал', 'Ответил', 'Интерес', 'Демо', 'Оплатил', 'Отказ', 'Не подходит']
MARKER = 'delokay_telegram_group_id'
AUTH = Path('.auth')


def group_reference(value):
    value = value.strip()
    if value.startswith('@'):
        value = value[1:]
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', value):
        return '@' + value
    parsed = urlparse(value if '://' in value else 'https://' + value)
    parts = parsed.path.strip('/').split('/')
    if parsed.scheme != 'https' or parsed.netloc.lower() not in {'t.me', 'telegram.me'}:
        raise ValueError('Укажите @username или ссылку https://t.me/имя_группы.')
    if len(parts) == 1 and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', parts[0]):
        return '@' + parts[0]
    # Resolve an invite only if the account is already a member; never join.
    if ((len(parts) == 1 and re.fullmatch(r'\+[\w-]+', parts[0])) or
            (len(parts) == 2 and parts[0] == 'joinchat' and re.fullmatch(r'[\w-]+', parts[1]))):
        return 'https://t.me/' + '/'.join(parts)
    raise ValueError('Нужна ссылка на саму группу, без номера сообщения или темы.')


def tab_title(title, group_id, occupied):
    base = re.sub(r'[\[\]:*?/\\\x00-\x1f]', ' ', title).strip().strip("'") or 'Telegram'
    base = base[:100]
    if base.casefold() not in {x.casefold() for x in occupied}:
        return base
    suffix = ' · ' + str(group_id)
    candidate = base[:100-len(suffix)] + suffix
    n = 2
    while candidate.casefold() in {x.casefold() for x in occupied}:
        extra = suffix + '-' + str(n)
        candidate = base[:100-len(extra)] + extra
        n += 1
    return candidate


def plan_rows(existing, members, group_id, group_url, now, coverage):
    """Only A:L is automatic; M:R belongs exclusively to the operator."""
    by_id = {}
    for number, row in enumerate(existing, 2):
        if not row or not row[0]:
            if any(row):
                raise ValueError('Есть строка без Telegram ID. Исправьте её перед обновлением.')
            continue
        key = str(row[0])
        if key in by_id:
            raise ValueError('В листе повторяется Telegram ID; обновление остановлено.')
        if len(row) < 8 or str(row[7]) != str(group_id):
            raise ValueError('В листе есть строка другой группы; обновление остановлено.')
        by_id[key] = (number, row)
    updates, additions, seen = [], [], set()
    for member in members:
        key = str(member['id'])
        if key in seen:
            continue
        seen.add(key)
        username = member.get('username') or ''
        deleted = member.get('deleted', False)
        link = 'https://t.me/' + username if username and not deleted else ''
        access = 'Открыть по ссылке' if link else ('Аккаунт удалён' if deleted else 'Нет username — переход через список участников')
        values = [key, member.get('name', ''), '@' + username if username else '', link,
                  access, 'Да' if member.get('bot') else 'Нет', 'Да' if deleted else 'Нет',
                  str(group_id), group_url, now, now, coverage]
        if key in by_id:
            number, previous = by_id[key]
            values[9] = previous[9] if len(previous) > 9 and previous[9] else now
            updates.append({'range': f'A{number}:L{number}', 'values': [values]})
        else:
            additions.append(values + ['Новый', '', '', '', '', ''])
    if additions:
        start = len(existing) + 2
        updates.append({'range': f'A{start}:R{start+len(additions)-1}', 'values': additions})
    return updates, len(seen) - len(additions), len(additions)


def open_book(credentials_path, spreadsheet_id):
    import gspread
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    raw = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
    client = (gspread.service_account_from_dict(json.loads(raw), scopes=scopes) if raw else
              gspread.service_account(filename=str(credentials_path), scopes=scopes))
    return client.open_by_key(spreadsheet_id)


def group_sheet(book, group_id, title):
    metadata = book.fetch_sheet_metadata(params={'fields': 'sheets(properties,developerMetadata)'})
    sheets = metadata.get('sheets', [])
    matches = [s for s in sheets if any(m.get('metadataKey') == MARKER and
               m.get('metadataValue') == str(group_id) for m in s.get('developerMetadata', []))]
    if len(matches) > 1:
        raise ValueError('Несколько листов привязаны к этой группе; исправьте дубликат.')
    occupied = [s['properties']['title'] for s in sheets]
    if matches:
        props = matches[0]['properties']
        occupied.remove(props['title'])
        new_title = tab_title(title, group_id, occupied)
        sheet = book.get_worksheet_by_id(props['sheetId'])
        if new_title != props['title']:
            sheet.update_title(new_title)
        return sheet
    used = {s['properties']['sheetId'] for s in sheets}
    sheet_id = secrets.randbelow(2**31-1)
    while sheet_id in used:
        sheet_id = secrets.randbelow(2**31-1)
    book.batch_update({'requests': [
        {'addSheet': {'properties': {'sheetId': sheet_id,
          'title': tab_title(title, group_id, occupied),
          'gridProperties': {'rowCount': 1000, 'columnCount': len(HEADERS), 'frozenRowCount': 1}}}},
        {'createDeveloperMetadata': {'developerMetadata': {
          'metadataKey': MARKER, 'metadataValue': str(group_id),
          'location': {'sheetId': sheet_id}, 'visibility': 'DOCUMENT'}}},
    ]})
    return book.get_worksheet_by_id(sheet_id)


def sync_sheet(book, group_id, title, group_url, members, now, coverage):
    sheet = group_sheet(book, group_id, title)
    header_result = sheet.get('A1:R1')
    # gspread can return [[]] for an empty range; the outer list is truthy.
    header = list(header_result[0]) if header_result else []
    if not any(value != '' for value in header):
        header = []
    if header and header != HEADERS:
        raise ValueError('Заголовки Telegram-листа изменены. Запись остановлена.')
    existing = []
    for start in range(2, sheet.row_count + 1, 1000):
        end = min(start + 999, sheet.row_count)
        block = sheet.get(f'A{start}:R{end}', value_render_option='UNFORMATTED_VALUE')
        existing.extend(list(row) for row in block)
        existing.extend([[] for _ in range(end-start+1-len(block))])
    while existing and not existing[-1]:
        existing.pop()
    if not header and any(any(value != '' for value in row) for row in existing):
        raise ValueError('Лист содержит данные без заголовков. Запись остановлена.')
    updates, updated, added = plan_rows(existing, members, group_id, group_url, now, coverage)
    needed = len(existing) + added + 1
    if needed > sheet.row_count:
        sheet.resize(rows=needed + 100)
    if not header:
        sheet.update(range_name='A1:R1', values=[HEADERS], value_input_option='RAW')
    # RAW prevents names such as =IMPORTXML(...) from becoming formulas.
    for offset in range(0, len(updates), 200):
        sheet.batch_update(updates[offset:offset+200], value_input_option='RAW')
    end = max(needed, 2)
    book.batch_update({'requests': [
        {'updateSheetProperties': {'properties': {'sheetId': sheet.id,
         'gridProperties': {'frozenRowCount': 1}}, 'fields': 'gridProperties.frozenRowCount'}},
        {'setBasicFilter': {'filter': {'range': {'sheetId': sheet.id,
         'startRowIndex': 0, 'endRowIndex': end, 'startColumnIndex': 0, 'endColumnIndex': 18}}}},
        {'repeatCell': {'range': {'sheetId': sheet.id, 'startRowIndex': 0, 'endRowIndex': 1},
         'cell': {'userEnteredFormat': {'backgroundColor': {'red': .345, 'green': .396, 'blue': .91},
          'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}}},
         'fields': 'userEnteredFormat'}},
        {'setDataValidation': {'range': {'sheetId': sheet.id, 'startRowIndex': 1,
          'endRowIndex': sheet.row_count, 'startColumnIndex': 12, 'endColumnIndex': 13},
          'rule': {'condition': {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': x} for x in STATUSES]},
                   'strict': True, 'showCustomUi': True}}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet.id, 'dimension': 'COLUMNS',
         'startIndex': 0, 'endIndex': 18}, 'properties': {'pixelSize': 180}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet.id, 'dimension': 'COLUMNS',
         'startIndex': 17, 'endIndex': 18}, 'properties': {'pixelSize': 320}, 'fields': 'pixelSize'}},
    ]})
    # Verify the identity and header; user fields are never included in updates to old rows.
    if sheet.get('A1:R1')[0] != HEADERS:
        raise ValueError('Не удалось подтвердить заголовки после записи.')
    return {'updated': updated, 'added': added,
            'url': f'https://docs.google.com/spreadsheets/d/{book.id}/edit#gid={sheet.id}'}


async def collect(client, reference):
    from telethon import functions, types, utils
    if reference.startswith('https://t.me/'):
        invite = reference.rsplit('/', 1)[-1].lstrip('+')
        checked = await client(functions.messages.CheckChatInviteRequest(invite))
        if not isinstance(checked, types.ChatInviteAlready):
            raise ValueError('Сначала вступите в эту группу вручную в Telegram, затем повторите сбор.')
        entity = checked.chat
    else:
        entity = await client.get_entity(reference)
    if not isinstance(entity, (types.Chat, types.Channel)) or (
            isinstance(entity, types.Channel) and not entity.megagroup):
        raise ValueError('Ссылка должна вести на группу, а не на пользователя или канал.')
    expected = None
    if isinstance(entity, types.Channel):
        full = await client(functions.channels.GetFullChannelRequest(entity))
        expected = getattr(full.full_chat, 'participants_count', None)
    else:
        expected = getattr(entity, 'participants_count', None)
    members = {}
    async for user in client.iter_participants(entity):
        members[user.id] = {'id': user.id,
          'name': ' '.join(x for x in [user.first_name, user.last_name] if x),
          'username': user.username or '', 'bot': bool(user.bot), 'deleted': bool(user.deleted)}
    if not members:
        raise ValueError('Telegram не вернул участников. Таблица не изменена; проверьте доступ к списку.')
    actual = len(members)
    coverage = (f'Получено {actual} из {expected}; состав мог измениться' if expected is not None else
                f'Получено {actual}; общий размер неизвестен')
    if expected is None or actual < expected:
        coverage = 'Неполная или неподтверждённая выборка. ' + coverage
    group_url = 'https://t.me/' + entity.username if getattr(entity, 'username', None) else ''
    return utils.get_peer_id(entity), entity.title, group_url, list(members.values()), coverage


def settings(args):
    if args.non_interactive:
        required = ['TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_SESSION', 'GOOGLE_SERVICE_ACCOUNT_JSON']
        missing = [name for name in required if not os.environ.get(name, '').strip()]
        if missing:
            raise ValueError('Добавьте GitHub Secrets: ' + ', '.join(missing))
        api_id = os.environ['TELEGRAM_API_ID'].strip()
        api_hash = os.environ['TELEGRAM_API_HASH'].strip()
        if not api_id.isdigit() or int(api_id) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
            raise ValueError('Некорректный TELEGRAM_API_ID или TELEGRAM_API_HASH.')
        from .sheets import configured_sheet
        sheet_id = args.spreadsheet or os.environ.get('GOOGLE_SHEET_ID', '').strip() or configured_sheet()
        if not sheet_id:
            raise ValueError('Не настроен ID Google-таблицы.')
        return int(api_id), api_hash, None, sheet_id
    AUTH.mkdir(mode=0o700, exist_ok=True)
    os.chmod(AUTH, 0o700)
    path = AUTH / 'telegram-config.json'
    config = json.loads(path.read_text()) if path.exists() else {}
    api_id = os.environ.get('TELEGRAM_API_ID') or config.get('api_id')
    api_hash = os.environ.get('TELEGRAM_API_HASH') or config.get('api_hash')
    if not api_id:
        api_id = input('Telegram api_id (my.telegram.org → API development tools): ').strip()
    if not api_hash:
        api_hash = getpass.getpass('Telegram api_hash: ').strip()
    if not str(api_id).isdigit() or int(api_id) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash or ''):
        raise ValueError('Некорректный api_id или api_hash.')
    credentials = Path(args.credentials or config.get('credentials') or '.auth/google-service-account.json').expanduser()
    if not os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON') and not credentials.is_file():
        credentials = Path(input('Путь к JSON-ключу Google сервисного аккаунта: ').strip().strip('"\'')).expanduser()
        if not credentials.is_file():
            raise ValueError('JSON-ключ Google не найден.')
    from .sheets import configured_sheet
    sheet_id = args.spreadsheet or os.environ.get('GOOGLE_SHEET_ID') or configured_sheet()
    if not sheet_id:
        raise ValueError('Не настроен ID Google-таблицы.')
    saved = {'api_id': int(api_id), 'api_hash': api_hash, 'credentials': str(credentials.resolve())}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(saved, handle)
    os.chmod(path, 0o600)
    return int(api_id), api_hash, credentials, sheet_id


async def run(args):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import ChatAdminRequiredError, FloodWaitError, RPCError
    references = [group_reference(value) for value in args.groups]
    api_id, api_hash, credentials, sheet_id = settings(args)
    # Check Sheets credentials before asking for a Telegram login.
    book = open_book(credentials, sheet_id)
    session = (StringSession(os.environ['TELEGRAM_SESSION'].strip()) if args.non_interactive else
               str(AUTH / 'telegram-members'))
    client = TelegramClient(session, api_id, api_hash,
                            flood_sleep_threshold=0)
    failed = False
    try:
        if args.non_interactive:
            await client.connect()
            if not await client.is_user_authorized():
                raise ValueError('Сессия Telegram отозвана или истекла. Создайте новую и обновите TELEGRAM_SESSION.')
            me = await client.get_me()
            if me.bot:
                raise ValueError('Нужна сессия пользовательского аккаунта Telegram, не бота.')
        else:
            await client.start()
        for reference in references:
            try:
                group_id, title, url, members, coverage = await collect(client, reference)
                print(coverage)
                result = sync_sheet(book, group_id, title, url, members,
                  datetime.now(timezone(timedelta(hours=3))).strftime('%Y-%m-%d %H:%M:%S'), coverage)
                print(f"Добавлено: {result['added']}; обновлено: {result['updated']}\n{result['url']}")
                if os.environ.get('GITHUB_STEP_SUMMARY'):
                    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
                        handle.write(f"\nПолучено: {len(members)}. Добавлено: {result['added']}; "
                                     f"обновлено: {result['updated']}.\n\n{coverage}\n\n"
                                     f"[Открыть лист]({result['url']})\n")
            except FloodWaitError as exc:
                print(f'Telegram просит подождать {exc.seconds} сек. Сбор остановлен; повторите позже.', file=sys.stderr)
                return 2
            except ChatAdminRequiredError:
                failed = True
                print('Список участников недоступен этому аккаунту: нужны права администратора.', file=sys.stderr)
            except RPCError as exc:
                failed = True
                print(f'Telegram отклонил запрос ({type(exc).__name__}); этот чат не записан.', file=sys.stderr)
            except ValueError as exc:
                failed = True
                print(str(exc), file=sys.stderr)
    finally:
        await client.disconnect()
        if not args.non_interactive:
            for session_file in AUTH.glob('telegram-members.session*'):
                os.chmod(session_file, 0o600)
    return 2 if failed else 0


def main():
    parser = argparse.ArgumentParser(description='Участники Telegram-групп → отдельные листы существующей Google-таблицы')
    parser.add_argument('groups', nargs='*', help='@username или https://t.me/группа; можно несколько')
    parser.add_argument('--credentials', help='Путь к JSON-ключу Google (не содержимое ключа)')
    parser.add_argument('--spreadsheet', help='ID другой Google-таблицы')
    parser.add_argument('--non-interactive', action='store_true',
                        help='GitHub Actions: только Secrets, без ввода и файлов сессии')
    args = parser.parse_args()
    args.non_interactive = args.non_interactive or os.environ.get('GITHUB_ACTIONS') == 'true'
    if not args.groups and args.non_interactive:
        args.groups = [x for x in re.split(r'[\s,]+', os.environ.get('TELEGRAM_GROUPS', '').strip()) if x]
    if not args.groups and not args.non_interactive:
        args.groups = input('Ссылки на группы через пробел: ').split()
    if not args.groups:
        parser.error('Укажите хотя бы одну группу.')
    if len(args.groups) > 20:
        parser.error('Не более 20 групп за один запуск.')
    if args.non_interactive:
        try:
            return asyncio.run(run(args))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except Exception as exc:
            print(f'Ошибка ({type(exc).__name__}). Проверьте Secrets и доступ аккаунта.', file=sys.stderr)
            return 2
    # Session keys must never be accessible to other local users.
    os.umask(0o077)
    import fcntl
    AUTH.mkdir(mode=0o700, exist_ok=True)
    with (AUTH / 'telegram-members.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Сбор уже запущен в другом окне.', file=sys.stderr)
            return 2
        try:
            return asyncio.run(run(args))
        except (ValueError, FileNotFoundError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print('\nСбор остановлен.')
            return 130
        except Exception as exc:
            # Remote exceptions may contain credentials, invite links or phone numbers.
            print(f'Ошибка ({type(exc).__name__}). Проверьте доступ Telegram и Google; секреты не выводятся.', file=sys.stderr)
            return 2


if __name__ == '__main__':
    raise SystemExit(main())
