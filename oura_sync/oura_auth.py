"""Oura OAuth2 (Authorization Code Flow) とトークン管理。

- `authorize()` : ブラウザで認可 → ローカル HTTP サーバーで code を受け取り tokens.json に保存
- `get_access_token()` : 期限切れなら refresh。refresh token は single-use なので即座に上書き保存
"""
import json
import fcntl
import os
import tempfile
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import config


def _load_tokens() -> dict | None:
    if config.TOKENS_PATH.exists():
        return json.loads(config.TOKENS_PATH.read_text())
    return None


def _save_tokens(tok: dict) -> None:
    tok = dict(tok)
    tok["expires_at"] = int(time.time()) + int(tok.get("expires_in", 86400)) - 60
    fd, name = tempfile.mkstemp(dir=config.TOKENS_PATH.parent, prefix=".oura-token-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(tok, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, config.TOKENS_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _token_request(data: dict) -> dict:
    data = {
        **data,
        "client_id": config.require("OURA_CLIENT_ID", config.OURA_CLIENT_ID),
        "client_secret": config.require("OURA_CLIENT_SECRET", config.OURA_CLIENT_SECRET),
    }
    r = requests.post(config.TOKEN_URL, data=data, timeout=30)
    if r.status_code >= 400:
        raise SystemExit(f"token 取得失敗 HTTP {r.status_code}")
    return r.json()


def authorize() -> None:
    redirect = urlparse(config.OURA_REDIRECT_URI)
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path != redirect.path:
                self.send_response(404)
                self.end_headers()
                return
            q = parse_qs(u.query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if q.get("state", [""])[0] != state:
                self.wfile.write("state が一致しません".encode())
                return
            if "error" in q:
                result["error"] = q["error"][0]
                self.wfile.write(f"認可エラー: {q['error'][0]}".encode())
                return
            result["code"] = q["code"][0]
            self.wfile.write("認可完了。ターミナルに戻ってください。".encode())

        def log_message(self, *a):  # 静かに
            pass

    srv = HTTPServer((redirect.hostname or "localhost", redirect.port or 80), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    url = config.AUTHORIZE_URL + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": config.require("OURA_CLIENT_ID", config.OURA_CLIENT_ID),
            "redirect_uri": config.OURA_REDIRECT_URI,
            "scope": config.OURA_SCOPES,
            "state": state,
        }
    )
    print("ブラウザで次の URL を開いて認可してください:\n", url, "\n")
    webbrowser.open(url)

    while "code" not in result and "error" not in result:
        time.sleep(0.2)
    srv.shutdown()
    if "error" in result:
        raise SystemExit(f"認可エラー: {result['error']}")

    tok = _token_request(
        {
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": config.OURA_REDIRECT_URI,
        }
    )
    _save_tokens(tok)
    print(f"tokens.json に保存しました (scope: {tok.get('scope')})")


def get_access_token() -> str:
    # Serialize rotating refresh tokens across daily, weekly, and local sync jobs.
    with config.TOKENS_PATH.with_suffix(".lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _get_access_token_locked()


def _get_access_token_locked() -> str:
    tok = _load_tokens()
    if not tok:
        raise SystemExit("tokens.json がありません。先に `oura-sync auth` を実行してください")
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    new = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    _save_tokens(new)  # refresh token は single-use。必ず即保存
    return new["access_token"]
