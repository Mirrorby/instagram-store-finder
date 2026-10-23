"""Internal authenticated import API and durable single-browser queue.

Deploy ONE replica with a Railway Volume mounted at DATA_DIR. Only the trusted
Delokay backend may hold IMPORT_API_TOKEN; this API is not a public Mini App API.
"""
import fcntl
import hashlib
import hmac
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class Conflict(ValueError):
    pass


def normalize(data):
    if isinstance(data, dict) and data.get('kind') == 'website':
        from .reference_site import public_url
        if set(data) - {'kind', 'url'}:
            raise ValueError('invalid_request')
        return {'kind': 'website', 'url': public_url(data.get('url'))}
    if not isinstance(data, dict) or set(data) - {'instagram', 'theme', 'language', 'currency', 'products', 'periodDays'}:
        raise ValueError('invalid_request')
    value = data.get('instagram', '')
    if not isinstance(value, str):
        raise ValueError('invalid_instagram')
    value = value.strip()
    if '://' in value:
        url = urlparse(value)
        if url.scheme != 'https' or url.hostname not in {'instagram.com', 'www.instagram.com'} or url.username or url.password or url.port:
            raise ValueError('invalid_instagram')
        value = url.path.strip('/')
    value = value.lstrip('@').lower()
    if not re.fullmatch(r'[a-z0-9_](?:[a-z0-9_.]{0,28}[a-z0-9_])?', value) or value in {'p', 'reel', 'reels', 'stories', 'accounts', 'explore'}:
        raise ValueError('invalid_instagram')
    result = {'username': value, 'theme': data.get('theme', 'minimal'),
              'language': data.get('language', 'auto'), 'currency': data.get('currency', 'USD'),
              'products': data.get('products', 6)}
    if result['theme'] not in ('minimal', 'fashion', 'home', 'beauty', 'auto'):
        raise ValueError('invalid_theme')
    if result['language'] not in ('auto', 'ru', 'en'):
        raise ValueError('invalid_language')
    if result['currency'] not in ('USD', 'EUR', 'BYN', 'RUB', 'KZT'):
        raise ValueError('invalid_currency')
    if type(result['products']) is not int or not 1 <= result['products'] <= 6:
        raise ValueError('invalid_products')
    if 'periodDays' in data:
        days = data['periodDays']
        if type(days) is not int or not 1 <= days <= 90:
            raise ValueError('invalid_period')
        result['period_days'] = days
        result['products'] = 500
    return result


