#!/usr/bin/env python3
"""Process a workflow_run request only after verifying its GitHub provenance."""
from __future__ import annotations
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_lock as gl
import gate_state as gs

REPO = "Hawaiine/mihomo-rules"
WORKFLOW_PATH = ".github/workflows/notice-confirm-request.yml"
ALLOWED_ID = 152380613
ALLOWED_LOGIN = "Hawaiine"


def api(token: str, path: str, *, binary: bool = False):
    req = urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "mihomo-rules-notice-confirm"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
            return data if binary else json.loads(data)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        location = exc.headers.get("Location", "")
        if not location.startswith("https://" ):
            raise ValueError("untrusted artifact redirect") from exc
        request = urllib.request.Request(location, headers={"User-Agent": "mihomo-rules-notice-confirm"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read() if binary else json.loads(response.read())


def unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def obtain_request(token: str, run_id: str) -> tuple[dict, dict]:
    run = api(token, f"/repos/{REPO}/actions/runs/{run_id}")
    if run.get("repository", {}).get("full_name") != REPO:
        raise ValueError("wrong repository")
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        raise ValueError("request must be a workflow_dispatch on main")
    if run.get("run_attempt") != 1 or run.get("run_number", 0) < 1:
        raise ValueError("workflow reruns are not accepted")
    default_ref = api(token, f"/repos/{REPO}/git/ref/heads/main")
    if run.get("head_sha") != default_ref.get("object", {}).get("sha"):
        raise ValueError("request workflow was not executed from current default-branch tip")
    workflow = api(token, f"/repos/{REPO}/actions/workflows/{run.get('workflow_id')}")
    if workflow.get("path") != WORKFLOW_PATH or workflow.get("state") != "active":
        raise ValueError("unexpected request workflow")
    actor = run.get("actor")
    if not isinstance(actor, dict) or actor.get("id") != ALLOWED_ID or actor.get("login") != ALLOWED_LOGIN:
        raise ValueError("unauthorized initiating actor")
    profile = api(token, f"/users/{ALLOWED_ID}")
    if profile.get("id") != ALLOWED_ID or profile.get("login") != ALLOWED_LOGIN or profile.get("type") != "User":
        raise ValueError("GitHub user identity did not resolve to authorized human")
    source_sha = run.get("head_sha")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("invalid source workflow SHA")
    source_file = api(token, f"/repos/{REPO}/contents/{WORKFLOW_PATH}?ref={source_sha}")
    import base64
    source_content = base64.b64decode(source_file.get("content", "")).decode("utf-8")
    if "workflow_dispatch:" not in source_content or "name: Manual notice confirmation request" not in source_content:
        raise ValueError("source run workflow content is not the trusted request workflow")
    artifacts = api(token, f"/repos/{REPO}/actions/runs/{run_id}/artifacts")
    matches = [a for a in artifacts.get("artifacts", []) if a.get("name") == f"manual-notice-request-{run_id}" and not a.get("expired")]
    if len(matches) != 1:
        raise ValueError("request artifact missing, expired, or ambiguous")
    download_path = matches[0]["archive_download_url"].removeprefix("https://api.github.com")
    archive = api(token, download_path, binary=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        if zf.namelist() != ["notice-request.json"]:
            raise ValueError("unexpected request archive contents")
        request = json.loads(zf.read("notice-request.json"), object_pairs_hook=unique_object)
    if set(request) != {"notice_run_id", "notice_digest", "expected_generation"}:
        raise ValueError("unexpected request fields")
    if not isinstance(request["notice_run_id"], str) or not re.fullmatch(r"[1-9][0-9]*", request["notice_run_id"]):
        raise ValueError("invalid notice_run_id")
    if not isinstance(request["notice_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", request["notice_digest"]):
        raise ValueError("invalid notice_digest")
    if not isinstance(request["expected_generation"], str) or not re.fullmatch(r"[1-9][0-9]*", request["expected_generation"]):
        raise ValueError("invalid expected_generation")
    return request, {"id": ALLOWED_ID, "login": ALLOWED_LOGIN, "type": "User"}


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not token or not event_path:
        raise ValueError("GitHub event context unavailable")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    wr = event.get("workflow_run", {})
    if wr.get("repository", {}).get("full_name") != REPO or wr.get("conclusion") != "success":
        raise ValueError("untrusted workflow_run event")
    source_id = wr.get("id")
    if not isinstance(source_id, int) or source_id < 1:
        raise ValueError("invalid source workflow run id")
    request, actor = obtain_request(token, str(source_id))
    store = gs.GitHubRefStore(REPO, token)
    return gl.confirm_notice(store, notice_run_id=request["notice_run_id"],
        notice_digest=request["notice_digest"], expected_generation=int(request["expected_generation"]),
        actor_id=actor["id"], actor_login=actor["login"], actor_type=actor["type"], strict_write=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "decision": "reject", "reason": "PROVENANCE_OR_REQUEST_INVALID",
                          "detail": str(exc), "state_written": False}, ensure_ascii=False))
        sys.exit(gl.EXIT_NEEDS_HUMAN)
