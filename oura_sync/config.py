import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OURA_CLIENT_ID = os.environ.get("OURA_CLIENT_ID", "")
OURA_CLIENT_SECRET = os.environ.get("OURA_CLIENT_SECRET", "")
OURA_REDIRECT_URI = os.environ.get("OURA_REDIRECT_URI", "http://localhost:8765/callback")
OURA_SCOPES = os.environ.get(
    "OURA_SCOPES", "email personal daily heartrate workout tag session spo2"
)
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

TOKENS_PATH = ROOT / "tokens.json"

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2/usercollection"


def require(name: str, value: str) -> str:
    if not value:
        raise SystemExit(f".env に {name} を設定してください (.env.example 参照)")
    return value
