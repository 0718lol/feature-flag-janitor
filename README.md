# Feature Flag Janitor

Feature Flag Janitor is an ops cleanup tool for finding expired feature flags, dead branches, and experiment residue in a codebase.

## Features

- Scan flag manifests, code references, experiment dates, and release records.
- Detect expired flags, orphan flags, unreachable branches, and leftover experiment code.
- Generate cleanup candidates with priority, confidence, evidence, affected files, and suggested tests.
- Export a Markdown cleanup report.
- Persist scan history and cleanup actions in SQLite.
- Run as an ASteam hosted product via `app.toml`.

## Architecture

The service stays intentionally small and uses a single Python process. `app.py` owns HTTP routes and analysis orchestration. Parsing and input limits live in `janitor/validation.py`; code reference detection and evidence classification live in `janitor/scanner.py`; lifecycle rules live in `janitor/rules.py`; risk and priority scoring live in `janitor/scoring.py`. This boundary keeps future rules and language-specific scanning independent from the hosted service.

## Run

```bash
PORT=8080 python3 -u app.py
```

Open `http://localhost:8080`.

## API

- `GET /api/health`
- `GET /api/sample`
- `GET /api/scans`
- `GET /api/scans/<scan_id>`
- `POST /api/analyze`
- `POST /api/actions` with `defer`, `ignore`, or `resolve`
- `POST /api/import-zip` with an `application/zip` body

Set `JANITOR_DB_PATH` to use a custom SQLite file location. The default is `data/janitor.sqlite3`, which is intentionally ignored by Git.

## Test

```bash
python3 -m unittest -q tests.test_app
node --check public/app.js
python3 -m py_compile app.py
```
