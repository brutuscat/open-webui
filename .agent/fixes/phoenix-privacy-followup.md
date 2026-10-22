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

## Third review: hostless URIs, private keys, and gRPC headers (2026-10-22)

Reviewed base: `af630aaf28575a1febe32c5ed17e838625fb9427`. All three new comments were P1 and reproduced:

- Hostless `file:///` and `sqlite:///` URIs re-entered embedded-URL sanitization indefinitely. The fallback now disables that pass, preserves URI syntax, removes query/fragment data, and sanitizes path components. The regression exports a two-span batch for both URI schemes and checks an encoded email path.
- `private_key` assignments and standalone PEM private-key blocks survived text sanitization. Private-key assignments now use the existing secret-value redaction, and PEM private-key blocks (including truncated blocks) are removed before other text processing. Tests cover service-account JSON, assignments, YAML, RSA/EC/OpenSSH/encrypted PEM variants, and preservation of public-key blocks.
- Decoded OTLP header names retained mixed casing, breaking gRPC metadata and case-insensitive precedence. Normalize names to lowercase before merging generic and trace-specific headers. The regression covers percent-encoded names and overrides of Basic authentication.

Each of the three new regression tests failed when run against the reviewed privacy/setup modules. The final full telemetry suite passed **33 tests** with Python 3.11.13 and pinned OpenTelemetry 1.42.1. This also runs the earlier YAML, Basic-auth, connection-URL, encoded-path, concurrency, and streaming regressions.

The runtime's existing dependencies were used without modifying the service environment. Test subprocesses set `DATA_DIR` to a fresh temporary directory, `DATABASE_URL` to a SQLite file in that directory, `WEBUI_AUTH=False`, `ENABLE_DB_MIGRATIONS=False`, `ENABLE_OTEL_TRACES=False`, `ENABLE_OTEL_METRICS=False`, and `PYTHONPATH=backend`:

```powershell
& 'C:\ProgramData\OpenWebUI\runtime\venv-0.12.3-harmony-20261016\Scripts\python.exe' -X utf8 -m unittest discover -s test/unit -p 'test_telemetry*.py' -v
```

The `-X utf8` flag is required for the existing coroutine tests' source-file reads on Windows. An in-memory SQLite URL is unsuitable for the application's pool options; the isolated file URL above works.

A separate loopback OTLP gRPC receiver accepted a synthetic span sent by the pinned exporter with headers built by the production helper. Receiver assertions confirmed lowercase `authorization`/`x-api-key` and trace-specific values overriding generic/Basic credentials. The receiver was stopped afterward. This validates local gRPC transport, not deployed Phoenix ingestion. Ruff formatting and the repository's CI logic checks on changed Python files, compilation, and `git diff --check` passed.

## Independent review: removed outlet turns (2026-10-22)

Two fresh GPT-6.1 Sol reviewers at medium effort reviewed pinned head `04d0a7b4a998db5f09387c2dba8a16c38bc5971b` using the read-only `review-agent` skill. The full PR reviewer confirmed a P1: removing the current assistant from the outlet result retained its pre-filter snapshot, or selected an older assistant as a fallback. The new export path could therefore send content absent from the final filter result. Ordinary direct API output/usage omissions were confirmed as P2 by both reviewers.

Select only the assistant matching the current message ID after outlet filters. If it is absent, explicitly set the context value to `None`. The non-streaming JSON fallback now runs only when the context has no assistant key, preserving that explicit suppression. Existing message delivery and persistence paths are unchanged; output capture is conservatively omitted if a filter discards the current message ID.

A new regression runs the complete production outlet and chat coroutines with service stubs across dict, JSON, and streaming responses. It covers empty results, retained historical turns, empty current content, and a replacement current turn. It produced six failing subtests on the reviewed implementation and passes after the fix. All **34 telemetry tests** pass in the isolated Python 3.11.13 environment described above. Formatting, CI logic checks, compilation, and whitespace checks pass. The full PR reviewer independently inspected the integrated fix and reran all 34 tests without further findings. No production application/provider test was performed.

The two GitHub P2 threads about direct API streaming accumulation and non-streaming dictionary capture remain deferred under the requested P0/P1 scope. They are acknowledged in their threads and remain open. Any future response fallback must preserve explicit outlet suppression and must not repopulate removed output from the raw provider response.
