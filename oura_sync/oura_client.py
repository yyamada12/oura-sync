"""Oura API v2 からのデータ取得 (ページング対応)。"""
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from . import config
from .oura_auth import get_access_token


@dataclass(frozen=True)
class Endpoint:
    name: str          # API パス末尾 / シートのタブ名
    mode: str          # "date" | "datetime" | "none" | "single"
    key: tuple = ("id",)  # upsert キーになるフィールド


# OpenAPI 1.37 (2026-08 時点) の /v2/usercollection 配下
ENDPOINTS: list[Endpoint] = [
    Endpoint("personal_info", "single"),
    Endpoint("daily_activity", "date"),
    Endpoint("daily_readiness", "date"),
    Endpoint("daily_sleep", "date"),
    Endpoint("daily_spo2", "date"),
    Endpoint("daily_stress", "date"),
    Endpoint("daily_resilience", "date"),
    Endpoint("daily_cardiovascular_age", "date"),
    Endpoint("sleep", "date"),
    Endpoint("sleep_time", "date"),
    Endpoint("workout", "date"),
    Endpoint("session", "date"),
    Endpoint("enhanced_tag", "date"),
    Endpoint("rest_mode_period", "date"),
    Endpoint("vO2_max", "date"),
    Endpoint("ring_configuration", "none"),
    Endpoint("heartrate", "datetime", key=("timestamp", "source")),
    Endpoint("ring_battery_level", "datetime", key=("timestamp",)),
]
# 旧 `tag` は enhanced_tag に置き換わっているため対象外


def _get(path: str, params: dict) -> dict:
    for attempt in range(5):
        r = requests.get(
            f"{config.API_BASE}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {get_access_token()}"},
            timeout=60,
        )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 30))
            print(f"  rate limited, {wait}s 待機")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(2**attempt)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{path} {r.status_code}: {r.text}")
        return r.json()
    raise RuntimeError(f"{path}: リトライ上限")


def fetch(ep: Endpoint, start: date, end: date) -> list[dict]:
    if ep.mode == "single":
        return [_get(ep.name, {})]

    params: dict = {}
    if ep.mode == "date":
        params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    elif ep.mode == "datetime":
        # heartrate 系は datetime 指定。end は翌日 0 時まで含める
        s = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        e = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        params = {"start_datetime": s.isoformat(), "end_datetime": e.isoformat()}

    out: list[dict] = []
    next_token = None
    while True:
        p = dict(params)
        if next_token:
            p["next_token"] = next_token
        body = _get(ep.name, p)
        out.extend(body.get("data", []))
        next_token = body.get("next_token")
        if not next_token:
            break
    return out
