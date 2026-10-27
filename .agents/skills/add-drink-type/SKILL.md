---
name: add-drink-type
description: Add a new supported beer to BrewOps end to end, so it validates on the API, appears in the dashboard dropdowns, and counts in the stats. Use when someone asks to support a new beer or drink type (for example "add Gose").
---

# Add a beer (drink type) to BrewOps

BrewOps is database-driven: the list of valid beers lives in exactly one place,
and the API validation, the dashboard dropdowns, and the stats all read from it.
Adding a beer is a small change, but it must go in the right place and be applied
to the database, or it will silently fail to show up.

## Steps

1. Open `src/brewops/db/schema.py` and find the `DRINK_TYPES` list.
2. Add one tuple, `(name, label)`:
   - `name` is the machine-readable key: lowercase, underscores, no spaces (e.g. `gose`, `milk_stout`).
   - `label` is what people read in the UI (e.g. `Gose`, `Milk Stout`).
3. Apply the change to the database by reseeding:
   ```
   uv run seed
   ```
4. Verify it end to end — don't assume, check:
   - `uv run start`, then open http://localhost:8123
   - the new beer appears in the "Log a pour" beer dropdown
   - log a pour with it and confirm it shows up in the per-beer stats
5. Report what you changed and the evidence you saw (the beer in the dropdown and in stats).

## Gotchas

- Beers are defined **only** in `DRINK_TYPES` in `src/brewops/db/schema.py`. Everything
  else (API validation, dropdowns, stats) reads from the database — never hardcode a
  beer anywhere else.
- The edit does nothing until you run `uv run seed`. That step is what writes it to the DB.
- `uv run seed` rebuilds the database from the sample CSVs in `data/inbox/`, so it also
  clears any pours you logged by hand. That's expected.
- `name` is the stable key stored on every pour (`brew_events.drink_type`); `label` is
  display-only. Never rename an existing `name` — it would orphan existing pour rows.

## Version

0.1.0
