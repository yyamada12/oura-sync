"""NRC (Apple Health) ランニングワークアウト受信 API。

iOS ショートカットから POST された Run データを BigQuery
`spherical-depth-263101.running.runs` に冪等 MERGE で保存する。
(source, started_at) をキーに、再送時は上書き更新。
"""

import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone
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
    try:
        dt = dateparser.parse(str(value))
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pick(payload: dict, *keys: str) -> Any:
    for k in keys:
        if k in payload and payload[k] not in (None, ""):
            return payload[k]
    return None


def normalize(payload: dict) -> dict:
    started_at = _ts(_pick(payload, "started_at", "start", "start_date", "startDate"))
    if started_at is None:
        raise HTTPException(422, "started_at is required (ISO8601 or unix epoch)")

    ended_at = _ts(_pick(payload, "ended_at", "end", "end_date", "endDate"))
    duration = _num(_pick(payload, "duration_seconds", "duration"))
    if duration is None and ended_at is not None:
        duration = (ended_at - started_at).total_seconds()
    if ended_at is None and duration is not None:
        ended_at = datetime.fromtimestamp(started_at.timestamp() + duration, tz=timezone.utc)

    distance = _num(_pick(payload, "distance_m", "distance"))
    # 30km 未満の値は km 単位で送られたとみなす (NRC のランで 30km/日超の m 表記はない)
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

    return {
        "source": str(_pick(payload, "source") or "nrc_apple_health"),
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat() if ended_at else None,
        "duration_seconds": duration,
        "distance_m": distance,
        "active_calories": _num(_pick(payload, "active_calories", "calories", "activeCalories")),
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "heart_rates": json.dumps(hr_samples),
        "raw": json.dumps(payload, ensure_ascii=False, default=str),
    }


MERGE_SQL = f"""
MERGE `{TABLE}` T
USING (
  SELECT
    @source AS source,
    TIMESTAMP(@started_at) AS started_at,
    TIMESTAMP(@ended_at) AS ended_at,
    @duration_seconds AS duration_seconds,
    @distance_m AS distance_m,
    @active_calories AS active_calories,
    @avg_hr AS avg_hr,
    @max_hr AS max_hr,
    PARSE_JSON(@heart_rates) AS heart_rates,
    PARSE_JSON(@raw, wide_number_mode=>'round') AS raw
) S
ON T.source = S.source AND T.started_at = S.started_at
WHEN MATCHED THEN UPDATE SET
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
  (source, started_at, ended_at, duration_seconds, distance_m,
   active_calories, avg_hr, max_hr, heart_rates, raw, ingested_at)
VALUES
  (S.source, S.started_at, S.ended_at, S.duration_seconds, S.distance_m,
   S.active_calories, S.avg_hr, S.max_hr, S.heart_rates, S.raw, CURRENT_TIMESTAMP())
"""


# NOTE: /healthz は *.run.app では Google Frontend に予約されておりアプリに届かない
@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/runs", dependencies=[Depends(require_token)])
async def ingest_run(request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(payload, dict):
        raise HTTPException(422, "body must be a JSON object")

    row = normalize(payload)
    params = [
        bigquery.ScalarQueryParameter("source", "STRING", row["source"]),
        bigquery.ScalarQueryParameter("started_at", "STRING", row["started_at"]),
        bigquery.ScalarQueryParameter("ended_at", "STRING", row["ended_at"]),
        bigquery.ScalarQueryParameter("duration_seconds", "FLOAT64", row["duration_seconds"]),
        bigquery.ScalarQueryParameter("distance_m", "FLOAT64", row["distance_m"]),
        bigquery.ScalarQueryParameter("active_calories", "FLOAT64", row["active_calories"]),
        bigquery.ScalarQueryParameter("avg_hr", "FLOAT64", row["avg_hr"]),
        bigquery.ScalarQueryParameter("max_hr", "FLOAT64", row["max_hr"]),
        bigquery.ScalarQueryParameter("heart_rates", "STRING", row["heart_rates"]),
        bigquery.ScalarQueryParameter("raw", "STRING", row["raw"]),
    ]
    job = bq.query(MERGE_SQL, job_config=bigquery.QueryJobConfig(query_parameters=params))
    job.result()
    inserted = job.num_dml_affected_rows == 1  # MERGE は insert/update とも 1 を返すが目安として
    logger.info("merged run source=%s started_at=%s affected=%s",
                row["source"], row["started_at"], job.num_dml_affected_rows)
    return {
        "ok": True,
        "source": row["source"],
        "started_at": row["started_at"],
        "distance_m": row["distance_m"],
        "hr_samples": len(json.loads(row["heart_rates"])),
        "affected_rows": job.num_dml_affected_rows,
        "inserted_or_updated": inserted,
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
