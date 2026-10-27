---
name: verify-change 
description: blabla
---

## Gotchas

- The app runs on port **8123**, not 8000/3000/5000.
- Run `uv run seed` before the app, it won't start without the database.
- CLI output is ASCII-only (`[OK]`/`[FAIL]`), so it works in Windows terminals, keep it that way.
- "Looks done" is not "verified." Always show the actual command output.

## Version

0.1.0