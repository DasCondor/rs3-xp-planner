# Changelog

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
