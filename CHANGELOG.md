# Changelog

## Unreleased

- Docker: `Dockerfile` and `docker-compose.yml`. `docker compose up -d` runs the planner
  24/7, with history kept in a volume.
- New settings: `RSXP_PLAYERS` tracks names on startup, and `RSXP_DB` sets where the
  database lives.
- CI builds the Docker image and smoke-tests it.
- Dashboard redesign: a skill-sheet layout, game-style labels and skill-group colours on
  names, bars and charts.
- Outfits and portables are picked per skill, and only skills that have one are listed.
  Each uses its real bonus: 5-6% for outfits, the portable range 21%, other portables 10%.
  There is no Smithing portable any more.
- Theme switch in the top bar: auto, light or dark, remembered per browser.
- Easier reading: clearer column headers, darker text in the light theme, and a tidier
  "not counted above" box. Quests and the Adventurer's Log show 8 entries with a "show
  all" link instead of scroll boxes.
- Fix: charts no longer spill out of their panels after the window is resized.
- Fix: the demo export no longer depends on the page title.

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
