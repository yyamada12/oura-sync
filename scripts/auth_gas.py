"""GAS 専用の Oura OAuth 認可を行い、tokens_gas.json に保存する。

ローカルの tokens.json とは別チェーンのトークンを発行する
(Oura の refresh token は single-use のため、GAS と共有できない)。
`refresh_token` を Apps Script のスクリプト プロパティ OURA_REFRESH_TOKEN に
登録し、GAS の初回同期が成功したらこのファイルは削除してよい。

実行: uv run python scripts/auth_gas.py
"""
from oura_sync import config

config.TOKENS_PATH = config.ROOT / "tokens_gas.json"

from oura_sync.oura_auth import authorize

if __name__ == "__main__":
    authorize()
    print("GAS 用トークンを tokens_gas.json に保存しました")
