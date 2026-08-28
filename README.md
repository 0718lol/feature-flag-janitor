# Feature Flag Janitor

Feature Flag Janitor is an ops cleanup tool for finding expired feature flags, dead branches, and experiment residue in a codebase.

## Features

- Scan flag manifests, code references, experiment dates, and release records.
- Detect expired flags, orphan flags, unreachable branches, and leftover experiment code.
- Generate cleanup candidates with priority, confidence, evidence, affected files, and suggested tests.
- Export a Markdown cleanup report.
- Run as an ASteam hosted product via `app.toml`.

## Run

```bash
PORT=8080 python3 -u app.py
```

Open `http://localhost:8080`.

## API

- `GET /api/health`
- `GET /api/sample`
- `POST /api/analyze`

## Test

```bash
python3 -m unittest -q tests.test_app
node --check public/app.js
python3 -m py_compile app.py
```
