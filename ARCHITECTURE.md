# Feature Flag Janitor Architecture

## Product boundary

The product accepts four inputs and returns a reviewable cleanup report:

```text
flag manifest + code files + experiment records + release records
                              |
                              v
                         /api/analyze
                              |
       validation -> parsing -> reference scan -> lifecycle rules
                              |
                  scoring -> report/graph -> SQLite history
                              |
                              v
 flags + dead branches + cleanup list + reminders + input_check
```

The current product is a single Python process with a browser client. This is intentional: the work is CPU-light, the product is deployed as one ASteam app, and a database or queue service would add operational cost without solving the current bottleneck.

## Module responsibilities

| Module | Input | Output | Must not own |
| --- | --- | --- | --- |
| `app.py` | HTTP request | HTTP response | scanning rules or SQL details |
| `janitor/validation.py` | untrusted JSON | validated payload or `InputError` | business scoring |
| `janitor/scanner.py` | flags and code files | references with file, line, snippet, type | lifecycle decisions |
| `janitor/rules.py` | normalized records and dates | lifecycle and branch findings | HTTP or persistence |
| `janitor/scoring.py` | finding signals | risk, score, priority, action, confidence | parsing files |
| `janitor/storage.py` | scan result and action | scan ID, history, action state | detection logic |
| `janitor/ai.py` | redacted scan result | optional structured explanation | cleanup decisions or source storage |
| `public/app.js` | API JSON | views and user actions | source-of-truth state |

## Input contract

`POST /api/analyze` accepts JSON with `manifest_text`, `experiments_text`, `releases_text`, `code_files`, and optional `today`. Text sources can be JSON, TOML, or the supported simple block format.

Hard checks reject malformed dates, rollout outside `0..100`, empty or duplicate keys, non-object requests, oversized requests, oversized files, and excessive file counts. Missing optional sources are accepted but returned in `input_check.warnings`.

## Output contract

Every successful analysis returns:

```text
scan_id, generated_at, input_check, summary, flags, dead_branches,
experimental_residue, orphan_flags, cleanup_list, reminders, graph, source, actions
```

`input_check` reports parsed source counts and warnings. `summary` must agree with the detailed arrays; this relationship is covered by API tests. Each flag evidence item includes file, line, snippet, polarity, and `reference_type`.

## Persistence boundary

SQLite stores scan metadata, the serialized result, and the latest action per finding. Source code is not indexed globally and no external repository credential is stored. `JANITOR_DB_PATH` can select another database file for tests or deployment.

## Extension order

1. JSON report export and a result detail drawer. Done.
2. ZIP upload with the same validation limits. Done.
3. A downloadable, review-only cleanup patch. Done.
4. GitHub read-only scanning for public repositories. Done.
5. Add CI and pull request drafts after the preceding outputs are trusted.

New functionality should preserve the API contract and add tests before changing persistence or deployment topology.

## Optional AI boundary

The AI layer is advisory only. It receives a redacted summary and never receives the full `source.code_files`; its result cannot directly change a finding or execute a patch. `DEEPSEEK_API_KEY` is read only from the process environment, and `DEEPSEEK_MODEL` can override the default `deepseek-chat`. Thinking is explicitly disabled in the request. Scan history redacts raw source by default and supports explicit deletion.
