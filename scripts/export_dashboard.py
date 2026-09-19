"""Export a private dashboard snapshot; never copy credentials into the Site."""
import argparse, datetime as dt, hashlib, json, pathlib, re
ROOT = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--runs', required=True, help='JSON output of the documented BQ query')
a = p.parse_args()
raw = json.loads(pathlib.Path(a.runs).read_text())
runs = []
for r in raw:
    instant = dt.datetime.fromisoformat(r['started_at'].replace('Z', '+00:00'))
    if instant.tzinfo is None: instant = instant.replace(tzinfo=dt.timezone.utc)
    local = instant.astimezone(dt.timezone(dt.timedelta(hours=9)))
    runs.append({'date':local.date().isoformat(), 'time':local.strftime('%H:%M'), 'km':float(r['distance_m'])/1000 if r.get('distance_m') is not None else None, 'seconds':float(r['duration_seconds']) if r.get('duration_seconds') is not None else None, 'source':r.get('source_name') or ''})
plan_text = (ROOT/'training-plan-2027-02-14.md').read_text()
plans = []
for line in plan_text.splitlines():
    cells = [s.strip() for s in line.strip('|').split('|')]
    if len(cells) < 3: continue
    match = re.fullmatch(r'(?:((?:20)\d{2})/)?(\d{1,2})/(\d{1,2})（[月火水木金土日]）(朝|夜)?', cells[0])
    if not match: continue
    year, month, day, slot = match.groups(); month = int(month)
    date = dt.date(int(year or (2026 if month >= 9 else 2027)), month, int(day)).isoformat()
    rest = '休' in cells[1] and not re.search(r'\d+km', cells[1])
    distance = re.search(r'(\d+(?:\.\d+)?)km', cells[1])
    plans.append({'date':date,'slot':slot or '', 'text':cells[1], 'note':cells[2], 'km':0 if rest else (float(distance.group(1)) if distance else None), 'rest':rest})
if not plans: raise SystemExit('No dated daily plans found; preserve previous snapshot.')
if len({x['date'] for x in plans}) != len(plans): raise SystemExit('Duplicate plan dates: reconcile before export.')
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds')
out = ROOT/'dashboard-site/dist/data.json'
old = json.loads(out.read_text()) if out.exists() else {}
versions = old.get('planVersions', [])
digest = hashlib.sha256(json.dumps(plans, sort_keys=True).encode()).hexdigest()
if not versions or versions[-1]['hash'] != digest:
    versions.append({'recordedAt':now,'hash':digest,'plans':plans})
data = {'updatedAt':now, 'runs':runs, 'plans':plans, 'planVersions':versions, 'raceDate':'2027-02-14'}
out.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print('Exported {} runs, {} daily plans, {} plan revisions.'.format(len(runs),len(plans),len(versions)))
