"""Google Sheets への upsert。

- エンドポイントごとに 1 タブ
- 1 行目がヘッダ。A 列は upsert キー (`_key`)、B 列は取り込み時刻 (`_synced_at`)
- ネストした dict は `a.b` に平坦化、list は JSON 文字列として格納
- 新しい列が出てきたらヘッダを拡張
"""
import json
from datetime import datetime, timezone

import google.auth
import gspread

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
META_COLS = ["_key", "_synced_at"]


def flatten(obj: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in obj.items():
        name = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, name + "."))
        elif isinstance(v, list):
            out[name] = json.dumps(v, ensure_ascii=False)
        else:
            out[name] = v
    return out


def make_key(doc: dict, key_fields: tuple) -> str:
    return "|".join(str(doc.get(f, "")) for f in key_fields)


class SheetStore:
    def __init__(self):
        creds, _ = google.auth.default(scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.book = self.gc.open_by_key(config.require("SPREADSHEET_ID", config.SPREADSHEET_ID))

    def _worksheet(self, name: str) -> gspread.Worksheet:
        try:
            return self.book.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(name, rows=100, cols=26)
            ws.update("A1", [META_COLS])
            return ws

    def upsert(self, tab: str, docs: list[dict], key_fields: tuple) -> tuple[int, int]:
        """(更新件数, 追加件数) を返す"""
        if not docs:
            return 0, 0
        ws = self._worksheet(tab)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        rows = {}
        for d in docs:
            flat = flatten(d)
            flat["_key"] = make_key(d, key_fields)
            flat["_synced_at"] = now
            rows[flat["_key"]] = flat

        header = ws.row_values(1) or META_COLS[:]
        new_cols = sorted({c for r in rows.values() for c in r} - set(header))
        if new_cols:
            header = header + new_cols
            if ws.col_count < len(header):
                ws.resize(cols=len(header))
            ws.update("A1", [header])

        existing = ws.col_values(1)[1:]  # A 列 (ヘッダ除く)
        index = {k: i + 2 for i, k in enumerate(existing)}  # 1-origin 行番号

        def to_row(flat: dict) -> list:
            return [_cell(flat.get(c)) for c in header]

        updates = []
        appends = []
        for k, flat in rows.items():
            if k in index:
                updates.append({"range": f"A{index[k]}", "values": [to_row(flat)]})
            else:
                appends.append(to_row(flat))

        if updates:
            for i in range(0, len(updates), 500):
                ws.batch_update(updates[i : i + 500], value_input_option="RAW")
        if appends:
            ws.append_rows(appends, value_input_option="RAW", table_range="A1")
        return len(updates), len(appends)


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    return v
