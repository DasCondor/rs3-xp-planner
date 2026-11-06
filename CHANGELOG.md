# Changelog

## Unreleased

- "Your pick" for combat skills: choose from 21 creatures (combat XP per kill from the wiki;
  Slayer locks applied) and enter your kills per hour, to get XP/hr, hours and weeks to the goal.
  Hitpoints gets about a third. The creatures live in one shared table in `methods.json`, which
  replaces 17 scattered per-kill rows.
- A "Closest to 120" panel (it follows the chosen goal) under the by-skill chart lists the six
  skills nearest the goal, with XP left and ETA. Click one to open it.
- Ravenous strigoi: Ranged 247,680 XP/hr and Slayer 218,352 XP/hr at 4 kills per 5 minutes,
  needing 104 Slayer.
- The server sends `Cache-Control: no-cache` for the page and its files, so an update is never
  run against a stale cached `app.js`.
- Slayer creatures as combat training options: corrupted scorpions, abyssal demons, gargoyles,
  abyssal beasts, and abyssal savages, beasts and armoured phantoms for Necromancy. They only
  count once your Slayer level is high enough; otherwise they're listed with what's missing.
  Also new Ranged rates: the Abyss with red chinchompas (702k/hr) and tunnel waterfiends.
- `methods.json` uses compact rows (`[level, name, xp/hr, effort, {extras}]`), about 30% smaller.
  Dead code is removed.
- The dashboard is split into `index.html`, `app.css`, `app.js`, `theme.js` and `logo.svg`, with
  nothing inlined. This also fixes the browser-tab icon, whose inline data URI was broken.
- The demo site is no longer committed. `demo/fixture.json` holds the raw data (one file,
  about 35 KB), and a GitHub Actions workflow builds and deploys the site on each push. This
  replaces the generated `docs/` folder (an HTML copy plus 51 JSON files).
- The Docker image uses Python 3.15, and CI tests on 3.10 and 3.15.
- README screenshots moved to `assets/`. New commands: `demo-fixture` and `build-demo` (they
  replace `export-demo`).
- The "99" logo is shown in the top bar and used as the browser-tab icon.
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
