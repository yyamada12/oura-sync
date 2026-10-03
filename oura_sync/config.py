import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OURA_CLIENT_ID = os.environ.get("OURA_CLIENT_ID", "")
OURA_CLIENT_SECRET = os.environ.get("OURA_CLIENT_SECRET", "")
OURA_REDIRECT_URI = os.environ.get("OURA_REDIRECT_URI", "http://localhost:8765/callback")
OURA_SCOPES = os.environ.get(
    "OURA_SCOPES",
    "email personal daily heartrate workout tag session spo2 ring_configuration stress heart_health"
)
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# remote 環境用: 指定するとトークンを tokens.json ではなく Secret Manager に保存する
# 形式: projects/<project>/secrets/<name>
OURA_TOKEN_SECRET = os.environ.get("OURA_TOKEN_SECRET", "")
# remote 環境用: サービスアカウント鍵 JSON の中身 (生 JSON または base64)。未指定なら ADC
GOOGLE_SERVICE_ACCOUNT_KEY = os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY", "")

# サービスアカウント鍵の相対パスはプロジェクトルート基準に解決する (launchd 等で cwd が違っても動くように)
_gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
if _gac and not os.path.isabs(_gac):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(ROOT / _gac)

TOKENS_PATH = ROOT / "tokens.json"

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2/usercollection"


def google_credentials(scopes: list[str]):
    """GOOGLE_SERVICE_ACCOUNT_KEY があればそれを、無ければ ADC を使う。"""
    if GOOGLE_SERVICE_ACCOUNT_KEY:
        from google.oauth2 import service_account

        raw = GOOGLE_SERVICE_ACCOUNT_KEY.strip()
        info = json.loads(raw if raw.startswith("{") else base64.b64decode(raw))
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)
    import google.auth

    creds, _ = google.auth.default(scopes=scopes)
    return creds


def require(name: str, value: str) -> str:
    if not value:
        raise SystemExit(f".env に {name} を設定してください (.env.example 参照)")
    return value
