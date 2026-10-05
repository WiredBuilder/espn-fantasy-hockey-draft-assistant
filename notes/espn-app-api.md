# ESPN app "Trending & Recommended players": where the data comes from

Checked 2026-10-04 against league 41078 with the league cookies, read-only. The ESPN mobile app's
Players tab shows a "Trending & Recommended players" card that the web app does not. Both halves turn
out to be reachable from the same `kona_player_info` view the repo already uses; no app-only endpoint
was needed for Trending, and Recommended looks like a sort of the same data.

## Trending: reproduced exactly

The app card on 2026-10-04 (iPhone screenshot) read: P. Cotter LW, Rost 28% +12.7; L. Cagnoni D,
Rost 29% +5.8; C. Perfetti, Rost 12%.

The league players endpoint with this `x-fantasy-filter` returns the same list in the same order:

```json
{"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
             "limit": 15,
             "sortPercChanged": {"sortPriority": 1, "sortAsc": false}}}
```

Response, 2026-10-04 23:30 UTC (name, `ownership.percentChange`, `ownership.percentOwned`):
Paul Cotter 12.6 / 28.0; Joey Daccord 6.3 / 39.4; Luca Cagnoni 5.6 / 29.6; Cole Perfetti 5.5 / 12.2;
Vasily Podkolzin 4.2 / 64.4; Tommy Novak 3.8 / 5.4; Tristan Luneau 3.7 / 6.4; Jamie Oleksiak 3.4 / 9.2.

So "Trending" is `ownership.percentChange` (ESPN's own window, not ours) sorted descending among free
agents. `refresh.py` stores `percentOwned` but drops `percentChange`; the checkup today derives a trend
from the difference between our last two pulls instead, which is a longer and noisier window (Cotter
reads +27.9 over five days versus ESPN's +12.6). Storing `percentChange` is the fix (card chunk 4).

Fields per player object in this view that the repo does not store yet:
- `ownership.percentChange`, `ownership.percentStarted`, `ownership.activityLevel`
- `ratings`: ESPN's Player Rater under the league's scoring, keyed by stat split
  (`{"0": {"positionalRanking": 1, "totalRanking": 4, "totalRating": 8.0}, ...}`). Cotter's
  `totalRanking` 4 means fourth-best free agent in points so far this season.
- `status` (FREEAGENT / WAIVERS / ONTEAM), `lineupLocked`, `tradeLocked`, `droppable`

No field or string in the response contains "recommend".

## Recommended: most likely a sort of the same data, phone step still open

Two candidate sorts work on the API:

- `sortAppliedStatTotal` with value `"012027"` (season-to-date actuals under league scoring) returned
  Paul Cotter, Arturs Silovs, Vasily Podkolzin, Dylan Garand, Easton Cowan.
- `ratings.totalRanking` (Player Rater) is in every player object and can be sorted client-side.
- `sortRatings` as a server-side sort returned ESPNUnknownError.

Hypothesis: the app's Recommended tab is the Player Rater (`ratings`) or recent actual points among
free agents, possibly filtered to the user's open or weak positions. The one thing that settles it is
watching the app's own request once: Proxyman or Charles on the Mac with its CA profile on the phone,
open Players, tap Recommended, remove the profile. Ten minutes of Blake's time; cookies and tokens stay
on the phone and in .env, never in a note or chat.

## Decision

- Trending: ingest `ownership.percentChange` in `refresh.py` (new column `pct_change` on
  `player_snapshots`) and print it in the checkup's Trending block in place of the two-pull delta.
- Recommended: keep the fallback ready (free agents ranked by `ratings.totalRanking` plus games this
  week), and confirm against the app's request when Blake has ten minutes. Either way the checkup can
  show an "ESPN rates" block from `ratings` now.

Read-only throughout. Nothing here writes to ESPN.
