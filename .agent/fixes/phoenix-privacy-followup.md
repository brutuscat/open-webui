# Phoenix privacy review follow-up

Status: FIXED

PR: https://github.com/brutuscat/open-webui/pull/4
Reviewed base: `76ff5bf841e77484c67130db9093ecc5fed81cbd`
Validation date: 2026-10-21

## Failures and supported causes

- Webhook credentials survived URL export because `safe_url` and `safe_http_target` retained the raw path. The same path could appear in HTTP span names and `url.path`. Replace the known credential-bearing route with `/api/v1/channels/webhooks/{webhook_id}/{token}` at text and URL sanitization boundaries.
- Quoted prefixed keys combined two previously separate cases: the credential suffix matcher could not consume a closing quote after an identifier prefix. Both assignment patterns now include the prefix inside the captured key; existing escaped-value handling remains in place.
- Concurrent model tasks wrote post-inlet snapshots into one shared `request.state` field and read it after further awaits. Return the deep-copied snapshot from `process_chat_payload` directly to its sole caller instead, including `None` when the inlet removed the user turn. Do not fall back to assembled provider messages.
- The stream wrapper concatenated immutable bytes and rescanned the accumulated prefix. Use an incremental bytearray, scan only each new chunk, and stop inspecting a line after 64 KiB of UTF-8 bytes. Discard through its newline before resuming inspection. Forward original chunks unchanged and mark `openinference.stream.inspection_truncated=true` when inspection is incomplete. An error wholly inside an oversized line is intentionally not detected by this bounded inspector.

## Regression evidence

The new tests execute real exporter/stream code. The concurrency test compiles the complete production payload and chat coroutines with external-service stubs, then uses an event barrier to force three model tasks to overlap after their inlet snapshots. It checks DLP-filtered, unfiltered, and removed user turns, plus later in-place RAG injection. It does not boot the full application or contact a model provider.

The initial 23-test set was also run in a separate worktree at the reviewed base. Four regression tests failed, producing seven failure records including JSON-key subtests: webhook path disclosure, prefixed JSON disclosure, cross-model disclosure, and excessive stream memory. The stream memory test measured 16,803,002 bytes at the base, versus 91,030 bytes after the fix; its assertion allows less than 512 KiB. These are local tracemalloc measurements, not an RSS or production-load guarantee.

A local microbenchmark used one unterminated line in 1 KiB fragments (median of three runs). For 4/8/16 MiB, the base took 0.384/1.949/9.466 s; the fixed inspector took 0.00184/0.00355/0.00731 s. This supports linear scanning for this workload; it is not an application-throughput benchmark.

Additional coverage checks byte-at-a-time SSE/NDJSON, a final line without newline, UTF-8 byte boundaries at 65,535/65,536/65,537 bytes, string chunks, large single chunks, cancellation, errors, resumption after an oversized line, and unchanged client chunks. Test providers explicitly sample all spans so deployment sampling variables cannot randomly disable assertions.

## Commands and results

Python 3.12.14, project-pinned OpenTelemetry SDK/exporters 1.42.1 and instrumentors 0.63b1. Missing imports were installed in a temporary virtual environment, including typer, uvicorn, the telemetry packages, FastAPI, SQLAlchemy, Redis, aiohttp/httpx, markdown/BeautifulSoup, aiosqlite, alembic, python-socketio, orjson, and python-mimeparse. No project dependency declarations or lockfiles changed. Authentication and DB migrations were disabled for unit-test imports.

```sh
WEBUI_AUTH=False ENABLE_DB_MIGRATIONS=False PYTHONPATH=backend .venv/bin/python -m pytest -q
# 24 passed; 10 subtests passed; one existing SQLAlchemy deprecation warning

WEBUI_AUTH=False ENABLE_DB_MIGRATIONS=False PYTHONPATH=backend .venv/bin/python -m unittest discover -s test/unit -v

.venv/bin/ruff format --check . --exclude .venv --exclude venv
.venv/bin/ruff check --select=F --ignore=F401,F403,F405,F541,F811,F841 .
.venv/bin/python -m compileall -q backend/open_webui/main.py backend/open_webui/utils/middleware.py backend/open_webui/utils/telemetry/privacy.py test/unit
git diff --check
```

The repository-wide formatter and CI-configured logic checks passed. Only touched Python files were formatted.

## Real Phoenix transport canary

A temporary Phoenix 20.23.0 server ran locally with authentication, product telemetry, internet resources, and its agent assistant disabled, using a separate environment and temporary SQLite storage. The application environment retained its pinned OpenTelemetry 1.42.1 packages.

Two synthetic spans were sent through the production `RedactingSpanExporter`, one with the HTTP OTLP exporter and one with the gRPC OTLP exporter. `capture_chat_span` and the production streaming wrapper provided the input/error attributes. Phoenix's REST endpoint `/v1/projects/privacy-followup-synthetic/spans` returned both spans. Readback assertions confirmed both ERROR statuses and absence of synthetic API credentials, webhook tokens, query credentials, and the synthetic client IPv6 address. The webhook path was parameterized. The temporary server was stopped afterward.

This proves local synthetic export, receipt, and sanitized readback over both transports. It does not prove a deployed Open WebUI-to-Phoenix integration, authenticated remote ingestion, real provider calls, frontend behavior, Python 3.11 compatibility, or production-load behavior. Pattern-based redaction still cannot guarantee removal of arbitrary sensitive text.

## Second review: request filters and URL user identifiers

Reviewed base: `046c12d377161eeb1b783eeff8f6f7bc30929d92`.

Two further P1 issues reproduced: a request-stage plugin could redact/remove input after the earlier snapshot, and canonical user UUIDs survived inside URL paths despite identity attributes being dropped.

Request filters keep their existing position after context assembly and run once. Compare the last user turn and user-turn counts before/after that stage. If a direct, unaugmented turn is rewritten without changing turn counts, capture the final filtered copy. If assembled context was rewritten, or user turns were added/removed, suppress input capture. This avoids stale pre-filter text, RAG leakage, and accidentally exporting an earlier turn when the latest is deleted, including duplicate consecutive turns. Unchanged request-stage input retains the isolated pre-context snapshot.

The text sanitizer now parameterizes canonical UUID path segments as `{id}` across span names, URLs, legacy HTTP targets, stable path attributes, and list-valued attributes. The regression covers user, chat-user-list, channel-user, and SCIM paths, including trailing subpaths, and checks that static `/users/search` is preserved. This remains pattern-based redaction; it does not establish removal of arbitrary opaque identifiers.

The new tests failed at the reviewed base. Final pytest discovery passed 26 tests and 32 subtests; unittest discovery passed 26 tests. The original concurrency, redaction, streaming-resource, and cancellation regressions remain included. Repository-wide Ruff formatting/CI logic checks, changed-module compilation, and whitespace checks passed. The request-filter regression executes production coroutines with service stubs and deterministic overlap, verifies that request filters still see assembled RAG context, and covers redaction, removing all turns, deleting the latest turn (including duplicates), and no-op filters, both with and without RAG.

The real local Phoenix HTTP/gRPC canary was repeated with a synthetic user UUID in `url.path`. Both spans were received, their user paths contained `/api/v1/users/{id}/profile/image`, and readback contained no synthetic UUID. The first retry exceeded a 20-second server-start deadline before export; a repeat with a 50-second startup allowance passed. This remains a local synthetic transport check, not a deployed application/provider test.
