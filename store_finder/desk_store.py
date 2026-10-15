"""Private SQLite workspace for the local store desk. No browser/API credentials."""
import csv
import io
import json
import os
import re
import sqlite3
from pathlib import Path
from .models import now, profile_username

STAGES = {'queued', 'checking', 'review', 'collected', 'ready', 'error', 'restricted'}
CONTACT_STATUSES = {'Новый', 'Написал', 'Ответил', 'Купил', 'Не подходит'}


class DeskStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'desk.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                  username TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                  stage TEXT NOT NULL DEFAULT 'queued', contact_status TEXT NOT NULL DEFAULT 'Новый',
                  note TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}',
                  bundle_path TEXT NOT NULL DEFAULT '', demo_url TEXT NOT NULL DEFAULT '',
                  message TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                  status TEXT NOT NULL, message TEXT NOT NULL, started_at TEXT NOT NULL,
                  finished_at TEXT);
            ''')
            db.execute("UPDATE accounts SET stage='error',error='Предыдущая обработка прервана; можно повторить' WHERE stage='checking'")
            db.execute("UPDATE jobs SET status='interrupted',finished_at=? WHERE status='running'", (now(),))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def config(self):
        with self.connect() as db:
            return {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM config')}

    def set_config(self, values):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO config VALUES (?,?)', [(k, json.dumps(v)) for k, v in values.items()])

    def get(self, username):
        with self.connect() as db:
            row = db.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
        if not row:
            raise ValueError('Аккаунт не найден')
        result = dict(row)
        result['data'] = json.loads(result['data'])
        return result

    def all(self):
        with self.connect() as db:
            names = [r[0] for r in db.execute('SELECT username FROM accounts ORDER BY created_at,username')]
        return [self.get(n) for n in names]

    def add(self, value, title='', data=None, contact_status='Новый', note='', demo_url=''):
        value = str(value).strip().lstrip('@')
        username = profile_username(value if '://' in value else 'https://www.instagram.com/' + value + '/')
        if not username:
            raise ValueError('Нужен username или ссылка на профиль Instagram')
        with self.connect() as db:
            inserted = db.execute('INSERT OR IGNORE INTO accounts (username,title,data,contact_status,note,demo_url,created_at,updated_at,stage) VALUES (?,?,?,?,?,?,?,?,?)',
                (username, title[:500], json.dumps(data or {}, ensure_ascii=False), contact_status if contact_status in CONTACT_STATUSES else 'Новый', note[:10000], demo_url, now(), now(), 'ready' if demo_url else 'queued')).rowcount
            if not inserted:
                # Only source fields update. Local notes/statuses and checked data win.
                old = self.get(username)
                merged = {**(data or {}), **old['data']}
                db.execute('UPDATE accounts SET data=?,title=CASE WHEN title=\'\' THEN ? ELSE title END WHERE username=?',
                           (json.dumps(merged, ensure_ascii=False), title[:500], username))
        return bool(inserted)

    def update(self, username, **fields):
        allowed = {'title', 'stage', 'contact_status', 'note', 'data', 'bundle_path', 'demo_url', 'message', 'error'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Недопустимые поля')
        if 'stage' in fields and fields['stage'] not in STAGES:
            raise ValueError('Неизвестный этап')
        if 'contact_status' in fields and fields['contact_status'] not in CONTACT_STATUSES:
            raise ValueError('Неизвестный статус')
        if 'data' in fields:
            fields['data'] = json.dumps(fields['data'], ensure_ascii=False)
        fields['updated_at'] = now()
        with self.connect() as db:
            db.execute('UPDATE accounts SET ' + ','.join(k + '=?' for k in fields) + ' WHERE username=?', (*fields.values(), username))

    def import_csv(self, text):
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
        if not rows or rows[0][:2] != ['Аккаунт', 'Ссылка на Instagram']:
            raise ValueError('Нужен CSV листа «Магазины» с исходными заголовками')
        from .sheets import lead_from_row
        count, invalid = 0, 0
        for values in rows[1:]:
            if not values or not values[0].strip():
                continue
            row = values + [''] * max(0, 20 - len(values))
            try:
                lead = lead_from_row(row)
                count += self.add(lead['username'], lead.get('title', ''), lead, row[16], row[19], row[18])
            except (ValueError, TypeError):
                invalid += 1
        return {'added': count, 'invalid': invalid, 'rows': len(rows) - 1}

    def scan_demos(self, project):
        # Prefer newer local bundles; do not import unrelated files or credentials.
        seen = set()
        for path in sorted((Path(project) / 'output').glob('demos*/*/bundle.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                bundle = json.loads(path.read_text())
                name = bundle['username']
                if name in seen:
                    continue
                seen.add(name)
                self.add(name, bundle['store']['name'], bundle['source'])
                old = self.get(name)
                if old['bundle_path']:
                    continue
                state = json.loads((path.parent / 'publish.json').read_text()) if (path.parent / 'publish.json').exists() else {}
                url = state.get('store', {}).get('url', '') if state.get('status') == 'ready' else ''
                message = (path.parent / 'first-contact.txt').read_text() if (path.parent / 'first-contact.txt').exists() else ''
                self.update(name, bundle_path=str(path.parent.resolve()), demo_url=url, message=message,
                            stage='ready' if url else 'review' if bundle['issues'] else 'collected', data=bundle['source'])
            except (OSError, ValueError, KeyError):
                continue

    def new_job(self, action):
        with self.connect() as db:
            return db.execute("INSERT INTO jobs(action,status,message,started_at) VALUES (?,'running','Запуск…',?)", (action, now())).lastrowid

    def job(self, job_id, message, status='running'):
        with self.connect() as db:
            db.execute('UPDATE jobs SET message=?,status=?,finished_at=? WHERE id=?',
                       (message[:2000], status, None if status == 'running' else now(), job_id))

    def jobs(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 20')]
