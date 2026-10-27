# Plan: dashboard date-range filter (ticket 005)

Goal: let the user pick a date range on the dashboard. The stat tiles, the per-beer bars, the
pours-over-time chart and the tap-card pour counts then reflect only that range. With no range
set, behaviour is exactly as it is today ("all time").

Read `tickets/005-date-filter.md` for the original request. Do NOT touch anything described in
`tickets/006-lobby-dashboard.md` (tap-card maintenance/error details) - that is a separate ticket.

Stack: Python + FastAPI + SQLite, vanilla JS frontend with no build step. Code is in `src/brewops/`.
Run the app with `uv run start` (http://localhost:8123). Run tests with `uv run pytest`.

## Decisions (already made, do not re-litigate)

- Granularity is whole days. Dates are `YYYY-MM-DD` strings.
- Both bounds are optional and **inclusive**. Either one can be given alone.
- Query parameter names are `date_from` and `date_to` (`from` is a Python keyword).
- No params = all time (current behaviour, existing tests must still pass unchanged).
- Filtered on a tap card: `brew_count` and `specialty`. NOT filtered: `last_brew`,
  `last_maintenance`, `recent_errors` (these are "most recent" facts, not counts).
- `per_day` keeps returning only days that have at least one brew (no zero-filling of gaps).
  `per_drink` keeps returning every drink type, with `count: 0` for beers not poured in the range.
- The range is not saved in the URL or storage. It resets on page reload.

## How data flows today (context)

1. `src/brewops/db/queries.py` -> `get_stats(conn)` runs 3 unfiltered SQL queries and returns
   `{"total_brews", "per_drink": [{name,label,count}], "per_day": [{day,count}]}`.
2. `src/brewops/api/main.py` -> `stats()` handler for `GET /api/stats` calls `get_stats(conn)`.
3. `src/brewops/db/queries.py` -> `get_tap_health(conn, tap_id)` builds one tap card
   (`brew_count`, `last_brew`, `last_maintenance`, `recent_errors`, `specialty`).
   Served by `tap_health()` in `main.py` for `GET /api/taps/{tap_id}`.
4. `src/brewops/frontend/app.js` -> `loadDashboard()` fetches `/api/stats`, `/api/taps`, then
   `/api/taps/{id}` for each tap, and calls `renderDrinkBars`, `renderTimeline`, `renderTapCards`.
5. Timestamps in `brew_events.timestamp` are TEXT like `2026-06-01 08:00:00` (naive local time).
   There is an index `idx_brew_events_timestamp`.

## Step 1 - query layer: `src/brewops/db/queries.py`

Add a private helper near the top of the file (after imports):

```python
def _range_bounds(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    """Turn inclusive 'YYYY-MM-DD' bounds into a half-open [start, end) timestamp range."""
```

- `start` = `date_from + " 00:00:00"`, or `None` if `date_from` is None.
- `end` = midnight of the day AFTER `date_to` (use `datetime.strptime(...)` + `timedelta(days=1)`,
  format `%Y-%m-%d 00:00:00`), or `None` if `date_to` is None.
- Reason: timestamps include a time, so `timestamp <= '2026-06-07'` would wrongly drop
  `2026-06-07 09:00:00`. Always compare `timestamp >= start AND timestamp < end`.
- Do not wrap the column in `DATE()` in the WHERE clause (it would bypass the index).

Add a second small helper that builds the SQL fragment and params, e.g.
`_range_clause(start, end, column="timestamp") -> tuple[str, list[str]]`, returning
`("timestamp >= ? AND timestamp < ?", [start, end])`, with only the parts that apply, or
`("", [])` when both are None. Callers join with `AND` / `WHERE` themselves.

### `get_stats(conn, date_from=None, date_to=None)`

Change the signature (new args optional, default None). Compute bounds once, then:

- `total`: `SELECT COUNT(*) FROM brew_events` + `WHERE <range>` when a range is set.
- `per_drink`: **put the range in the JOIN condition, not in WHERE**:
  ```sql
  FROM drink_types dt
  LEFT JOIN brew_events be ON be.drink_type = dt.name AND be.timestamp >= ? AND be.timestamp < ?
  ```
  Putting it in `WHERE` would turn the LEFT JOIN into an inner join and beers with zero pours in
  the range would vanish from the bars. Keep `GROUP BY dt.id ORDER BY dt.id`.
- `per_day`: `WHERE <range>` before `GROUP BY DATE(timestamp)`.
- Use `?` placeholders and pass params. Never f-string user input into SQL.

### `get_tap_health(conn, tap_id, date_from=None, date_to=None)`

- Add the same optional args.
- `brews` query: split it. `count` gets the range filter. `last_brew` (`MAX(timestamp)`) must NOT
  be range-filtered, so run it as a separate query (or use `COUNT(CASE WHEN ... END)`), but keep the
  returned keys `brew_count` and `last_brew` unchanged.
- `specialty` query: add the range to its WHERE (`WHERE be.tap_id = ? AND <range>`).
  If no brews are in range, it already returns `None` -> `"specialty": None`.
- Leave `last_maintenance` and `recent_errors` queries untouched.

## Step 2 - API: `src/brewops/api/main.py`

Add a validator next to `parse_timestamp`:

```python
def parse_date_range(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
```

- Treat an empty string as None (the frontend may send blanks).
- Parse each non-empty value with `datetime.strptime(value, "%Y-%m-%d")`. On failure raise
  `HTTPException(400, f"unparsable date {value!r}")`.
- If both are set and `date_from > date_to` raise `HTTPException(400, "date_from is after date_to")`.
- Return the normalized `YYYY-MM-DD` strings. Do NOT reject future dates (unlike `parse_timestamp`).
  `date_from == date_to` is valid (a single day).

Update the handlers:

```python
@app.get("/api/stats")
def stats(date_from: str | None = None, date_to: str | None = None, conn=Depends(get_db)):
    start, end = parse_date_range(date_from, date_to)
    return queries.get_stats(conn, start, end)

@app.get("/api/taps/{tap_id}")
def tap_health(tap_id: int, date_from: str | None = None, date_to: str | None = None, conn=Depends(get_db)):
    ...
```

Keep the existing 404 for an unknown tap. Do not change any other endpoint.

## Step 3 - frontend

### `src/brewops/frontend/index.html`

1. Add a filter bar as the FIRST child of `<main>`, i.e. directly above `<!-- Zone 1: dashboard -->`
   and OUTSIDE `<section id="dashboard">`. (Do not put it inside `#dashboard`: that section is a CSS
   grid with hard-coded `grid-row` values 1-3 and an extra child would collide with them.)

   ```html
   <section class="panel filter-bar" aria-label="Date range filter">
     <label for="filter-from">From</label>
     <input type="date" id="filter-from">
     <label for="filter-to">To</label>
     <input type="date" id="filter-to">
     <button type="button" id="filter-clear">All time</button>
     <p id="filter-message" class="message" role="status"></p>
   </section>
   ```

2. Give the `pours total` tile label (`<span class="tile-label">`) an `id="total-label"`. JS sets
   its text to `pours total` when no bound is set and `pours in range` when any bound is set. The
   "pours on last active day" label needs no change.

3. In the "Pours over time" panel, directly after the `<svg id="timeline" ...></svg>`, add:

   ```html
   <p id="timeline-empty" class="timeline-empty" hidden>No pours in this range.</p>
   ```

Do not use any `http://` or `https://` strings in frontend files (a test forbids them).

### `src/brewops/frontend/app.js`

Function by function (current functions in the file: `fetchJSON`, `renderDrinkBars`,
`renderTimeline`, `escapeHTML`, `renderTapCards`, `loadDashboard`, `localNow`, `fillSelect`,
`setupForms`, `submitForm`, plus two bootstrap calls at the bottom).

**New module-level state** (put it in the `// ---- dashboard ----` section, above `renderDrinkBars`):
```js
const filter = { from: "", to: "" };
let loadId = 0;
```

**New `filterQuery()`**: returns `""` if `filter.from` and `filter.to` are both empty. Otherwise build
a `URLSearchParams`, `set("date_from", ...)` / `set("date_to", ...)` only for the non-empty values,
and return `"?" + params.toString()`.

**New `setFilterMessage(text)`**: gets `#filter-message`, sets `textContent = text`, and sets
`className` to `"message error"` when text is non-empty, else `"message"`.

**New `setupFilter()`** (call it from the bottom of the file next to `setupForms()`, with the same
`.catch(console.error)` style):
- `change` listener on `#filter-from` and `#filter-to`: copy both input values into `filter`, then
  call `loadDashboard()` (add `.catch(console.error)`).
- `click` listener on `#filter-clear`: set both inputs' `.value = ""`, reset `filter.from` and
  `filter.to` to `""`, then call `loadDashboard()`.

**`loadDashboard()`** (the only existing function with real changes). New order of work:
1. Validate: if `filter.from && filter.to && filter.from > filter.to`, call
   `setFilterMessage("From date is after To date.")` and `return` WITHOUT calling the API and without
   touching the current charts. (String comparison is correct for `YYYY-MM-DD`.)
2. `const myLoad = ++loadId; const query = filterQuery();`
3. Wrap the fetching/rendering in `try/catch`. Fetch `"/api/stats" + query`.
   After EVERY `await`, do `if (myLoad !== loadId) return;` so a slow older response can't overwrite
   a newer one.
4. Set `#total-label` text: `"pours in range"` if `filter.from || filter.to`, else `"pours total"`.
5. Existing lines stay as they are: `#total-brews`, `#brews-today` (from the last `per_day` entry,
   already 0 when `per_day` is empty), `renderDrinkBars(stats.per_drink)`,
   `renderTimeline(stats.per_day)`.
6. Fetch `/api/taps` (unfiltered, it is only the list of taps) and set `#tap-count` as today.
7. Fetch each card with `` `/api/taps/${t.id}` + query `` and call
   `renderTapCards(healths, Boolean(filter.from || filter.to))`.
8. Call `setFilterMessage("")` once everything succeeded.
9. `catch (error)`: if a filter is active, `setFilterMessage(error.message)` and return (leave the
   existing charts alone). If no filter is active, rethrow, so the existing bottom-of-file `.catch`
   still shows `!` in the total tile exactly as today.

**`renderTapCards(healths, filtered = false)`**: add the `filtered` parameter. In the pours line
change `${m.brew_count} pours` to `${m.brew_count} pours${filtered ? " in range" : ""}`. The
`last ...` part stays as is (it is intentionally unfiltered). Keep the `Specialty:` text exactly as
it is (a test searches `app.js` for it). Do NOT touch the maintenance/errors markup (ticket 006).

**`renderTimeline(perDay)`**: see "Empty range: what the per-day chart does" below.

**Unchanged functions**: `fetchJSON`, `renderDrinkBars` (already safe when every count is 0
because of `Math.max(1, ...)`; zero-width bars plus `0` counts are the correct display),
`escapeHTML`, `localNow`, `fillSelect`, `setupForms`, `submitForm` (it already calls
`loadDashboard()` after a successful POST, so a new pour appears within the active range
automatically).

### Empty range: what the per-day chart does

Today, when `perDay.length === 0`, `renderTimeline` clears the SVG and returns, leaving a blank
panel (an empty 190px box). That is confusing once users can pick empty ranges, so change it:

- At the top of `renderTimeline`, get `#timeline` and `#timeline-empty`.
- `const empty = perDay.length === 0;`
- Set `svg.hidden = empty` and `emptyNote.hidden = !empty`, then clear the svg (`innerHTML = ""`)
  and `if (empty) return;` before the drawing code.
- Result: a range with zero brews shows the text "No pours in this range." in place of the chart.
  A range with data shows the bars as before. Switching between the two states works in both
  directions because both elements' `hidden` is set on every call.
- Do NOT draw the message as an SVG `<text>`: the svg uses `preserveAspectRatio="none"` so the text
  would be stretched and distorted. Use the HTML `<p>` instead.
- The API returns `per_day: []` for this case, with no zero-filled days. Days without brews inside a
  non-empty range are likewise simply absent, so the bars are spaced evenly over the days that have
  data (existing behaviour, deliberately unchanged). Consequence to know about: a one-day range
  produces a single bar that fills the whole chart width; that is acceptable.
- The other widgets for the same empty range: tile "pours in range" = 0, "pours on last active
  day" = 0, per-beer bars all show 0 with empty tracks, tap cards show `0 pours in range` and no
  Specialty line.

### `src/brewops/frontend/style.css`

Use the existing CSS variables (`--line`, `--ink`, `--ink-soft`, `--radius-sm`, `--warn`, ...).

- `.filter-bar { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; }`
  (`main` is a 2-column grid, so `grid-column: 1 / -1` makes the bar span the full width). Style its
  `label` like `.form-panel label` (colour `--ink-soft`).
- Style `.filter-bar input[type="date"]` the same as the existing `.form-panel select, .form-panel input`
  rule (around line 414) - those rules are scoped to `.form-panel`, so they do NOT apply to the filter
  bar automatically.
- Style `.filter-bar button` like `.form-panel button` (scoped the same way) but it may be smaller.
- `.filter-bar .message { grid-column: auto; margin: 0; }` so the error text sits inline (the base
  `.message` rule sets `grid-column: 2`, which is irrelevant in a flex row but should be neutralised).
- `#timeline[hidden], .timeline-empty[hidden] { display: none; }` - REQUIRED, because `#timeline`
  sets `display: block`, which would otherwise override the `hidden` attribute.
- `.timeline-empty { flex: 1; display: flex; align-items: center; justify-content: center;
  min-height: 190px; margin: 0; color: var(--ink-soft); font-family: var(--serif); font-style: italic; }`
  (the timeline panel is a flex column, so this fills the same space the chart would).
- No changes needed in the media queries (the filter bar is a normal `main` grid child; at
  `max-width: 820px` `main` already collapses to one column).

## Edge cases (all must be handled and tested)

| Case | Expected behaviour |
|---|---|
| No params | Identical to today; all existing tests pass unchanged. |
| Only `date_from` | Everything on or after that day. |
| Only `date_to` | Everything up to and including that whole day (incl. `23:59:59`). |
| `date_from == date_to` | Exactly one day. |
| Range with no brews (empty range) | `total_brews: 0`; `per_drink` lists ALL 6 drink types with count 0; `per_day: []`; HTTP 200, not an error. Frontend shows zeros, and the chart is replaced by "No pours in this range." (see "Empty range" above). |
| `date_from` after `date_to` | API returns 400 with message "date_from is after date_to"; frontend blocks the request and shows an inline message. |
| Malformed date (e.g. `2026-13-45`, `yesterday`) | API returns 400 `unparsable date ...`. |
| Empty string params (`?date_from=&date_to=`) | Treated as not set. |
| Days with no brews inside the range | Not listed in `per_day` (no zero-filling); chart plots only days with data. |
| Brew at exactly `date_to 23:59:59` / `date_from 00:00:00` | Included (inclusive bounds). |
| Tap with no brews in range | Card shows `brew_count: 0`, `specialty: null`; `last_brew`, maintenance and errors unchanged. |
| Future dates in range | Allowed; just returns empty/zero data. |

## Tests to add

`tests/test_db.py` (reuse the `conn` fixture; add several `insert_brew` rows across 3+ days with
different drinks, including one at `2026-06-02 23:59:59` and one at `2026-06-03 00:00:00`):
- range with both bounds: totals, per-drink and per-day all filtered.
- only-from and only-to variants.
- inclusive end-of-day (the `23:59:59` brew is counted when `date_to` is that day).
- empty range: `total_brews == 0`, `len(per_drink) == 6` with all counts 0, `per_day == []`.
- `get_tap_health` with a range: `brew_count` and `specialty` are filtered, `last_brew` is not.

`tests/test_api.py` (reuse the `db` fixture and `request(app, "GET", ...)`):
- `/api/stats?date_from=2026-06-02&date_to=2026-06-02` returns only that day.
- `?date_from=2026-06-05` (after all data) returns 200 with zeros.
- `?date_from=2026-06-03&date_to=2026-06-01` returns 400 and mentions "after".
- `?date_from=nonsense` returns 400.
- `?date_from=&date_to=` equals unfiltered output.
- `/api/taps/1?date_from=...&date_to=...` has a filtered `brew_count`.

`tests/test_frontend.py`: assert the served `index.html` contains `filter-from`, `filter-to` and
`timeline-empty`,
and `app.js` contains `date_from`. Keep the existing no-external-resources test passing.

## Verification checklist

1. `uv run pytest` - all tests pass, including every pre-existing test unmodified.
2. Start the app with `uv run start`, then check with curl (PowerShell: `curl.exe`):
   - `curl.exe "http://localhost:8123/api/stats"` - unchanged shape and totals.
   - `curl.exe "http://localhost:8123/api/stats?date_from=2026-05-01&date_to=2026-05-31"` - total
     is smaller than all-time and equals the sum of `per_drink` counts and the sum of `per_day` counts.
   - `curl.exe "http://localhost:8123/api/stats?date_from=2030-01-01"` - 200, zeros, 6 drink rows.
   - `curl.exe "http://localhost:8123/api/stats?date_from=2026-06-02&date_to=2026-06-01"` - 400.
   - `curl.exe "http://localhost:8123/api/taps/1?date_from=2026-05-01&date_to=2026-05-31"`.
3. In the browser at http://localhost:8123: set From/To and confirm tiles, bars, chart and tap-card
   pour counts change; set From after To and confirm the inline message and no request; pick a
   range with no data and confirm zeros plus "No pours in this range"; click "All time" and confirm
   the original numbers return; log a pour while a range is active and confirm the dashboard
   refreshes within that range.
4. Cross-check: the sum of tap-card `brew_count` values for a range should equal the stats
   `total_brews` for the same range.
5. Update `README.md` with one short paragraph documenting the `date_from` / `date_to` params.

## Files touched (summary)

- `src/brewops/db/queries.py` - `get_stats`, `get_tap_health`, two private helpers
- `src/brewops/api/main.py` - `parse_date_range`, `stats`, `tap_health`
- `src/brewops/frontend/index.html`, `app.js`, `style.css`
- `tests/test_db.py`, `tests/test_api.py`, `tests/test_frontend.py`
- `README.md` (short note)

Out of scope: tap-card maintenance/error changes (ticket 006), zero-filling the timeline,
saving the range in the URL, date presets, schema changes.
