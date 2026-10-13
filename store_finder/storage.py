import csv
import io
import json
import os
import zipfile
from pathlib import Path


def empty_state() -> dict:
    return {"version": 1, "cursor": 0, "cycle": 0, "leads": {}, "runs": 0}


def read_state(path: Path, key: str) -> dict:
    from cryptography.fernet import Fernet, InvalidToken
    try:
        plaintext = Fernet(key.encode()).decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise ValueError("Cannot decrypt previous results. Keep the original RESULTS_ENCRYPTION_KEY; refusing to reset the database.") from exc
    with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
        state = json.loads(archive.read("state.json"))
    if state.get("version") != 1 or not isinstance(state.get("leads"), dict):
        raise ValueError("Unsupported or damaged state archive")
    return state


def safe_cell(value) -> str:
    text = str(value) if value is not None else ""
    # Prevent formula execution when importing search snippets into Excel/Sheets.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def csv_bytes(leads: dict) -> bytes:
    from .sheets import HEADERS, sheet_row
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for lead in sorted(leads.values(), key=lambda item: (-item.get("score", 0), item["username"])):
        row = sheet_row(lead)
        row[12:14] = [lead["first_seen"], lead["last_seen"]]
        row[16] = lead.get("review_status", "Новый")
        writer.writerow([safe_cell(value) for value in row])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def write_state(path: Path, state: dict, key: str):
    from cryptography.fernet import Fernet
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("state.json", json.dumps(state, ensure_ascii=False))
        archive.writestr("leads.csv", csv_bytes(state["leads"]))
    ciphertext = Fernet(key.encode()).encrypt(buffer.getvalue())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(ciphertext)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def export_state(path: Path, key: str, target: Path):
    state = read_state(path, key)
    target.mkdir(parents=True, exist_ok=True)
    for filename, content in [("state.json", json.dumps(state, ensure_ascii=False, indent=2).encode()),
                              ("leads.csv", csv_bytes(state["leads"]))]:
        file = target / filename
        file.write_bytes(content)
        os.chmod(file, 0o600)
