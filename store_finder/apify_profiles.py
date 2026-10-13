"""One bounded Apify profile-scraper run. Never retries a billable POST."""
import math
import re
import time
import requests

BASE = 'https://api.apify.com/v2'
ACTOR = 'apify~instagram-profile-scraper'
TERMINAL = {'SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'}


class ApifyError(RuntimeError):
    pass


class ApifyProfiles:
    def __init__(self, token, max_charge=0.10, timeout=300):
        if not token: raise ValueError('Добавьте APIFY_API_TOKEN в GitHub Secrets.')
        if not math.isfinite(max_charge) or not 0 < max_charge <= 0.10 or not 30 <= timeout <= 300:
            raise ValueError('Лимит Apify: до $0.10 за запуск, время: 30–300 секунд.')
        self.session = requests.Session()
        self.session.headers.update({'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
        self.max_charge, self.timeout = max_charge, timeout
        self.free_plan_confirmed = False

    def ensure_free(self):
        if self.free_plan_confirmed: return
        data = self.request('GET', '/users/me').get('data', {})
        plan = data.get('plan') or {}
        if plan.get('tier') != 'FREE' or plan.get('monthlyBasePriceUsd') != 0 or data.get('isPaying') is not False:
            raise ApifyError('Облачная проверка разрешена только на Apify Free. Платный или неизвестный план: запуск остановлен.')
        self.free_plan_confirmed = True

    def request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, BASE + path, timeout=40, allow_redirects=False, **kwargs)
            if not 200 <= response.status_code < 300:
                raise ApifyError('Apify HTTP ' + str(response.status_code))
            if len(response.content) > 12_000_000:
                raise ApifyError('Apify response exceeds size limit')
            return response.json()
        except (requests.RequestException, ValueError):
            # Remote text/URLs may include credentials; do not expose them.
            raise ApifyError('Apify request failed; the run-start request was not retried') from None

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9]{5,40}', value):
            raise ApifyError('Invalid Apify run/dataset identifier')
        return value

    def fetch(self, usernames):
        if not 1 <= len(usernames) <= 20 or len(set(usernames)) != len(usernames):
            raise ValueError('Облачная партия: от 1 до 20 разных аккаунтов.')
        if any(not re.fullmatch(r'[a-zA-Z0-9_.]{1,30}', name) for name in usernames):
            raise ValueError('Некорректное имя аккаунта Instagram.')
        self.ensure_free()
        started = time.monotonic()
        data = self.request('POST', f'/acts/{ACTOR}/runs',
                            params={'timeout': self.timeout, 'maxTotalChargeUsd': self.max_charge, 'restartOnError': 'false'},
                            json={'usernames': usernames, 'includeAboutSection': False})
        run = data.get('data', {})
        run_id = self.identifier(run.get('id'))
        while run.get('status') not in TERMINAL:
            if time.monotonic() - started >= self.timeout + 30:
                try: self.request('POST', f'/actor-runs/{run_id}/abort')
                except ApifyError: pass
                raise ApifyError('Apify run exceeded the polling time budget')
            time.sleep(5)
            run = self.request('GET', f'/actor-runs/{run_id}', params={'waitForFinish': 30}).get('data', {})
        dataset = self.identifier(run.get('defaultDatasetId'))
        items = self.request('GET', f'/datasets/{dataset}/items', params={'clean': 'true', 'limit': len(usernames), 'format': 'json'})
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ApifyError('Unexpected Apify dataset format')
        summary = {'status': run.get('status'), 'returned': len(items), 'max_charge_usd': self.max_charge}
        charge = run.get('usageTotalUsd')
        if isinstance(charge, (int, float)) and math.isfinite(charge): summary['usage_usd'] = charge
        return items, summary
