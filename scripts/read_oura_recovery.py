"""Read Oura recovery directly; never writes Sheets/BQ or prints credentials."""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oura_sync.oura_client import Endpoint, fetch

FIELDS = {
    'daily_sleep': ('id', 'day', 'score', 'timestamp'),
    'daily_readiness': ('id', 'day', 'score', 'timestamp', 'temperature_deviation'),
    'sleep': ('id', 'day', 'type', 'bedtime_start', 'bedtime_end',
              'total_sleep_duration', 'average_heart_rate', 'lowest_heart_rate', 'average_hrv'),
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=dt.date.fromisoformat)
    parser.add_argument('--days', type=int, default=28)
    args = parser.parse_args()
    if not 1 <= args.days <= 28:
        parser.error('--days must be 1..28')
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    day = args.date or now.date()
    start = day - dt.timedelta(days=args.days - 1)
    result = {'source': 'Oura API', 'fetched_at': now.isoformat(),
              'target_day': day.isoformat(), 'start_date': start.isoformat(), 'end_date': day.isoformat(), 'data': {}, 'errors': {}}
    for name, fields in FIELDS.items():
        try:
            rows = fetch(Endpoint(name, 'date'), start, day + dt.timedelta(days=1))
            result['data'][name] = [{k: row.get(k) for k in fields} for row in rows
                                   if start.isoformat() <= row.get('day', '') <= day.isoformat()]
        except (Exception, SystemExit) as exc:
            # API error bodies and authentication errors can contain secrets.
            result['errors'][name] = type(exc).__name__ + ': request failed; check authentication/network without printing secrets'
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['errors'] else 0

if __name__ == '__main__':
    raise SystemExit(main())
