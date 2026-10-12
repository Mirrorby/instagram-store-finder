"""Restore only ciphertext from a trusted main-branch Actions run."""
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "instagram-store-finder"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def restore(path: Path):
    if path.exists():
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        return
    current = int(os.environ.get("GITHUB_RUN_ID", "0"))
    artifact = None
    # Search newest first, with bounded pagination; only main-branch same-repo runs.
    for page in range(1, 11):
        data = request_json(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}", token)
        for item in data["artifacts"]:
            run = item.get("workflow_run", {})
            if (item["name"] == "leads-encrypted" and not item["expired"]
                    and run.get("head_branch") == "main" and run.get("id") != current
                    and run.get("head_repository_id") == run.get("repository_id")):
                artifact = item
                break
        if artifact or len(data["artifacts"]) < 100:
            break
    if not artifact:
        if page == 10 and len(data["artifacts"]) == 100:
            raise RuntimeError("Artifact history exceeded restore limit; refusing to reset database")
        return
    request = urllib.request.Request(artifact["archive_download_url"], headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "User-Agent": "instagram-store-finder"})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            payload = response.read(50_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        target = exc.headers["Location"]
        if urllib.parse.urlparse(target).scheme != "https":
            raise RuntimeError("Unexpected artifact redirect")
        # Signed storage URL does not receive the GitHub Authorization header.
        with urllib.request.urlopen(target, timeout=60) as response:
            payload = response.read(50_000_001)
    if len(payload) > 50_000_000:
        raise RuntimeError("Previous artifact is too large")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo("leads.fernet")
        if info.file_size > 50_000_000:
            raise RuntimeError("Previous encrypted file is too large")
        ciphertext = archive.read(info)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ciphertext)
    os.chmod(path, 0o600)
