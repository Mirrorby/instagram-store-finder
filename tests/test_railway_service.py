import json
import io
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from store_finder.railway_service import Conflict, Queue, handler, normalize
from store_finder.railway_job import run


class RailwayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Queue(self.temp.name)
        self.request = normalize({'instagram': 'https://www.instagram.com/shop.name/?igsh=123'})

    def test_input_boundaries(self):
        self.assertEqual(self.request['username'], 'shop.name')
        for value in ['https://instagram.com.evil.test/shop', 'http://instagram.com/shop',
                      'https://instagram.com/p/123', 'https://u:p@instagram.com/shop',
                      'https://instagram.com:443/shop', 'https://127.0.0.1/shop', '../shop']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize({'instagram': value})
        for extra in [{'products': True}, {'products': 7}, {'theme': []}, {'currency': 'KGS'}, {'url': 'x'}]:
            with self.subTest(extra=extra), self.assertRaises((ValueError, TypeError)):
                normalize({'instagram': 'shop', **extra})

    def test_concurrent_idempotency_and_conflict(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: self.queue.enqueue('same_request_key', self.request), range(8)))
        self.assertEqual(len(set(ids)), 1)
        with self.assertRaises(Conflict):
            self.queue.enqueue('same_request_key', normalize({'instagram': 'other'}))

    def test_persistence_and_explicit_retry(self):
        job_id = self.queue.enqueue('a_request_key_123', self.request)
        row = self.queue.take()
        self.assertEqual(row['id'], job_id)
        self.assertIsNone(self.queue.take())
        self.queue.finish(job_id, {'status': 'failed', 'error': 'import_timeout'})
        reopened = Queue(self.temp.name)
        self.assertEqual(reopened.get(job_id)['status'], 'failed')
        reopened.retry(job_id)
        self.assertEqual(reopened.take()['id'], job_id)
        reopened.finish(job_id, {'status': 'ready', 'store': {'id': 'store'}})
        with self.assertRaises(Conflict):
            reopened.retry(job_id)

    def test_queue_limit(self):
        for index in range(20):
            self.queue.enqueue('request_key_%08d' % index, self.request)
        with self.assertRaises(Conflict):
            self.queue.enqueue('request_key_overflow', self.request)

    def test_http_auth_and_job_polling(self):
        def call(method, path, data=None, auth=True, raw=False):
            cls = handler(self.queue, 't' * 32)
            instance = cls.__new__(cls)
            instance.path = path
            instance.request_version = 'HTTP/1.1'
            instance.command = method
            instance.requestline = method + ' ' + path + ' HTTP/1.1'
            instance.headers = Message()
            instance.headers['Idempotency-Key'] = 'http_request_key_123'
            body = json.dumps(data).encode() if data is not None else b''
            instance.headers['Content-Length'] = str(len(body))
            if auth:
                instance.headers['Authorization'] = 'Bearer ' + 't' * 32
            instance.rfile, instance.wfile = io.BytesIO(body), io.BytesIO()
            getattr(instance, 'do_' + method)()
            header, response = instance.wfile.getvalue().split(b'\r\n\r\n', 1)
            return int(header.split()[1]), response if raw else json.loads(response)

        self.assertEqual(call('GET', '/health', auth=False)[0], 200)
        self.assertEqual(call('POST', '/v1/jobs', {'instagram': 'shop'}, auth=False)[0], 401)
        status, job = call('POST', '/v1/jobs', {'instagram': 'shop'})
        self.assertEqual(status, 202)
        self.assertEqual(call('GET', '/v1/jobs/' + job['id'])[1]['status'], 'queued')
        self.assertEqual(call('POST', '/v1/jobs', {'instagram': 'other'})[0], 409)
        self.assertEqual(call('POST', '/v1/jobs', ['invalid'])[0], 400)
        self.assertEqual(call('GET', '/v1/jobs/' + '0' * 32)[0], 404)
        asset_dir=Path(self.temp.name) / 'jobs' / job['id'] / 'assets'
        asset_dir.mkdir(parents=True)
        filename='f'*64+'.jpg'
        data=b'\xff\xd8\xffphoto'
        (asset_dir / filename).write_bytes(data)
        url='/v1/jobs/'+job['id']+'/assets/'+filename
        self.assertEqual(call('GET',url,raw=True),(200,data))
        self.assertEqual(call('GET',url,auth=False)[0],401)
        self.assertEqual(call('GET','/v1/jobs/'+job['id']+'/assets/../../request.json')[0],404)


    def test_terminal_files_expire_without_losing_statistics_or_active_jobs(self):
        import time
        job_id=self.queue.enqueue('retention_request_123',self.request)
        self.queue.take()
        directory=Path(self.temp.name)/'jobs'/job_id
        directory.mkdir()
        (directory/'source.json').write_text('heavy private source')
        self.queue.finish(job_id,{'status':'ready','products':71,'profile':{'description':'raw text'}})
        active=self.queue.enqueue('retention_active_1234',self.request)
        self.queue.take()
        future=time.time()+8*86400
        self.assertEqual(self.queue.cleanup(now=future),1)
        self.assertFalse(directory.exists())
        saved=self.queue.get(job_id)
        self.assertEqual(saved['products'],71)
        self.assertTrue(saved['archived'])
        self.assertNotIn('profile',saved)
        self.assertEqual(self.queue.get(active)['status'],'running')
        self.assertEqual(self.queue.cleanup(now=future),0)

    def test_child_reuses_checkpoint_without_browser(self):
        directory = Path(self.temp.name) / 'jobs' / ('a' * 32)
        directory.mkdir()
        (directory / 'request.json').write_text(json.dumps(self.request))
        (directory / 'bundle.json').write_text(json.dumps({'issues': [], 'store': {'products': [1]}}))
        with patch('store_finder.railway_job.BrowserProfiles') as browser, \
             patch.dict(os.environ, {'STORE_API_ORIGIN': 'https://demo.test', 'PLATFORM_API_KEY': 'k' * 32}), \
             patch('store_finder.railway_job.PlatformAPI'), \
             patch('store_finder.railway_job.publish', return_value={'store': {'id': 'id', 'url': 'https://demo.test'}}):
            run(directory)
        browser.assert_not_called()
        self.assertEqual(json.loads((directory / 'result.json').read_text())['status'], 'ready')

    def test_child_redacts_remote_errors(self):
        directory = Path(self.temp.name) / 'jobs' / ('b' * 32)
        directory.mkdir()
        (directory / 'request.json').write_text(json.dumps(self.request))
        with patch('store_finder.railway_job.BrowserProfiles', side_effect=ValueError('SECRET cookie')):
            run(directory)
        result = (directory / 'result.json').read_text()
        self.assertNotIn('SECRET', result)
        self.assertEqual(json.loads(result)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
