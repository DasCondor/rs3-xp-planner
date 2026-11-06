# Changelog

## Unreleased

Security pass:

- The local server only answers to `127.0.0.1` / `localhost` (plus anything in `RSXP_HOSTS`),
  and refuses changes from other websites. Before this, a web page you visited could in theory
  reach it through your browser (DNS rebinding) or make it hit Jagex for you.
- The page sends no-framing / no-sniffing / no-referrer headers and has a content security
  policy: no inline scripts or `onerror=` style handlers, and nothing from other sites except
  the one Chart.js file, which is pinned with an integrity hash so a tampered copy won't run.
  The demo's settings moved from an inline script to `config.js` to fit the policy.
- Numbers and text from Jagex are checked as they come in. Junk where a number should be is
  dropped instead of stored.
- If Jagex sends something the code can't read, that player waits the normal hour instead of
  being asked again every minute. Same for the history import. Answers over 2 MB are refused.
- At most 25 tracked names by default (`RSXP_MAX_PLAYERS`). `/docs` is gone.
- Docker: read-only filesystem, all capabilities dropped, no privilege escalation, memory and
  process limits. Dependencies and GitHub Actions are pinned to exact versions, and the tests
  workflow gets a read-only token.
- Needs Python 3.15, the same version the Docker image uses. That's the only version tested.

Also:

- Invention shows as locked until Crafting, Divination and Smithing are all 80, and says which
  ones are short. Its hours still count, starting from the unlock. The time to get those three
  to 80 is already in their own routes.
- Levels stop at 120 like in game. Hitpoints, Necromancy and a few others were showing up to 126.
- New players get a year of history: the first time a name is tracked, it pulls their monthly
  XP from RuneMetrics once (until RuneMetrics shuts on 10 Nov 2026). If their profile is
  private the page tells you, and it checks again every hour.
- A Players table shows up once you track two or more names. Same goal, pace and boosts for
  everyone, so you can see who's closer. Click a skill to compare just that one, or a row to
  switch player.
- The demo has two players now, Demo Player 1 and 2.
- The player dropdown was unreadable in dark mode. Fixed.
- Combat skills get a "Your pick" box: choose one of 21 creatures, type how many you kill an
  hour, and it works out XP/hr and how long you've got left. Hitpoints gets about a third.
- "Closest to 120" (or whatever goal you've picked) sits under the by-skill chart: the six
  skills nearest the goal. Click one to open it.
- Ravenous strigoi for Ranged (247,680/hr) and Slayer (218,352/hr), at 4 kills per 5 minutes.
  Needs 104 Slayer.
- Slayer creatures count as combat training once your Slayer level is high enough: corrupted
  scorpions, abyssal demons, gargoyles, abyssal beasts, and savages, beasts and armoured
  phantoms for Necromancy. Until then they're listed with what you're missing. Also red chins
  in the Abyss (702k/hr) and tunnel waterfiends for Ranged.
- `methods.json` is about 30% smaller (one short row per method). Some dead code removed.
- The page is split into `index.html`, `app.css`, `app.js`, `theme.js` and `logo.svg`. That
  also fixed the tab icon. Files are sent with `no-cache` so an update never runs old JS.
- The demo isn't committed any more. GitHub builds it from `demo/fixture.json` on every push,
  which got rid of the `docs/` folder and its 51 JSON files.
- Docker runs Python 3.15.
- The "99" logo is in the top bar and the browser tab.
- Up to a year of history again, from monthly totals saved before RuneMetrics closed.
- Screenshots moved to `assets/`. `demo-fixture` and `build-demo` replace `export-demo`.
  Line endings are pinned with `.gitattributes`, and `docker-compose.override.yml` is ignored
  for your own local tweaks.

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
