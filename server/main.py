"""NRC (Apple Health) ランニングワークアウト受信 API。

iOS ショートカットから POST された Run データを BigQuery
`spherical-depth-263101.running.runs` に冪等 MERGE で保存する。
(source, started_at) をキーに、再送時は上書き更新。

受け付ける形式:
1. 単一: {"started_at": "...", "distance": "8.02 km", ...}
2. 一括 (Shortcuts のリスト変数): 各フィールドが改行区切りで N 件分
   {"started_at": "2026/08/30 20:45\n2026/08/29 20:36", "distance": "12,147 m\n7,003 m", ...}
3. 一括 (配列): [{...}, {...}]
"""

import hmac
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from dateutil import parser as dateparser
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nrc-ingest")

PROJECT_ID = os.environ.get("BQ_PROJECT", "spherical-depth-263101")
DATASET = os.environ.get("BQ_DATASET", "running")
TABLE = f"{PROJECT_ID}.{DATASET}.runs"
VIEW = f"{PROJECT_ID}.{DATASET}.runs_v"
API_TOKEN = os.environ.get("API_TOKEN", "")

app = FastAPI(title="nrc-ingest", docs_url=None, redoc_url=None)
bq = bigquery.Client(project=PROJECT_ID)
bearer = HTTPBearer(auto_error=False)


def require_token(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    if not API_TOKEN:
        raise HTTPException(500, "API_TOKEN not configured on server")
    if cred is None or not hmac.compare_digest(cred.credentials, API_TOKEN):
        raise HTTPException(401, "invalid or missing bearer token")


def _num(value: Any) -> float | None:
    """Shortcuts は "8.02 km" "421 kcal" のような単位付き文字列を送ってくることがある。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    return float(m.group().replace(",", "")) if m else None


def _ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    # 日本語ロケールの日付 ("2026年8月31日 21:05" 等) を ISO 風に正規化
    text = re.sub(r"[年月]", "-", str(value))
    text = re.sub(r"日", " ", text).replace("午前", "AM ").replace("午後", "PM ")
    try:
        dt = dateparser.parse(text.strip())
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        # タイムゾーンなしは JST とみなす (Shortcuts のローカル日付対策)
        dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt


def _pick(payload: dict, *keys: str) -> Any:
    for k in keys:
        if k in payload and payload[k] not in (None, ""):
            return payload[k]
    return None


def normalize(payload: dict) -> dict | None:
    """1 件分のペイロードを行データに正規化する。started_at が読めなければ None。"""
    started_at = _ts(_pick(payload, "started_at", "start", "start_date", "startDate"))
    if started_at is None:
        return None

    ended_at = _ts(_pick(payload, "ended_at", "end", "end_date", "endDate"))
    duration = _num(_pick(payload, "duration_seconds", "duration"))
    if duration is None and ended_at is not None:
        duration = (ended_at - started_at).total_seconds()
    if ended_at is None and duration is not None:
        ended_at = datetime.fromtimestamp(started_at.timestamp() + duration, tz=timezone.utc)

    distance = _num(_pick(payload, "distance_m", "distance"))
    # 30 未満の値は km 単位で送られたとみなす (30km/日超の m 表記はない)
    if distance is not None and distance < 30:
        distance *= 1000

    hr_samples = []
    for item in payload.get("heart_rates") or []:
        if not isinstance(item, dict):
            continue
        t = _ts(_pick(item, "time", "date", "timestamp", "start"))
        bpm = _num(_pick(item, "bpm", "value", "hr"))
        if t is not None and bpm is not None:
            hr_samples.append({"time": t.isoformat(), "bpm": bpm})
    hr_samples.sort(key=lambda s: s["time"])

    avg_hr = _num(_pick(payload, "avg_hr", "average_heart_rate", "avgHeartRate"))
    max_hr = _num(_pick(payload, "max_hr", "max_heart_rate", "maxHeartRate"))
    if hr_samples:
        bpms = [s["bpm"] for s in hr_samples]
        avg_hr = avg_hr if avg_hr is not None else round(sum(bpms) / len(bpms), 1)
        max_hr = max_hr if max_hr is not None else max(bpms)

    source_name = _pick(payload, "source_name", "sourceName", "app", "workout_source")

    return {
        "source": str(_pick(payload, "source") or "nrc_apple_health"),
        "source_name": str(source_name) if source_name is not None else None,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat() if ended_at else None,
        "duration_seconds": duration,
        "distance_m": distance,
        "active_calories": _num(_pick(payload, "active_calories", "calories", "activeCalories")),
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "heart_rates": hr_samples,
        "raw": payload,
    }


# Shortcuts のリスト変数は改行区切りで全件連結されるため、zip して 1 件ずつに分解する
SPLITTABLE_KEYS = (
    "started_at", "start", "start_date", "startDate",
    "ended_at", "end", "end_date", "endDate",
    "duration_seconds", "duration",
    "distance_m", "distance",
    "active_calories", "calories", "activeCalories",
    "avg_hr", "average_heart_rate", "avgHeartRate",
    "max_hr", "max_heart_rate", "maxHeartRate",
    "source_name", "sourceName", "app", "workout_source",
)


def explode(payload: dict) -> list[dict]:
    started_raw = _pick(payload, "started_at", "start", "start_date", "startDate")
    if not isinstance(started_raw, str) or "\n" not in started_raw:
        return [payload]

    columns: dict[str, list[str]] = {}
    for key in SPLITTABLE_KEYS:
        if isinstance(payload.get(key), str):
            columns[key] = [line.strip() for line in payload[key].split("\n")]

    n = len(columns[[k for k in ("started_at", "start", "start_date", "startDate") if k in columns][0]])
    items = []
    for i in range(n):
        item = {k: v for k, v in payload.items() if k not in SPLITTABLE_KEYS}
        item.pop("heart_rates", None)  # 一括モードではどのランの心拍か特定できないため無視
        for key, lines in columns.items():
            if i < len(lines) and lines[i] != "":
                item[key] = lines[i]
        items.append(item)
    return items


MERGE_SQL = f"""
MERGE `{TABLE}` T
USING (
  SELECT
    JSON_VALUE(r, '$.source') AS source,
    JSON_VALUE(r, '$.source_name') AS source_name,
    TIMESTAMP(JSON_VALUE(r, '$.started_at')) AS started_at,
    TIMESTAMP(JSON_VALUE(r, '$.ended_at')) AS ended_at,
    SAFE_CAST(JSON_VALUE(r, '$.duration_seconds') AS FLOAT64) AS duration_seconds,
    SAFE_CAST(JSON_VALUE(r, '$.distance_m') AS FLOAT64) AS distance_m,
    SAFE_CAST(JSON_VALUE(r, '$.active_calories') AS FLOAT64) AS active_calories,
    SAFE_CAST(JSON_VALUE(r, '$.avg_hr') AS FLOAT64) AS avg_hr,
    SAFE_CAST(JSON_VALUE(r, '$.max_hr') AS FLOAT64) AS max_hr,
    JSON_QUERY(r, '$.heart_rates') AS heart_rates,
    JSON_QUERY(r, '$.raw') AS raw
  FROM UNNEST(JSON_QUERY_ARRAY(PARSE_JSON(@rows, wide_number_mode=>'round'))) AS r
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY JSON_VALUE(r, '$.source'), JSON_VALUE(r, '$.started_at')
    ORDER BY JSON_VALUE(r, '$.started_at')
  ) = 1
) S
ON T.source = S.source AND T.started_at = S.started_at
WHEN MATCHED THEN UPDATE SET
  source_name = COALESCE(S.source_name, T.source_name),
  ended_at = COALESCE(S.ended_at, T.ended_at),
  duration_seconds = COALESCE(S.duration_seconds, T.duration_seconds),
  distance_m = COALESCE(S.distance_m, T.distance_m),
  active_calories = COALESCE(S.active_calories, T.active_calories),
  avg_hr = COALESCE(S.avg_hr, T.avg_hr),
  max_hr = COALESCE(S.max_hr, T.max_hr),
  -- 再送で心拍時系列が空のときは既存データを保持する
  heart_rates = IF(ARRAY_LENGTH(JSON_QUERY_ARRAY(S.heart_rates)) > 0,
                   S.heart_rates, T.heart_rates),
  raw = S.raw,
  ingested_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT
  (source, source_name, started_at, ended_at, duration_seconds, distance_m,
   active_calories, avg_hr, max_hr, heart_rates, raw, ingested_at)
