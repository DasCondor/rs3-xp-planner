"""Command line: python -m rsxp <command>"""
import argparse
import logging
import os
import sys
import time
from contextlib import closing
from pathlib import Path

from . import collector, db, planner

ROOT = Path(__file__).resolve().parent.parent


def fmt_eta(days):
    if days is None:
        return "no recent XP"
    if days == 0:
        return "done"
    for unit, size in (("yr", 365), ("mo", 30.44), ("wk", 7)):
        if days >= size * (2 if unit != "yr" else 1):
            return f"{days / size:.1f} {unit}"
    return f"{days:.0f} d"


def need_player(conn, name):
    p = db.get_player(conn, name)
    if not p:
        sys.exit(f"{name} isn't tracked yet. Run: python -m rsxp add \"{name}\"")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rsxp", description="RS3 XP gains tracker and planner")
    ap.add_argument("--db", default=os.environ.get("RSXP_DB") or str(db.DEFAULT_DB),
                    help="SQLite file (default: $RSXP_DB, else data/rsxp.sqlite3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add", help="track a player").add_argument("name")
    p = sub.add_parser("poll", help="fetch Hiscores now (one player, or all that are due)")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("import-backfill", help="load runemetrics_backfill.py output")
    p.add_argument("name")
    p.add_argument("--dir", help="default: rm_backfill/<name>")
    p = sub.add_parser("plan", help="print the plan for one target")
    p.add_argument("name")
    p.add_argument("--target", choices=["99", "120", "200m"], default="120")
    p.add_argument("--window", type=int, choices=planner.RATE_WINDOWS, default=30)
    p = sub.add_parser("export-demo", help="build the static GitHub Pages demo from one player's data")
    p.add_argument("name")
    p.add_argument("--out", default=str(ROOT / "docs"))
    p.add_argument("--as", dest="display_as", default="Demo Player",
                   help='name shown in the demo (default "Demo Player"; pass the real name to keep it)')
    p.add_argument("--repo", default="auto",
                   help='repository URL to link from the demo; "auto" (default) works it out on GitHub Pages')
    p = sub.add_parser("serve", help="run the dashboard and the hourly collector")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-collector", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.cmd == "serve":
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(args.db, collect=not args.no_collector), host=args.host, port=args.port)
        return

    with closing(db.connect(args.db)) as conn:
        if args.cmd == "add":
            pid = db.add_player(conn, args.name)
            print(f"Tracking {db.get_player(conn, pid)['display_name']} (id {pid})")
        elif args.cmd == "poll":
            if args.name:
                print(collector.poll(conn, need_player(conn, args.name)))
            else:
                print(f"polled {collector.run_due(conn)} player(s)")
        elif args.cmd == "export-demo":
            from .demo import export
            n = export(conn, args.name, Path(args.out), args.display_as, args.repo)
            print(f"wrote {Path(args.out) / 'index.html'} and {n} data files")
        elif args.cmd == "import-backfill":
            player = need_player(conn, args.name)
            folder = Path(args.dir) if args.dir else ROOT / "rm_backfill" / db.name_key(args.name).replace(" ", "_")
            print(f"wrote {db.import_runemetrics(conn, player['id'], folder)} snapshots from {folder}")
        elif args.cmd == "plan":
            player = need_player(conn, args.name)
            s = planner.summary(conn, player["id"], int(time.time()), args.window)
            print(f"{player['display_name']} -> {args.target}, pace = last {args.window} days\n")
            print(f"{'Skill':<14}{'Lvl':>5}{'XP':>14}{'XP/day':>11}{'Left':>14}  ETA")
            for sk in s["skills"][1:]:
                t = sk["targets"] and sk["targets"][args.target]
                if not t:
                    print(f"{sk['name']:<14}  no data")
                    continue
                rate = f"{sk['rate_per_day']:,.0f}" if sk["rate_per_day"] else "-"
                print(f"{sk['name']:<14}{sk['virtual_level'] or '':>5}{sk['xp']:>14,}{rate:>11}"
                      f"{t['remaining']:>14,}  {fmt_eta(t['eta_days'])}")
            tot = s["totals"][args.target]
            print(f"\nAll {args.target}: {tot['skills_left']} skills left, {tot['remaining']:,} XP, "
                  f"last one done in {fmt_eta(tot['eta_days'])}")


if __name__ == "__main__":
    main()
