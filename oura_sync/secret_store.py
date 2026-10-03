"""Oura トークンを GCP Secret Manager に保存する (remote 環境向け)。

ファイルを永続化できない環境でも refresh token のローテーションを引き継げるよう、
トークン JSON を 1 つのシークレットのバージョンとして持つ。
- 読み込み: `versions/latest` を access
- 保存: 新しいバージョンを追加し、読み込んだ旧バージョンは destroy (課金対象のバージョンを溜めない)
"""
import base64
import json

from google.auth.transport.requests import AuthorizedSession

from . import config

API = "https://secretmanager.googleapis.com/v1"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_session: AuthorizedSession | None = None
_loaded_version: str | None = None  # 直近に読んだバージョンのリソース名


def _sess() -> AuthorizedSession:
    global _session
    if _session is None:
        _session = AuthorizedSession(config.google_credentials(SCOPES))
    return _session


def _check(r, what: str) -> dict:
    if r.status_code >= 400:
        raise SystemExit(f"Secret Manager {what} 失敗 {r.status_code}: {r.text}")
    return r.json()


def load(secret: str) -> dict | None:
    global _loaded_version
    r = _sess().get(f"{API}/{secret}/versions/latest:access", timeout=30)
    if r.status_code == 404:  # シークレットはあるがバージョンが無い (初回)
        return None
    body = _check(r, "access")
    _loaded_version = body["name"]
    return json.loads(base64.b64decode(body["payload"]["data"]))


def save(secret: str, tok: dict) -> None:
    global _loaded_version
    data = base64.b64encode(json.dumps(tok).encode()).decode()
    body = _check(
        _sess().post(f"{API}/{secret}:addVersion", json={"payload": {"data": data}}, timeout=30),
        "addVersion",
    )
    old, _loaded_version = _loaded_version, body["name"]
    if old:
        # 旧 refresh token は既に失効済みなので残す意味がない。失敗しても致命的ではない
        r = _sess().post(f"{API}/{old}:destroy", json={}, timeout=30)
        if r.status_code >= 400:
            print(f"  旧バージョン {old} の destroy に失敗 ({r.status_code})。手動で削除してください")