class Queue:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / 'jobs').mkdir(exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                request TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, created REAL NOT NULL)''')

    def connect(self):
        db = sqlite3.connect(self.root / 'queue.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, key, request):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', key):
            raise ValueError('invalid_idempotency_key')
        encoded = json.dumps(request, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM jobs WHERE request_key=?', (key,)).fetchone()
            if old:
                if old['request'] != encoded:
                    raise Conflict('idempotency_conflict')
                return old['id']
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                raise Conflict('queue_full')
            job_id = uuid.uuid4().hex
            db.execute('INSERT INTO jobs VALUES(?,?,?,\'queued\',NULL,?)',
                       (job_id, key, encoded, time.time()))
        return job_id

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return None
        return {'id': row['id'], 'status': row['status'], 'createdAt': row['created'],
                **(json.loads(row['result']) if row['result'] else {})}

    def take(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (row['id'],))
        return row

    def finish(self, job_id, result):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,result=? WHERE id=?',
                       (result['status'], json.dumps(result), job_id))

    def retry(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('job_not_found')
            if row['status'] not in ('failed', 'interrupted'):
                raise Conflict('job_not_retryable')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                raise Conflict('queue_full')
            db.execute("UPDATE jobs SET status='queued',result=NULL WHERE id=?", (job_id,))


def execute_job(queue, row, timeout=2400):
    from .personal_demos import save
    directory = queue.root / 'jobs' / row['id']
    directory.mkdir(exist_ok=True, mode=0o700)
    save(directory / 'request.json', json.loads(row['request']))
    result_path = directory / 'result.json'
    result_path.unlink(missing_ok=True)
    child = subprocess.Popen([sys.executable, '-m', 'store_finder.railway_job', str(directory)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        return {'status': 'failed', 'error': 'import_timeout'}
    finally:
        # Chromium descendants must not survive a failed Python child.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if child.returncode != 0 or not result_path.exists():
        return {'status': 'failed', 'error': 'import_process_failed'}
    result = json.loads(result_path.read_text())
    if result.get('status') not in {'ready', 'needs_review', 'restricted', 'failed'}:
        raise ValueError('invalid_job_result')
    return result


def worker(queue, stop):
    while not stop.is_set():
        row = queue.take()
        if not row:
            stop.wait(2)
            continue
        try:
            result = execute_job(queue, row)
        except Exception:
            result = {'status': 'failed', 'error': 'worker_error'}
        queue.finish(row['id'], result)
        print(json.dumps({'job': row['id'], 'status': result['status']}), flush=True)


def handler(queue, token):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_):
            pass

        def send_json(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            supplied = self.headers.get('Authorization', '')
            return hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),
                                       hashlib.sha256(('Bearer ' + token).encode()).digest())

        def do_GET(self):
            if self.path == '/health':
                try:
                    with queue.connect() as db:
                        db.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
                    return self.send_json(200, {'ok': True})
                except sqlite3.Error:
                    return self.send_json(503, {'ok': False})
            if not self.authorized():
                return self.send_json(401, {'error': 'unauthorized'})
            artifact = re.fullmatch(r'/v1/jobs/([a-f0-9]{32})/bundle', self.path)
            if artifact:
                path = queue.root / 'jobs' / artifact[1] / 'bundle.json'
                if not path.is_file():
                    return self.send_json(404, {'error': 'not_found'})
                if path.stat().st_size > 20_000_000:
                    return self.send_json(413, {'error': 'artifact_too_large'})
                return self.send_json(200, json.loads(path.read_text()))
            match = re.fullmatch(r'/v1/jobs/([a-f0-9]{32})', self.path)
            job = queue.get(match[1]) if match else None
            return self.send_json(200 if job else 404, job or {'error': 'not_found'})

        def do_POST(self):
            if not self.authorized():
                return self.send_json(401, {'error': 'unauthorized'})
            try:
                match = re.fullmatch(r'/v1/jobs/([a-f0-9]{32})/retry', self.path)
                if match:
                    queue.retry(match[1])
                    return self.send_json(202, queue.get(match[1]))
                if self.path != '/v1/jobs':
                    return self.send_json(404, {'error': 'not_found'})
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('unsupported_transfer_encoding')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('invalid_body_size')
                data = normalize(json.loads(self.rfile.read(length)))
                job_id = queue.enqueue(self.headers.get('Idempotency-Key', ''), data)
                return self.send_json(202, queue.get(job_id))
            except Conflict as exc:
                return self.send_json(409, {'error': str(exc)})
            except (ValueError, TypeError):
                return self.send_json(400, {'error': 'invalid_request'})
    return Handler


def main():
    os.umask(0o077)
    token = os.environ.get('IMPORT_API_TOKEN', '')
    if len(token) < 32:
        raise SystemExit('Set IMPORT_API_TOKEN to at least 32 characters')
    from .personal_demos import PlatformAPI, private_write
    if os.environ.get('IMPORT_PUBLISH_ENABLED', '1') != '0':
        PlatformAPI(os.environ.get('STORE_API_ORIGIN', ''), os.environ.get('PLATFORM_API_KEY', ''))
    root = Path(os.environ.get('DATA_DIR', '/data')).resolve()
    queue = Queue(root)
    lock = (root / 'service.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # A restart is visible, never silently rerun a possibly completed import.
    with queue.connect() as db:
        db.execute("UPDATE jobs SET status='interrupted',result=? WHERE status='running'",
                   (json.dumps({'status': 'interrupted', 'error': 'service_restarted'}),))
    session = os.environ.get('INSTAGRAM_STORAGE_STATE_JSON')
    if session:
        parsed = json.loads(session)
        if not isinstance(parsed, dict) or not isinstance(parsed.get('cookies'), list):
            raise SystemExit('Invalid INSTAGRAM_STORAGE_STATE_JSON')
        private_write(root / 'instagram-session.json', json.dumps(parsed))
    else:
        (root / 'instagram-session.json').unlink(missing_ok=True)
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(queue, stop), daemon=True)
    thread.start()
    # Exit if the queue thread dies, so Railway can restart the service.
    def watch():
        thread.join()
        os._exit(1)
    threading.Thread(target=watch, daemon=True).start()
    callback = os.environ.get('DELOKAY_CALLBACK_ORIGIN', '')
    if callback:
        parsed_callback = urlparse(callback)
        if callback != 'https://catalog-factory-staging.nikita-karpof.workers.dev':
            raise SystemExit('Invalid DELOKAY_CALLBACK_ORIGIN')
        def pump():
            import requests
            while not stop.is_set():
                try:
                    response = requests.post(callback + '/api/import/tick',
                        headers={'Authorization': 'Bearer ' + token},
                        json={}, timeout=90, allow_redirects=False)
                    response.close()
                except requests.RequestException:
                    pass
                stop.wait(10)
        threading.Thread(target=pump, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), handler(queue, token)).serve_forever()


if __name__ == '__main__':
    main()
