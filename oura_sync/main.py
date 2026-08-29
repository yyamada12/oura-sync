import argparse
from datetime import date, timedelta

from .oura_auth import authorize
from .oura_client import ENDPOINTS, fetch
from .sheets import SheetStore


def cmd_sync(args):
    end = date.fromisoformat(args.until) if args.until else date.today()
    start = date.fromisoformat(args.since) if args.since else end - timedelta(days=args.days)
    targets = [e for e in ENDPOINTS if not args.only or e.name in args.only]

    store = SheetStore()
    for ep in targets:
        print(f"[{ep.name}] {start} .. {end}")
        # 時系列系はデータ量が多いので 30 日ずつ
        step = 30 if ep.mode == "datetime" else 3650
        s = start
        total_u = total_a = 0
        while s <= end:
            e = min(s + timedelta(days=step - 1), end)
            docs = fetch(ep, s, e)
            u, a = store.upsert(ep.name, docs, ep.key)
            total_u += u
            total_a += a
            s = e + timedelta(days=1)
            if ep.mode in ("single", "none"):
                break
        print(f"  updated={total_u} added={total_a}")


def main():
    p = argparse.ArgumentParser(prog="oura-sync")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="Oura の OAuth 認可を行い tokens.json を作る")
    s = sub.add_parser("sync", help="Oura → Google Sheets に取り込む")
    s.add_argument("--since", help="開始日 YYYY-MM-DD (省略時は --days 日前)")
    s.add_argument("--until", help="終了日 YYYY-MM-DD (省略時は今日)")
    s.add_argument("--days", type=int, default=7, help="--since 省略時の遡り日数 (既定 7)")
    s.add_argument("--only", nargs="*", help="対象エンドポイント名を限定")
    args = p.parse_args()
    if args.cmd == "auth":
        authorize()
    else:
        cmd_sync(args)


if __name__ == "__main__":
    main()
