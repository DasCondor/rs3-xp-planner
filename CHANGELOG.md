# Changelog

## Unreleased

- Up to a year of history again. Monthly totals saved from RuneMetrics before it closed are
  read from your local database (nothing is fetched). They join hourly Hiscores snapshots
  and Adventurer's Log milestones.
- `.gitattributes` keeps line endings consistent. `docker-compose.override.yml` is
  gitignored for per-machine tweaks, like mounting an existing `./data` folder.

## v0.2.0 (November 2026)

### Highlights

- **Docker.** `docker compose up -d` runs the planner 24/7 and keeps your history in a
  volume. Set `RSXP_CONTACT` and `RSXP_PLAYERS` in `.env` (copy `.env.example`).
- **Only the RuneScape Platform API.** RuneMetrics is discontinued on 10 Nov 2026, so nothing
  depends on it any more. History comes from hourly Hiscores snapshots plus your
  Adventurer's Log: every 2M XP milestone and level-up is an XP point.
- **Per-skill outfits and portables.** Pick the ones you own. Only skills that have one are
  listed, each at its real bonus: 5-6% for outfits, 21% for the portable range, 10% for the
  other portables.
- **A new look.** A skill-sheet layout with skill-group colours on names, bars and charts,
  plus a light/dark/auto theme switch.

### Changed

- The default pace is now Fastest methods, so a fresh install has ETAs straight away.
- Charts show per-day bars while history is short. `#woodcutting` (or any skill) in the
  address opens that skill.
- A clearer skills summary line, clearer column headers and darker text in the light theme.
  ETAs over 50 years show as "50+ yr".
- "Gold sinks" are now called **Pricey Options** and are grouped on one line per route.
- Quests and the Adventurer's Log show 8 entries with a "show all" link instead of scroll
  boxes.
- New settings: `RSXP_PLAYERS` and `RSXP_DB`.
- CI builds and smoke-tests the Docker image, and syntax-checks the dashboard script.

### Removed

- `runemetrics_backfill.py` and the `import-backfill` command.

### Fixed

- Charts no longer spill out of their panels after the window is resized.
- Smithing no longer gets a portable bonus (the portable forge was removed from the game).
- The demo no longer fails to load on GitHub Pages.

### Upgrading from v0.1.0

- Your database carries over as it is. Snapshots imported from RuneMetrics are kept but no
  longer read.
- Outfit and portable choices are now per skill, so tick yours again under **Boosts → outfits
  & portables**.

## v0.1.0 (November 2026)

First release.

- Local tracker built on the RuneScape Platform API: Hiscores Index Lite, Player Events
  and Player Quests.
- **Collector:** checks Hiscores hourly and saves only real changes. Requests are spaced
  out with jitter, use an identifying User-Agent, retry and back off politely, and pause
  behind a circuit breaker after repeated failures.
- **Gains:** daily (UTC reset), weekly (Wednesday reset), monthly and yearly gains, with
  charts by skill and by month.
- **Planner:** goals of 99, 120 and 200m with virtual levels and Invention's elite curve.
  Finish dates use your recent pace, the fastest or easiest training methods, or your own
  XP/hr.
- **Training routes:** level-by-level methods from RuneScape Wiki rates, including Colossi
  Divination and Fenmoor Totems from 2026. Supports XP boosts and an expensive-methods switch.
- **Other panels:** Hiscores activities, quests and the Adventurer's Log.
- **RuneMetrics:** a one-time monthly XP backfill and importer (RuneMetrics retired on
  10 November 2026).
- **Demo:** a static GitHub Pages demo built with `python -m rsxp export-demo`.
