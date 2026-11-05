# RS3 XP Planner

Track your RuneScape 3 XP gains over time and plan every skill to **99, 120 or 200m XP**.
It's built on the [RuneScape Platform API](https://developer.runescape.com/platform/concepts.html)
that Jagex launched in November 2026. It runs on your own PC, keeps its data in a local
SQLite file and never needs an account or API key.

**[Try the demo](docs/index.html)**: a read-only copy of the dashboard with a real
account's data. To serve it at `https://<user>.github.io/<repo>/`, turn on GitHub Pages
for the `docs/` folder.

## What it does

- **XP history.** Hiscores only show your current XP, and the API has no history
  endpoint, so the planner checks Hiscores hourly and saves every change. Gains are shown
  for today (since the 00:00 UTC reset), this week (since the Wednesday reset), this month
  and this year. There are charts by skill and by month.
- **Goals for 99, 120 and 200m.** For every skill it shows virtual levels past the cap,
  progress bars, the XP left and a finish date. The finish date can use your own recent
  pace (7, 30, 90 or 365 days), the fastest or easiest training methods, or your own XP/hr.
- **Training routes.** For each skill it lists which method to use from each level to the
  goal, and how many hours each stretch takes:
  - **Fastest:** the highest XP/hr unlocked at each level.
  - **Easiest:** AFK methods first, then low-effort ones.
  - **Rates:** base rates come from the RuneScape Wiki's training guides (checked
    November 2026) and live in [`rsxp/methods.json`](rsxp/methods.json). Each one is
    tagged as a wiki figure, a third-party guide figure or an estimate.
  - **Excluded from the hours:** expensive methods (unless you allow them) and methods
    with no published rate. Both are still listed.
- **XP boosts.** Clan avatar, skilling outfit, portable stations and Double XP all scale
  the routes. The Wisdom aura and the Premier artefact were removed from the game in 2026,
  so they aren't offered.
- **Everything else the API offers:** per-skill ranks, Hiscores activities (RuneScore,
  clue scrolls, minigames), quests (quest points, ready to start, in progress, recently
  completed) and the Adventurer's Log.

## Install and run

Needs Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

```bash
python -m rsxp serve
```

Open http://127.0.0.1:8765 and use **Track** to add a display name. On Windows you can
double-click `start_planner.bat` instead. History only builds while the server is running,
so start it when you log in (Task Scheduler, or a shortcut in `shell:startup`).

**Please set a contact** so Jagex can reach you if your copy misbehaves. Their
[best practices](https://developer.runescape.com/platform/best-practices.html) ask every
client to identify itself:

```bash
set RSXP_CONTACT=@yourname on Discord
```

That's for Windows `cmd`; use `export RSXP_CONTACT=...` on macOS or Linux. You can also
replace the whole header with `RSXP_USER_AGENT`.

## Commands

| Command | What it does |
|---|---|
| `python -m rsxp serve` | Run the dashboard and the hourly collector |
| `python -m rsxp add "Name"` | Track a player |
| `python -m rsxp poll ["Name"]` | Check Hiscores now: one player, or every player that's due |
| `python -m rsxp plan "Name" --target 120 --window 30` | Print the plan in the terminal |
| `python -m rsxp import-backfill "Name"` | Load saved RuneMetrics monthly XP (see below) |
| `python -m rsxp export-demo "Name" [--as "Demo Player"] [--repo URL]` | Rebuild the static demo in `docs/` |
| `python -m pytest tests` | Run the tests |

## How it treats the API

All requests go through one client ([`rsxp/client.py`](rsxp/client.py)), which works like this:

- One request at a time, with at least 5 seconds plus jitter between requests.
- An identifying User-Agent on every request.
- Redirects are never followed.
- Only 429, 5xx and network errors are retried, honouring `Retry-After`.
- After 3 failed calls in a row, all requests pause for 30 minutes.
- Hiscores are checked hourly; they only change when you log out or hop worlds.
- "Update now" works at most once per 5 minutes per player.
- Player Events are fetched every 30 minutes, and Player Quests every 6 hours.
- Players that Hiscores can't find are checked once a day.

## History before you installed it

RuneMetrics' monthly XP endpoint went offline on 10 November 2026 and has no replacement.
If you saved its responses with [`runemetrics_backfill.py`](runemetrics_backfill.py) before
then, `import-backfill` turns them into month-start snapshots. Gains inside those months
are interpolated, and the dashboard labels them as estimates.

## Project layout

| Path | Contents |
|---|---|
| `rsxp/client.py` | Rate-limited API client |
| `rsxp/db.py` | SQLite schema and storage |
| `rsxp/collector.py` | Polling schedule |
| `rsxp/gains.py` | XP-over-time queries |
| `rsxp/planner.py` | Goals and finish dates |
| `rsxp/training.py` | Training routes |
| `rsxp/methods.json` | Training methods and boosts |
| `rsxp/server.py` | Local API |
| `rsxp/static/index.html` | Dashboard |
| `rsxp/demo.py` | Static demo export |
| `docs/` | Built demo for GitHub Pages |
| `design/` | Phase 1 blueprint |
| `tests/` | Tests |

## Disclaimer

This is a fan project, not affiliated with or endorsed by Jagex. RuneScape is a trademark
of Jagex Ltd. Training rates are estimates for planning, not guarantees.

## License

[MIT](LICENSE)
