"""Google Sheets: A:P and U:AS automatic; Q:T owned by the user."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .models import profile_username
from .storage import empty_state

HEADERS = ["Аккаунт", "Ссылка на Instagram", "Название магазина", "Ниша (гипотеза)",
           "Результат проверки", "Оценка (0–100)", "Описание из поиска", "Сайт",
           "Подписчики", "Страна", "Язык аккаунта", "Языки запросов", "Первый раз найден",
           "Последняя проверка", "Источник", "Причины оценки", "Статус работы",
           "Контакт", "Ссылка на демо", "Комментарий"]
STATUSES = ["Новый", "Проверить", "Написал", "Ответил", "Демо готово", "Купил", "Не подходит"]
BASE_HEADERS = HEADERS[:]
DETAIL_FIELDS = [
    ("website_urls", "Ссылка на сайт"), ("website_status", "Состояние сайта"),
    ("last_post_at", "Последний пост (дата)"), ("days_since_post", "Дней без постов"),
    ("activity_status", "Активность"), ("profile_status", "Доступ к профилю"),
    ("followers_source", "Источник подписчиков"), ("activity_source", "Источник активности"),
    ("enrichment_checked_at", "Проверка профиля"), ("qualification", "Подходит для предложения?"),
    ("qualification_reason", "Причина отбора"), ("biography", "Описание профиля"),
    ("is_private", "Закрытый аккаунт"), ("website_http_status", "Код ответа сайта"),
    ("website_final_url", "Сайт после переходов"), ("website_source", "Источник сайта"),
    ("recent_posts_sample", "Постов за 90 дней (выборка)"), ("latest_post_url", "Ссылка на последний пост"),
    ("outreach_reason", "Повод для предложения"), ("followers_approximate", "Подписчики округлены"),
    ("activity_complete", "Полнота выборки постов"), ("links_complete", "Ссылки профиля проверены"),
    ("browser_status", "Результат Chromium"), ("browser_api_status", "Код ответа API Instagram"),
    ("website_checked_at", "Проверка сайта")]
HEADERS += [title for _, title in DETAIL_FIELDS]
LAST_COLUMN = "AS"
DATES = {"last_post_at", "enrichment_checked_at", "website_checked_at"}
BOOLS = {"is_private", "followers_approximate", "activity_complete", "links_complete"}
LABELS = {
    "website_status": {"unknown": "Не установлен", "missing": "Не указан", "working": "Работает", "working_hub": "Страница ссылок", "broken": "Сайт указан, не работает", "empty": "Пустая страница / заглушка", "tls_error": "Ошибка сертификата", "unreachable": "Не открылся — перепроверить", "temporary": "Временная ошибка", "restricted": "Проверка ограничена"},
    "activity_status": {"active": "Активен", "inactive": "Неактивен", "unknown": "Не подтверждена"},
    "profile_status": {"verified": "Данные получены", "partial": "Часть данных", "restricted": "Доступ ограничен", "private": "Закрыт", "unavailable": "Недоступен", "unknown": "Не проверен"},
    "qualification": {"suitable": "Подходит", "review": "Нужно проверить", "rejected": "Не подходит"},
    "browser_status": {"verified": "Данные получены", "partial": "Часть данных", "restricted": "Доступ ограничен", "error": "Ошибка загрузки", "unknown": "Нет данных"}}
SOURCES = {"profile_html": "Страница Instagram", "chromium": "Chromium / ответы страницы", "chromium_post_page": "Дата на странице публикации", "search_index": "Поисковый индекс", "indexed_post_page": "Индексированная публикация", "apify": "Apify"}


def detail_value(lead, key):
    value = lead.get(key)
    if value is None:
        return ""
    if key in DATES:
        return sheet_date(value)
    if key in BOOLS:
        return "Да" if value else "Нет"
    if key == "website_urls":
        return "\n".join(value)
    if key.endswith("_source"):
        return SOURCES.get(value, value)
    return LABELS.get(key, {}).get(value, value)
NICHES = {"jewelry": "Украшения", "clothing": "Одежда", "candles": "Свечи", "bags": "Сумки", "ceramics": "Керамика"}
REASONS = {"commerce": "Продажа и доставка", "direct_orders": "Заказы в Direct/WhatsApp",
           "physical_products": "Физические товары", "non_target": "Признаки услуг"}
LANGUAGES = {"en": "Английский", "es": "Испанский", "pt": "Португальский", "ru": "Русский",
             "fr": "Французский", "de": "Немецкий", "it": "Итальянский", "tr": "Турецкий",
             "ar": "Арабский", "id": "Индонезийский", "pl": "Польский", "hi": "Хинди",
             "ja": "Японский", "ko": "Корейский"}
EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
MINSK = timezone(timedelta(hours=3))


def sheet_date(value: str):
    if not value:
        return ""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    # Store Minsk wall time as an actual sortable Sheets date serial.
    local = parsed.astimezone(MINSK).replace(tzinfo=timezone.utc)
    return (local - EPOCH).total_seconds() / 86400


def iso_date(value):
    if isinstance(value, (int, float)):
        return (EPOCH + timedelta(days=value) - timedelta(hours=3)).isoformat(timespec="seconds")
    return str(value) if value else ""


def sheet_row(lead: dict) -> list:
    snippets = [e["snippet"] for e in lead["evidence"] if e["snippet"] and
                e["snippet"] != "The site owner hides the web page description."]
    evidence = snippets[-1] if snippets else "Описание недоступно в поиске"
    reasons = "; ".join(REASONS.get(r["signal"], r["signal"]) + ": " + ", ".join(r["matched"])
                        for r in lead.get("reasons", []))
    if not reasons:
        reasons = lead.get("previous_reason_text") or "Недостаточно сведений в поисковой выдаче"
    engines = sorted({e.get("engine", "") for e in lead["evidence"]} - {""})
    return [lead["username"], lead["profile_url"], lead["title"] or "Название не определено",
            ", ".join(NICHES.get(n, n) for n in lead["niche_hints"]),
            "Вероятно магазин" if lead.get("classification") == "likely_store" else "Нужно проверить",
            lead.get("score", 0), evidence, LABELS["website_status"].get(lead.get("website_status", "unknown"), "Не установлен"), lead.get("followers") if lead.get("followers") is not None else "",
            lead.get("country") or "Не определена", lead.get("account_language") or "Не определён",
            ", ".join(LANGUAGES.get(code, code) for code in lead["query_languages"]),
            sheet_date(lead["first_seen"]), sheet_date(lead["last_seen"]),
            ", ".join(engines) or "Поисковая выдача", reasons,
            "Новый", "", "", ""] + [detail_value(lead, key) for key, _ in DETAIL_FIELDS]


def lead_from_row(values: list) -> dict:
    row = values + [""] * max(0, len(HEADERS) - len(values))
    account = str(row[0]).strip().lstrip("@").lower()
    canonical_url = f"https://www.instagram.com/{account}/"
    username = profile_username(canonical_url)
    linked_account = profile_username(str(row[1]))
    # Sheets may replace a URL with a rich-link display title. The username in
    # column A remains the stable identity; a mismatched actual URL is an error.
    if (not username or (linked_account and linked_account != username)
            or (not linked_account and str(row[1]).startswith(("https://", "http://")))):
        raise ValueError("Account and profile URL in the table do not match")
    reverse_niches = {value: key for key, value in NICHES.items()}
    reverse_languages = {value: key for key, value in LANGUAGES.items()}
    lead = {"username": username, "profile_url": canonical_url, "title": row[2],
            "niche_hints": [reverse_niches.get(x.strip(), x.strip()) for x in str(row[3]).split(",") if x.strip()],
            "query_languages": [reverse_languages.get(x.strip(), x.strip()) for x in str(row[11]).split(",") if x.strip()],
            "classification": "likely_store" if row[4] == "Вероятно магазин" else "needs_review",
            "score": int(row[5] or 0), "review_status": row[16] or "Новый",
            "first_seen": iso_date(row[12]), "last_seen": iso_date(row[13]),
            "followers": row[8] if isinstance(row[8], (int, float)) else None,
            "country": None, "account_language": None, "last_post_at": None,
            "website_status": "unknown", "website_urls": [], "reasons": [], "previous_reason_text": row[15],
            "evidence": [{"title": row[2], "snippet": row[6], "url": canonical_url,
                          "query": "", "query_language": "", "observed_at": iso_date(row[13]), "engine": row[14]}]}
    for index, (key, _) in enumerate(DETAIL_FIELDS, 20):
        value = row[index]
        if value == "":
            continue
        if key in DATES:
            value = iso_date(value)
        elif key in BOOLS:
            value = value == "Да"
        elif key == "website_urls":
            value = str(value).splitlines()
        elif key.endswith("_source"):
            value = {v: k for k, v in SOURCES.items()}.get(value, value)
        else:
            value = {v: k for k, v in LABELS.get(key, {}).items()}.get(value, value)
        lead[key] = value
    return lead


def plan_updates(existing: list[list], leads: dict) -> tuple[list[dict], list[list]]:
    by_username = {}
    for number, row in enumerate(existing, start=2):
        if not row or not row[0]:
            continue
        username = str(row[0]).strip().lstrip("@").lower()
        if username in by_username:
            raise ValueError("Duplicate usernames already exist in the destination table")
        by_username[username] = (number, row)
    updates, additions = [], []
    for username, lead in leads.items():
        values = sheet_row(lead)
        if username in by_username:
            number, previous = by_username[username]
            if len(previous) > 12 and previous[12] != "":
                values[12] = previous[12]
            # Never write user-owned Q:T for existing rows.
            updates.append({"range": f"A{number}:P{number}", "values": [values[:16]]})
            updates.append({"range": f"U{number}:{LAST_COLUMN}{number}", "values": [values[20:]]})
        else:
            additions.append(values)
    return updates, additions


class SheetsStore:
    def __init__(self, credentials_json: str, spreadsheet_id: str):
        import gspread
        credentials = json.loads(credentials_json)
        # Do not grant general Drive access; open only the configured spreadsheet.
        client = gspread.service_account_from_dict(credentials, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.book = client.open_by_key(spreadsheet_id)
        self.sheet = self.book.worksheet("Магазины")
        self.meta = self.book.worksheet("Служебное")
        self.rows = []

    def load(self) -> dict:
        headers = self.sheet.get(f"A1:{LAST_COLUMN}1")
        if not headers or list(headers[0]) != HEADERS:
            raise ValueError("Table headers changed; refusing to write to incorrect columns")
        state = empty_state()
        meta = {row[0]: row[1] for row in self.meta.get("A1:B8", value_render_option="UNFORMATTED_VALUE") if len(row) > 1}
        for name in ["cursor", "cycle", "runs"]:
            state[name] = int(meta.get(name, 0))
        # Bounded reads, not a whole-grid dump. Handles tables beyond 50k cells.
        self.rows = []
        for start in range(2, self.sheet.row_count + 1, 1000):
            end = min(start + 999, self.sheet.row_count)
            block = self.sheet.get(f"A{start}:{LAST_COLUMN}{end}", value_render_option="UNFORMATTED_VALUE")
            padded = [list(row) for row in block]
            padded.extend([[] for _ in range(end - start + 1 - len(padded))])
            self.rows.extend(padded)
        while self.rows and not self.rows[-1]:
            self.rows.pop()
        for row in self.rows:
            if row and row[0]:
                lead = lead_from_row(row)
                if lead["username"] in state["leads"]:
                    raise ValueError("Duplicate usernames in destination")
                state["leads"][lead["username"]] = lead
        return state

    def save(self, state: dict) -> dict:
        # Re-read before writing, preserving manual edits and row sorting between runs.
        self.load()
        updates, additions = plan_updates(self.rows, state["leads"])
        for start in range(0, len(updates), 100):
            self.sheet.batch_update(updates[start:start + 100], value_input_option="RAW")
        for start in range(0, len(additions), 100):
            self.sheet.append_rows(additions[start:start + 100], value_input_option="RAW", table_range=f"A1:{LAST_COLUMN}1")
        # Extend native table coverage and date formats to include newly appended records.
        metadata = self.book.fetch_sheet_metadata()
        properties = next(sheet for sheet in metadata["sheets"] if sheet["properties"]["sheetId"] == self.sheet.id)
        tables = properties.get("tables", [])
        if tables:
            table = next((t for t in tables if t["name"] == "InstagramStores"), None)
            if table:
                end = max(table["range"].get("endRowIndex", 0), len(self.rows) + 1 + len(additions))
                requests = [{"updateTable": {"table": {"tableId": table["tableId"],
                    "range": {**table["range"], "endRowIndex": end}}, "fields": "range"}}]
                for view in properties.get("filterViews", []):
                    if view.get("title") in {"Подходят для предложения", "Нерабочие сайты — повод для предложения"}:
                        requests.append({"updateFilterView": {"filter": {"filterViewId": view["filterViewId"],
                            "range": {**view["range"], "endRowIndex": end}}, "fields": "range"}})
                self.book.batch_update({"requests": requests})
        end = len(self.rows) + 1 + len(additions)
        if end >= 2:
            for columns in ["M2:N", "W2:W", "AC2:AC", "AS2:AS"]:
                self.sheet.format(f"{columns}{end}", {"numberFormat": {"type": "DATE_TIME", "pattern": "dd.mm.yyyy hh:mm"}})
        # Commit the cursor only after all account writes succeeded.
        self.meta.update(range_name="A1:B5", values=[["Параметр", "Значение"], ["version", 1],
            ["cursor", state["cursor"]], ["cycle", state["cycle"]], ["runs", state["runs"]]], value_input_option="RAW")
        return {"updated": len(updates) // 2, "added": len(additions)}

    def save_enrichment(self, profiles: list[dict]) -> dict:
        """Update only tested, still-existing accounts; leave search cursor alone."""
        from .observations import MANUAL_EXCLUSIONS, merge_enrichment
        current = self.load()
        changed, skipped = {}, 0
        for profile in profiles:
            name = profile['username']
            lead = current['leads'].get(name)
            if not lead or lead.get('review_status') in MANUAL_EXCLUSIONS:
                skipped += 1
                continue
            changed[name] = merge_enrichment(lead, profile)
        by_username = {str(row[0]).strip().lstrip('@').lower(): number
                       for number, row in enumerate(self.rows, 2) if row and row[0]}
        updates = []
        for name, lead in changed.items():
            number, values = by_username[name], sheet_row(lead)
            # Preserve profile link chips, search evidence, discovery dates,
            # country/language and all Q:T manual fields.
            for left, right, first, last in [('C', 'C', 2, 3), ('E', 'F', 4, 6),
                                           ('H', 'I', 7, 9), ('P', 'P', 15, 16),
                                           ('U', LAST_COLUMN, 20, len(HEADERS))]:
                updates.append({'range': f'{left}{number}:{right}{number}', 'values': [values[first:last]]})
        for start in range(0, len(updates), 100):
            self.sheet.batch_update(updates[start:start + 100], value_input_option='RAW')
        return {'updated': len(changed), 'skipped': skipped}

    def cloud_last_attempt(self):
        rows = self.meta.get('A7:B7', value_render_option='UNFORMATTED_VALUE')
        return iso_date(rows[0][1]) if rows and len(rows[0]) > 1 and rows[0][0] == 'cloud_last_attempt' else ''

    def mark_cloud_attempt(self, date):
        self.meta.update(range_name='A7:B7', values=[['cloud_last_attempt', date]], value_input_option='RAW')


def configured_sheet() -> str:
    config = Path("config/destination.json")
    return json.loads(config.read_text())["spreadsheet_id"] if config.exists() else ""
