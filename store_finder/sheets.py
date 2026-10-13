"""Google Sheets is the private database. A:P automatic; Q:T owned by the user."""
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
            lead.get("score", 0), evidence, "Не проверен", lead.get("followers") if lead.get("followers") is not None else "Неизвестно",
            lead.get("country") or "Не определена", lead.get("account_language") or "Не определён",
            ", ".join(LANGUAGES.get(code, code) for code in lead["query_languages"]),
            sheet_date(lead["first_seen"]), sheet_date(lead["last_seen"]),
            ", ".join(engines) or "Поисковая выдача", reasons,
            "Новый", "", "", ""]


def lead_from_row(values: list) -> dict:
    row = values + [""] * (20 - len(values))
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
    return {"username": username, "profile_url": canonical_url, "title": row[2],
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
        headers = self.sheet.get("A1:T1")
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
            block = self.sheet.get(f"A{start}:T{end}", value_render_option="UNFORMATTED_VALUE")
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
            self.sheet.append_rows(additions[start:start + 100], value_input_option="RAW", table_range="A1:T1")
        # Extend native table coverage and date formats to include newly appended records.
        metadata = self.book.fetch_sheet_metadata()
        properties = next(sheet for sheet in metadata["sheets"] if sheet["properties"]["sheetId"] == self.sheet.id)
        tables = properties.get("tables", [])
        if tables:
            table = next((t for t in tables if t["name"] == "InstagramStores"), None)
            if table:
                end = max(table["range"].get("endRowIndex", 0), len(self.rows) + 1 + len(additions))
                self.book.batch_update({"requests": [{"updateTable": {"table": {"tableId": table["tableId"],
                    "range": {**table["range"], "endRowIndex": end}}, "fields": "range"}}]})
        if additions:
            self.sheet.format(f"M2:N{len(self.rows) + 1 + len(additions)}", {"numberFormat": {"type": "DATE_TIME", "pattern": "dd.mm.yyyy hh:mm"}})
        # Commit the cursor only after all account writes succeeded.
        self.meta.update(range_name="A1:B5", values=[["Параметр", "Значение"], ["version", 1],
            ["cursor", state["cursor"]], ["cycle", state["cycle"]], ["runs", state["runs"]]], value_input_option="RAW")
        return {"updated": len(updates), "added": len(additions)}


def configured_sheet() -> str:
    config = Path("config/destination.json")
    return json.loads(config.read_text())["spreadsheet_id"] if config.exists() else ""
