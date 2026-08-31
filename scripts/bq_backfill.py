"""heartrate / ring_battery_level を Oura API から取得し BigQuery にロードする。

バックフィル用。--since 以降の全件を取得しテーブルを丸ごと洗い替える
(--replace) ので何度実行しても重複しない。
日次の増分同期は GAS (deploy/oura_sync.gs) が行う。

実行: uv run python scripts/bq_backfill.py [--since 2026-01-01]
"""
import argparse
import json
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone

from oura_sync import oura_client

BQ_PROJECT = "oura-data-yy"
BQ_DATASET = "oura"
TARGETS = {"heartrate", "ring_battery_level"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()
    start = date.fromisoformat(args.since)
    end = date.today()
    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for ep in oura_client.ENDPOINTS:
        if ep.name not in TARGETS:
            continue
        # datetime 系は 30 日以内の範囲指定しか受け付けないため分割して取得。
        # チャンク境界 (翌日 0 時ちょうど) の重複はキーで除去する
        docs: dict[str, dict] = {}
        s = start
        while s <= end:
            e = min(s + timedelta(days=29), end)
            for d in oura_client.fetch(ep, s, e):
                docs["|".join(str(d.get(k, "")) for k in ep.key)] = d
            s = e + timedelta(days=1)
        print(f"[{ep.name}] {start} .. {end}: {len(docs)}件取得")
        with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False) as f:
            for d in docs.values():
                d["synced_at"] = synced_at
                f.write(json.dumps(d) + "\n")
            path = f.name
        subprocess.run(
            [
                "bq", f"--project_id={BQ_PROJECT}", "load", "--replace",
                "--source_format=NEWLINE_DELIMITED_JSON",
                f"{BQ_DATASET}.{ep.name}", path,
            ],
            check=True,
        )
        print(f"[{ep.name}] BigQuery にロード完了")


if __name__ == "__main__":
    main()
