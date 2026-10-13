"""Manual sign-in and a bounded local diagnostic run; never writes to Sheets."""
import json
import os
import tempfile
import time
from collections import Counter
from pathlib import Path
from .browser import BrowserProfiles
from .enrichment import settings
from .models import merge_result, now, profile_username
from .network import Fetcher
from .qualification import qualify
from .scoring import score
from .sheets import LABELS
from .websites import check_websites


def save_session(context, path):
    """Create private files from the first byte, then replace the previous state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".session-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(context.storage_state(), handle)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def login(session):
    with BrowserProfiles(headless=False) as browser:
        page = browser.context.new_page()
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=60000)
        print("Войдите в Instagram в открывшемся окне. Завершите подтверждение входа, если оно появится.")
        print("Когда откроется ваша лента, вернитесь в Терминал.")
        input("Нажмите Enter после входа: ")
        cookies = browser.context.cookies("https://www.instagram.com/")
        if not any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies):
            print("Сессия входа не обнаружена. Повторите команду login и завершите вход в браузере.")
            return 2
        save_session(browser.context, session)
    print(f"Сессия сохранена: {session}. Файл содержит доступ к аккаунту; храните его только у себя.")
    return 0


def usernames(values):
    result = []
    for value in values:
        value = value.strip()
        name = profile_username(value if "://" in value else "https://www.instagram.com/" + value.lstrip("@") + "/")
        if not name:
            raise ValueError("Нужны имена аккаунтов или ссылки на профили Instagram")
        if name not in result:
            result.append(name)
    if not 1 <= len(result) <= 10:
        raise ValueError("Для локального теста укажите от 1 до 10 разных аккаунтов")
    return result


def local_test(accounts, session, output, headless=False):
    names = usernames(accounts)
    if not session.is_file():
        print("Сначала выполните: python -m store_finder login")
        return 2
    leads = {}
    query = {"query": "", "language": "", "niche": ""}
    for name in names:
        merge_result(leads, {"href": f"https://www.instagram.com/{name}/", "title": "", "body": ""}, query, now())
    fetcher, config = Fetcher(), settings()
    tested = []
    with BrowserProfiles(headless=headless, storage_state=session) as browser:
        for name in names:
            lead = leads[name]
            print(f"Проверяю @{name}…", flush=True)
            browser.fetch(lead)
            check_websites(lead, fetcher)
            score(lead)
            qualify(lead, config)
            tested.append(lead)
            followers = lead.get("followers")
            rounded = " (округлено)" if lead.get("followers_approximate") else ""
            print(f"  Подписчики: {followers if followers is not None else 'не получены'}{rounded}")
            print(f"  Последний пост: {lead.get('last_post_at') or 'дата не получена'}")
            links = ', '.join(lead.get('website_urls', []))
            print("  Ссылки: " + (links or ('не указаны' if lead.get('links_complete') else 'не получены')))
            print("  Сайт: " + LABELS['website_status'][lead.get('website_status', 'unknown')])
            print("  Chromium: " + LABELS['browser_status'].get(lead.get('browser_status'), 'Нет данных'))
            if lead.get("profile_status") == "restricted" or lead.get("browser_api_status") == 429:
                print("Instagram ограничил доступ. Тест остановлен; повторных запросов к оставшимся профилям не будет.")
                break
            time.sleep(3)
    summary = {"requested": len(names), "tested": len(tested),
        "with_followers": sum(lead.get('followers') is not None for lead in tested),
        "with_post_dates": sum(bool(lead.get('last_post_at')) for lead in tested),
        "with_confirmed_profile_links": sum(bool(lead.get('links_complete')) for lead in tested),
        "browser_results": dict(Counter(lead.get('browser_status', 'unknown') for lead in tested)),
        "sheets_written": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"summary": summary, "profiles": tested}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nИтог: " + json.dumps(summary, ensure_ascii=False))
    print(f"Подробный отчёт: {output}")
    return 0 if summary['tested'] else 2