VALUES
  (S.source, S.source_name, S.started_at, S.ended_at, S.duration_seconds, S.distance_m,
   S.active_calories, S.avg_hr, S.max_hr, S.heart_rates, S.raw, CURRENT_TIMESTAMP())
"""


# NOTE: /healthz は *.run.app では Google Frontend に予約されておりアプリに届かない
@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/runs", dependencies=[Depends(require_token)])
async def ingest_runs(request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "body must be JSON")

    if isinstance(payload, dict):
        items = explode(payload)
    elif isinstance(payload, list):
        items = [p for p in payload if isinstance(p, dict)]
    else:
        raise HTTPException(422, "body must be a JSON object or array")

    rows = []
    skipped = 0
    for item in items:
        row = normalize(item)
        if row is None:
            skipped += 1
        else:
            rows.append(row)

    if not rows:
        logger.warning("started_at missing/unparseable. payload=%s",
                       json.dumps(payload, ensure_ascii=False, default=str)[:2000])
        raise HTTPException(422, "started_at is required (ISO8601 or unix epoch)")

    rows_json = json.dumps(rows, ensure_ascii=False, default=str)
    job = bq.query(MERGE_SQL, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("rows", "STRING", rows_json)]
    ))
    job.result()
    logger.info("merged %d run(s), skipped=%d, affected=%s",
                len(rows), skipped, job.num_dml_affected_rows)
    return {
        "ok": True,
        "received": len(items),
        "merged": len(rows),
        "skipped": skipped,
        "affected_rows": job.num_dml_affected_rows,
        "latest": {k: rows[0][k] for k in ("source", "started_at", "distance_m")},
    }


@app.get("/api/runs", dependencies=[Depends(require_token)])
def list_runs(limit: int = 20) -> dict:
    limit = max(1, min(limit, 200))
    job = bq.query(
        f"SELECT * FROM `{VIEW}` ORDER BY started_at DESC LIMIT @limit",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", limit)]
        ),
    )
    rows = [dict(r) for r in job.result()]
    for r in rows:
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.isoformat()
    return {"count": len(rows), "runs": rows}
