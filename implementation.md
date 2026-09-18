# Implementation Plan — Feed Handler Symbol Validity Checker

Concrete "how" — files, signatures, libraries, CLI shape, exit codes. Assumes `requirements.md` §8 decisions are locked and `design.md` is signed off.

## 1. Files to Create / Modify

### Create (all done)
| Path | Purpose |
|---|---|
| `pyproject.toml` | Packaging metadata (setuptools backend); declares runtime deps, `[project.scripts] find-expired-symbols = fh_symbol_check.cli:run`, top-level `py-modules` (`check_delisted_symbol`, `mysql_select_query`), and `package-data` (`fh_symbol_check/data/*.yaml`). See §11. |
| `find_expired_symbols.py` | Source-checkout entry point — 3-line wrapper: `from fh_symbol_check.cli import run; if __name__ == "__main__": sys.exit(run())`. |
| `fh_symbol_check/data/exchange_mapping.yaml` | DB `exchange_name` → ccxt id. Code-tracked. Bundled inside the wheel as package data so an installed operator gets it for free. Overrideable with `--exchange-map`. |
| `fh_symbol_check/__init__.py` | Empty package marker. |
| `fh_symbol_check/cli.py` | Argparse + orchestration + `main()` + `run()` (console-script entry point); `DEFAULT_EXCHANGE_MAP` resolved via `importlib.resources`. `run()` calls `_install_system_trust_store()` (`truststore.inject_into_ssl()`, best-effort) before `main()` so ccxt/urllib HTTPS calls honour the OS trust store — necessary on corp networks whose SSL-inspection proxy re-signs traffic with an internal root CA that isn't in Python's Mozilla bundle. The DB-fetch → build_tasks → classify middle now delegates to `pipeline.run_scan` so the API service reuses the exact code path. |
| `fh_symbol_check/pipeline.py` | Shared orchestration entry: `run_scan(filters, creds, exchange_map, *, on_progress=None) -> list[SymbolResult]`, `ScanFilters` (frozen dataclass mirroring the CLI's argparse fields; `.validate()` reproduces the argparse error strings), `ScanProgress` (immutable snapshot per phase / ccxt-group completion), `describe_filters(filters) -> str` (log-line helper shared by CLI and API). No argparse / HTTP concerns — pure library. |
| `fh_symbol_check/creds.py` | `load_creds(path, section)`, `DBCreds`, `CredsError`. |
| `fh_symbol_check/db.py` | `fetch_feed_handlers(creds, hostname_pattern=None, *, exchange_name=None)` + `fetch_repeaters(creds, ...)` (same signature, queries `crypto_db.repeater_feeds`, returns `FeedHandlerRow`s with `source="repeater"` and `service_id=None`); `DBError`. |
| `fh_symbol_check/exchange_map.py` | `load_exchange_map(path)`, `resolve(mapping, exchange_name)`, `ExchangeMapError`. |
| `fh_symbol_check/symbol_translation.py` | Per-`exchange_name` `TRANSLATORS` registry + `translate(exchange_name, symbol)` + the `_translate_*` primitives. |
| `fh_symbol_check/models.py` | `FeedHandlerRow`, `ResolvedTask`, `SymbolResult`, `SymbolStatus`, `SourceKind`. All three producer dataclasses carry `source: SourceKind` and a nullable `service_id: int \| None`. |
| `fh_symbol_check/validator.py` | `build_tasks`, `filter_by_symbol`, `classify_symbols` (with custom-venue dispatch via `_classify_custom_venue`). Fully source-agnostic — `source` is copied verbatim from `FeedHandlerRow` → `ResolvedTask` → `SymbolResult`. |
| `fh_symbol_check/reporter.py` | `render`, `summary`, `summary_by_fh` (source="fh" only), `summary_by_repeater` (source="repeater" only), `summary_by_exchange` (group key = `(exchange_name, source)`), `keep_fhs_with_errors` (identity = `(source, hostname, fh_name, exchange_name)`), `FeedHandlerSummary`, `ExchangeSummary`. |
| `fh_symbol_check/logging_config.py` | `setup_logging(level)`. |
| `fh_symbol_check/custom_venues/__init__.py` | `CUSTOM_VENUES` registry, `is_custom_venue`, `custom_id_for`, `get_checker`, `CUSTOM_VENUE_PREFIX`. |
| `fh_symbol_check/custom_venues/nado.py` | Nado checker (`fetch_symbols()`), `User-Agent`-spoofed `urllib`. |
| `fh_symbol_check/custom_venues/polymarket_perps.py` | Polymarket Perps checker (`fetch_symbols()`), same shape. |
| `fh_symbol_check/custom_venues/vertex.py` | Vertex family checker (Arbitrum / Avalanche / Berachain / Mantle / Sonic). **Venue shut down July 2025** (Ink Foundation merger). `_fetch` raises `VenueGone` and does not hit the former `archive.*.vertexprotocol.com` hosts. Validator emits `DELISTED` with the shutdown reason. Historical `_parse_symbols` kept for fixture tests. |
| `fh_symbol_check/custom_venues/injective.py` | Injective (Helix) checker. Hits the public LCD REST (`sentry.lcd.injective.network`) for spot + derivative markets at `Active` / `Paused` / `Expired` (6 GETs). `Demolished` is not fetched so those classify as DELISTED. `_translate_injective` turns FH `BTC/USDC-PERP` into venue `BTC/USDC PERP`. |
| `fh_symbol_check/custom_venues/rhlighter.py` | Robinhood Chain Lighter checker (`api.rh.lighter.xyz/api/v1/orderBookDetails`). Separate book from ccxt `lighter` / mainnet. `_translate_rhlighter` turns FH `BTC/USDG-PERP` into venue `BTC`. |
| `fh_symbol_check/custom_venues/arcus.py` | Arcus (dYdX Labs DEX) checker. Hits `GET https://api.arcus.xyz/v1/markets`. `_translate_arcus` turns FH `BTC/USD-PERP` into venue `BTC-USD`. `ONLINE` → live, `OFFLINE` → inactive. |
| `fh_symbol_check/custom_venues/ondoperps.py` | Ondo Perps checker. Hits `GET https://api.ondoperps.xyz/v1/markets`. `_translate_ondoperps` turns FH `NVDA/USD-PERP` into venue `NVDA-USD.P`. Every returned pair is live (no status field). |
| `fh_symbol_check/api/__init__.py` | Package marker (short docstring). |
| `fh_symbol_check/api/main.py` | Console-script `run()` for `find-expired-symbols-service`. Argparse (`--bind`, `--port`, `--creds-file`, `--exchange-map`, `--job-ttl-seconds`, `--max-concurrent-scans`, `--log-level`, `-v`), loads creds + exchange map, calls `build_app`, blocks on `uvicorn.run(...)`. Reuses `cli._install_system_trust_store` for corp-proxy trust setup. |
| `fh_symbol_check/api/server.py` | `build_app(creds, exchange_map, *, job_ttl_seconds, max_concurrent_scans, sweep_interval_seconds, static_dir, templates_dir) -> FastAPI` factory. Registers a lifespan-managed background `asyncio.Task` running `store.sweep()` on `sweep_interval_seconds`. Mounts `/static` iff the packaged directory exists. Injects routes (`routes.api`) + views (`views.views`). |
| `fh_symbol_check/api/routes.py` | JSON API. `POST /scans` (202 + Location + summary), `GET /scans` (list), `GET /scans/{job_id}` (detail, 404 on unknown), `GET /health`. Dependency helpers pull `store` / `workers` off `request.app.state`. |
| `fh_symbol_check/api/views.py` | HTMX endpoints. `GET /` (form + list), `POST /` (form → `ScanRequest` → submit + return scan-card partial, or `form_error.html` partial on validation failure), `GET /scans/{job_id}/partial` (polling target; empty body on unknown-id / swept). |
| `fh_symbol_check/api/models.py` | Pydantic schemas. `ScanRequest` (extra="forbid"; `@model_validator` reproduces the CLI's argparse error strings; `.to_filters()` -> `ScanFilters`), `ScanJobSummary` / `ScanJobDetail` / `ProgressOut` / `SymbolResultOut`, `HealthResponse`. |
| `fh_symbol_check/api/jobs.py` | `Job` dataclass (id / state / filters / progress / created_at / completed_at / result_json / error) + `JobStore` guarded by `threading.RLock` — create / get / mark_running / update_progress / mark_done / mark_failed / sweep / active_count / recent_count. TTL sweep drops completed jobs older than `job_ttl_seconds`. Race-safe: update-on-unknown-job is a logged no-op. |
| `fh_symbol_check/api/workers.py` | `ScanWorkers` — bounded `ThreadPoolExecutor` (`max_workers=max_concurrent_scans`). `.submit(filters, request_dump) -> Job` schedules `_run_job`. `_run_job`: `mark_running` → `pipeline.run_scan(..., on_progress=<store.update_progress bridge>)` → `mark_done(result_json=[asdict(r) for r in ...])` or `mark_failed(error="<Type>: <msg>")`. `.shutdown(wait=False)` on lifespan teardown. |
| `fh_symbol_check/api/templates/base.html` | HTML shell: loads `/static/htmx.min.js` and `/static/app.css`; header nav; footer with version + TTL. |
| `fh_symbol_check/api/templates/home.html` | Filter form (source / hostname / exchange_name / symbol / concurrency + checkboxes) — HTMX-posts to `/` with `hx-target="#scans-list" hx-swap="afterbegin"`. Renders each existing `recent_jobs` entry via `{% include "scan_card.html" %}`. |
| `fh_symbol_check/api/templates/scan_card.html` | State-aware card. `queued`/`running` carries `hx-get="/scans/{{ job.id }}/partial" hx-trigger="every 1s" hx-swap="outerHTML"`. `done` shows completion time + row count + JSON link + `{% include "result_table.html" %}`. `failed` shows the error message. |
| `fh_symbol_check/api/templates/result_table.html` | Status-filter chip row + rows table (status / source / hostname / fh_name / exchange / original / ccxt / detail). ~15-line inline `<script>` toggles a `data-status` container attr; CSS attribute selectors hide non-matching rows — no JS framework beyond HTMX itself. |
| `fh_symbol_check/api/templates/form_error.html` | Small inline validation-error card returned in place of a scan card when the form fails Pydantic validation. |
| `fh_symbol_check/api/static/htmx.min.js` | HTMX 1.9.12, MIT-licensed, 48KB minified. Vendored — never hits a CDN. |
| `fh_symbol_check/api/static/app.css` | ~200 lines, CSS variables for light/dark theme, status pills, progress bar, responsive form grid, sticky table header. |
| `deploy/find-expired-symbols.service` | systemd **user** unit template for the API service. `Type=exec`, `ExecStart=%h/.pixi/bin/pixi run -e find-expired-symbols find-expired-symbols-service --bind 127.0.0.1 --port 8000`, `Restart=on-failure`, `WantedBy=default.target`. Operator copies to `~/.config/systemd/user/`, `systemctl --user enable --now`, `sudo loginctl enable-linger $USER`. Full first-time flow + update flow in `deploy.md`. |
| `tests/test_pipeline.py` | Locks the CLI/API pipeline contract: `ScanFilters.validate` error strings, `describe_filters` output, `run_scan` DB routing per `source`, `--symbol` OR-semantics, DB-error propagation, progress-callback phase order + monotonic completion counter + exception swallow. |
| `tests/test_api_models.py` | Pydantic layer: `ScanRequest` rejects the same combinations argparse rejects with identical wording, `.to_filters()` maps every field, `extra="forbid"` catches typos, `concurrency` bounded 1-32. |
| `tests/test_api_jobs.py` | `JobStore` state machine (queued/running/done/failed transitions), TTL sweep (evicts stale completed only), active/recent counters, thread-safety smoke (4 threads × 200 update_progress calls), update-on-unknown-job is a no-op. |
| `tests/test_api_routes.py` | End-to-end JSON API via `TestClient`: `/health`, `/openapi.json` locks the surface, POST validation 422 (both wording paths), POST 202 + Location header, list, poll-to-done, DB error → `state=failed`, unknown id → 404. |
| `tests/test_api_templates.py` | HTMX + Jinja UI: GET / renders form + HTMX bootstrap + empty state, static assets served, POST / returns scan card with polling trigger while running, done partial has result rows and no trigger, form_error partial carries CLI wording, `/scans/{id}/partial` returns empty body on unknown id. |
| `deploy.md` | End-to-end runbook: build wheel → rsync wheel + `pixi.toml` + `pixi.lock` to a user-chosen install directory → `pixi run -e find-expired-symbols pip install --no-deps --force-reinstall <wheel>` → creds → optional systemd user unit for the API service (`systemctl --user enable --now find-expired-symbols` + `loginctl enable-linger`) → verification / smoke test (CLI + API) → scheduler wiring (CLI, plus alternative `POST /scans` route) → uninstall / rollback → multi-user permissions appendix → troubleshooting cheatsheet (extended with API-specific symptoms). Same five steps serve both fresh installs and updates; API service needs `systemctl --user restart` after each install. |
| `tests/__init__.py` | Empty. |
| `tests/conftest.py` | Ensure workspace root is on `sys.path`. |
| `tests/test_creds.py` | YAML load by section; password-mask in repr; password absent from `caplog.text`. |
| `tests/test_db.py` | Stubbed `pymysql.connect`; dynamic-WHERE composition for all combinations of `hostname` / `exchange_name`; covers both `fetch_feed_handlers` and `fetch_repeaters` (correct SQL statement, source stamp, `service_id=None` for repeaters, `app_name` in the `fh_name` slot, error message scoped to the failing table). |
| `tests/test_exchange_map.py` | Load + case-insensitive lookup; YAML errors → `ExchangeMapError`. |
| `tests/test_symbol_translation.py` | Every registered translator primitive + dispatch tests for every key in `TRANSLATORS`. |
| `tests/test_validator.py` | LISTED / INACTIVE / DELISTED / ERROR classification (ccxt path); custom-venue dispatch; `filter_by_symbol` exhaustive cases; `source` propagation across `build_tasks` and `classify_symbols` for both fh and repeater inputs (including the unknown-exchange ERROR path). |
| `tests/test_reporter.py` | text / json / csv shapes; per-source (fh + repeater) summary tables with distinct column headers (`fh_name` vs `app_name`); per-`(exchange, source)` summary table under `--exchange-grouping` with an added `source` column; two-labelled-detail-section split (`--- Feed handlers ---` / `--- Repeaters ---`); single-source runs elide the empty table; `keep_fhs_with_errors` disambiguates producers by source; `--errors-only` filter; suppress-details behaviour; JSON/CSV invariance under text-only flags. |
| `tests/test_cli.py` | Argparse-level flag acceptance + the validation gates (`--all` exclusivity, "at least one of" requirement); `--source` accepts `fh`/`rp`/`both`, defaults to `both`, and rejects unknown choices. |
| `tests/test_custom_venues_nado.py` | Nado parser + `fetch_symbols` mocking. |
| `tests/test_custom_venues_polymarket_perps.py` | Polymarket Perps parser + `fetch_symbols` mocking. |
| `tests/test_custom_venues_vertex.py` | Vertex historical parser + `_EDGES` / registry sanity + `VenueGone` shutdown path (no network) + `KeyError` on typoed edge. |
| `tests/test_custom_venues_injective.py` | Injective LCD parser (Active/Paused/Expired/Demolished, ticker strip) + 6-URL fetch composition + merge + headers + error paths. |
| `tests/test_custom_venues_rhlighter.py` | RH Lighter parser (perp + spot, active/inactive) + fetch URL/headers + error paths. |
| `tests/test_custom_venues_arcus.py` | Arcus parser (ONLINE/OFFLINE) + fetch URL/headers + error paths. |
| `tests/test_custom_venues_ondoperps.py` | Ondo Perps parser (market field, success=false) + fetch URL/headers + error paths. |
| `tests/test_packaging.py` | Packaging-contract smoke checks: `run` is the entry-point target; `--help` mentions every filter flag; `DEFAULT_EXCHANGE_MAP` resolves and parses; exit-code constants frozen at `0/1/2/130`. |
| `.vscode/launch.json` | Python debug configs. |
| `README.md` | User-facing docs: install (pixi), run, creds & mapping location, CLI reference, examples, exit codes; pointer to `deploy.md` for the Linux server story. |

### Modify (each via a diff I will post in chat for sign-off first)
| Path | Change | Rationale |
|---|---|---|
| `.gitignore` | Append `.DBCreds.*`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `build/`, `dist/`, `*.egg-info/`. Confirm `fh_symbol_check/data/exchange_mapping.yaml` is NOT ignored. | Prevent committing secrets, dev-cache noise, and packaging build artefacts. **Repo hygiene — do first.** |
| `pixi.toml` | Add `[dependencies]`: `pymysql`, `pyyaml`, `python>=3.10,<3.14`; `[pypi-dependencies]`: `ccxt` (no conda-forge build); `[feature.dev.pypi-dependencies]`: `pytest`, `mypy`, `ruff`, `build`, `types-PyYAML`, `types-PyMySQL`. Add `linux-64` to `platforms` so the same manifest resolves on the deploy target. | Tool + dev + wheel-builder deps. |
| `mysql_select_query.py` | Design §4.2: drop hard-coded credential defaults, add `params=None` to `fetch_query_results`, migrate `__main__` to read from `.DBCreds.yaml` (`crypto_db` section). | Safety + parameterised queries. |
| `check_delisted_symbol.py` | Design §4.1: extract pure `classify(markets, symbol)` and `load_exchange_markets_safe(exchange_id)` raising `MarketLoadError`; keep existing CLI behaviour unchanged. | Library reuse without `sys.exit` hazards. |

## 2. CLI Interface

```
$ pixi run python find_expired_symbols.py --help

usage: find_expired_symbols.py [options]

Filter args (at least one is required):
  --hostname STRING             Hostname substring for the SQL LIKE filter
                                (e.g. TA-TKY-A-41). Bound as a parameter.
                                Applied to whichever producer tables --source
                                selects.
  --exchange-name STRING        Case-insensitive exact match against
                                exchange_name (e.g. BINANCE). Applied to
                                whichever producer tables --source selects.
  --symbol SYMBOL [SYMBOL ...]  Find every occurrence of one or more exact
                                symbols. Space-separated; OR semantics
                                (kept if it matches any). Case-sensitive;
                                each value matched against both the producer
                                form (cover_names / instruments) and the
                                translated venue form. Used alone implies
                                --all.
  --all                         Scan every producer row (no filters).
                                Mutually exclusive with --hostname and
                                --exchange-name.

Producer selection:
  --source {fh,rp,both}         Which producer tables to query (default: both).
                                  fh   = crypto_db.fh_config only
                                  rp   = crypto_db.repeater_feeds only
                                  both = concatenate rows from both tables
                                All other filters apply to whichever tables
                                are scanned.

Report-shaping args:
  --output {text,json,csv}      Report format (default: text).
  --output-file PATH            Write report to PATH instead of stdout.
  --show-listed                 Include LISTED rows in text output. Auto-
                                enabled when --symbol is set.
  --errors-only                 Filter the report to feed handlers that
                                have at least one ERROR row. Does not
                                change the summary log line or exit code.
  --exchange-grouping           Text only: replace the per-source summary
                                tables with a single per-(exchange, source)
                                one (adds a `source` column); suppress
                                per-symbol detail on stdout (kept when
                                writing to --output-file). No effect on
                                JSON / CSV.

Behaviour:
  --concurrency N               Max parallel exchanges (default: 4).
  --fail-on-invalid             Exit 1 when any DELISTED/INACTIVE found
                                (default).
  --no-fail-on-invalid          Always exit 0 unless an operational error.

Config:
  --creds-file PATH             Credentials YAML (default: ./.DBCreds.yaml).
  --creds-section NAME          Section in the creds YAML (default: crypto_db).
  --exchange-map PATH           Exchange-name → ccxt-id map (default: the
                                bundled fh_symbol_check/data/exchange_mapping.yaml,
                                resolved via importlib.resources; identical
                                whether run from the source tree or an
                                installed wheel).

Diagnostics:
  --log-level {DEBUG,INFO,WARNING,ERROR}   Default INFO.
  -v / --verbose                Shortcut for --log-level DEBUG.
```

## 3. Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success; no invalid symbols (or `--no-fail-on-invalid`). |
| `1` | Success; at least one `DELISTED` or `INACTIVE` symbol; `--fail-on-invalid` (default). |
| `2` | Operational failure (creds / DB / args / exchange-map / I/O / unhandled exception). |
| `130` | `KeyboardInterrupt`. |

`ERROR` rows alone do **not** force exit 1; they signal "couldn't determine".

## 4. Function Signatures (locked)

```python
# fh_symbol_check/models.py
SymbolStatus = Literal["LISTED", "INACTIVE", "DELISTED", "ERROR"]
SourceKind = Literal["fh", "repeater"]

@dataclass(frozen=True)
class FeedHandlerRow:
    fh_name: str                # fh_config.fh_name OR repeater_feeds.app_name
    hostname: str
    exchange_name: str
    symbols: tuple[str, ...]
    source: SourceKind = "fh"
    service_id: int | None = None   # None for repeaters

@dataclass(frozen=True)
class ResolvedTask:
    fh_name: str
    hostname: str
    exchange_name: str
    ccxt_id: str
    original_symbol: str
    ccxt_symbol: str
    source: SourceKind = "fh"
    service_id: int | None = None

@dataclass(frozen=True)
class SymbolResult:
    fh_name: str
    hostname: str
    exchange_name: str
    ccxt_id: str
    original_symbol: str
    ccxt_symbol: str
    status: SymbolStatus
    detail: str = ""
    source: SourceKind = "fh"
    service_id: int | None = None
```

```python
# fh_symbol_check/creds.py
@dataclass(frozen=True)
class DBCreds:
    host: str
    user: str
    password: str
    database: str
    def __repr__(self) -> str: ...  # masks password

class CredsError(Exception): ...

def load_creds(path: Path, section: str = "crypto_db") -> DBCreds: ...
```

```python
# fh_symbol_check/db.py
class DBError(Exception): ...

def fetch_feed_handlers(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]:
    """SELECT service_id, fh_name, hostname, exchange_name, cover_names
    FROM crypto_db.fh_config [WHERE …]. Rows carry source='fh'."""

def fetch_repeaters(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]:
    """SELECT hostname, app_name, exchange_name, instruments
    FROM crypto_db.repeater_feeds [WHERE …]. Rows carry source='repeater',
    service_id=None, and store the DB `app_name` value in the `fh_name` slot."""
```

Both queries share the same WHERE composition helper (`_compose_sql`) so the filter contract is identical; both wrap pymysql errors in `DBError("failed to query <table>: …")`.

```python
# fh_symbol_check/exchange_map.py
class ExchangeMapError(Exception): ...

def load_exchange_map(path: Path) -> dict[str, str]: ...  # keys uppercased on load
def resolve(mapping: dict[str, str], exchange_name: str) -> str | None: ...
```

```python
# fh_symbol_check/symbol_translation.py
Translator = Callable[[str], str]

# Keyed by FH `exchange_name` (uppercase). Missing entries default to identity.
TRANSLATORS: dict[str, Translator] = {
    "BINANCEDM": _translate_perp_suffix,
    "BINANCEDMCOIN": _translate_perp_suffix_inverse,
    "BITGETDM": _translate_perp_by_quote,
    "BYBITDM": _translate_perp_by_quote,
    "NADO": _translate_perp_strip_quote,
    "POLYMARKETPERPS": _translate_polymarketperps,
    # ... see source for the full list
}

def translate(exchange_name: str, internal_symbol: str) -> str: ...
def _translate_perp_suffix(s: str) -> str: ...           # linear:  BTC/USDT-PERP -> BTC/USDT:USDT
def _translate_perp_suffix_inverse(s: str) -> str: ...   # inverse: BTC/USD-PERP  -> BTC/USD:BTC
def _translate_perp_by_quote(s: str) -> str: ...         # linear OR inverse by quote (BYBITDM, BITGETDM)
def _translate_perp_strip_quote(s: str) -> str: ...      # BTC/USD-PERP -> BTC-PERP (NADO)
def _translate_polymarketperps(s: str) -> str: ...       # GOLD/USDC-PERP -> GOLD-USD (+ WTI -> WTIOIL remap)
```

```python
# fh_symbol_check/custom_venues/__init__.py
CustomVenueChecker = Callable[[], Mapping[str, bool]]

CUSTOM_VENUES: dict[str, CustomVenueChecker] = {
    "NADO": nado.fetch_symbols,
    "POLYMARKETPERPS": polymarket_perps.fetch_symbols,
}

CUSTOM_VENUE_PREFIX = "custom:"

def is_custom_venue(exchange_name: str) -> bool: ...
def custom_id_for(exchange_name: str) -> str: ...   # "NADO" -> "custom:NADO"
def get_checker(custom_id: str) -> CustomVenueChecker: ...
```

```python
# fh_symbol_check/validator.py
def build_tasks(
    rows: Iterable[FeedHandlerRow],
    exchange_map: dict[str, str],
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Routes custom venues first (sentinel ccxt_id = 'custom:<NAME>'),
    then exchange_map; unknown exchange_names become ERROR rows."""

def filter_by_symbol(
    tasks: list[ResolvedTask],
    errors: list[SymbolResult],
    symbols: Iterable[str],
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Keep only tasks/errors where original_symbol OR ccxt_symbol equals
    any value in symbols (OR semantics). Case-sensitive, full-string
    equality. Empty ``symbols`` filters everything out."""

def classify_symbols(
    tasks: Iterable[ResolvedTask],
    *,
    concurrency: int = 4,
) -> list[SymbolResult]: ...
```

```python
# fh_symbol_check/reporter.py
@dataclass(frozen=True)
class FeedHandlerSummary:
    """Per-producer summary; the `fh_name` slot holds the DB `fh_name`
    for feed handlers and the DB `app_name` for repeaters."""
    fh_name: str
    hostname: str
    exchange_name: str
    active: int       # LISTED
    inactive: int
    delisted: int
    error: int
    total_dead: int   # inactive + delisted
    total: int

@dataclass(frozen=True)
class ExchangeSummary:
    exchange_name: str
    source: SourceKind         # "fh" or "repeater" — part of the group key
    feed_handlers: int         # distinct (hostname, fh_name) pairs in (exchange, source)
    active: int
    inactive: int
    delisted: int
    error: int
    total_dead: int
    total: int

def render(
    results: list[SymbolResult],
    fmt: Literal["text", "json", "csv"],
    stream: TextIO,
    *,
    show_listed: bool = False,
    exchange_grouping: bool = False,
    suppress_details: bool = False,
) -> None: ...

def summary(results: list[SymbolResult]) -> dict[SymbolStatus, int]: ...
def summary_by_fh(results: list[SymbolResult]) -> list[FeedHandlerSummary]: ...        # source="fh" only
def summary_by_repeater(results: list[SymbolResult]) -> list[FeedHandlerSummary]: ...  # source="repeater" only
def summary_by_exchange(results: list[SymbolResult]) -> list[ExchangeSummary]: ...     # keyed by (exchange, source)
def keep_fhs_with_errors(results: list[SymbolResult]) -> list[SymbolResult]:
    """Subset to producers identified by (source, hostname, fh_name,
    exchange_name) that have at least one ERROR row; keeps every row of
    those producers so the surviving summary stays complete."""
```

```python
# fh_symbol_check/cli.py
from pathlib import Path

DEFAULT_EXCHANGE_MAP: Path   # resolved via importlib.resources.files("fh_symbol_check")
                             #    / "data" / "exchange_mapping.yaml"

def main(argv: list[str] | None = None) -> int:
    """Argparse-driven orchestration. Returns exit code (0/1/2)."""

def run() -> int:
    """Console-script entry point (`[project.scripts]` target).

    Wraps `main()` with a KeyboardInterrupt handler (→ 130) and a
    catch-all exception guard (→ 2 with `logger.exception`). Both the
    source-tree launcher (`find_expired_symbols.py`) and the installed
    `find-expired-symbols` binary land here.

    Before dispatching to main(), calls _install_system_trust_store()
    which invokes truststore.inject_into_ssl(). This makes Python honour
    the OS-native trust store (macOS Keychain / Linux system CA bundle /
    Windows cert store) instead of the Mozilla-only bundle bundled with
    conda-forge Python, so ccxt calls succeed on corp networks whose
    SSL-inspection proxy (Zscaler, Palo Alto, Netskope, …) re-signs
    HTTPS with an internal root CA. Any exception from the injection is
    logged as WARNING and swallowed (best-effort — the tool falls back
    to the bundled bundle rather than crashing).
    """
```

## 5. Helper-Script Refactors (completed)

### 5.1 `check_delisted_symbol.py`
Added (above existing functions):
```python
class MarketLoadError(Exception): ...

def load_exchange_markets_safe(exchange_id: str) -> tuple[ccxt.Exchange, dict]:
    if exchange_id not in ccxt.exchanges:
        raise MarketLoadError(f"'{exchange_id}' is not a supported exchange.")
    exchange = getattr(ccxt, exchange_id)()
    try:
        markets = exchange.load_markets()
    except (ccxt.NetworkError, ccxt.ExchangeError) as e:
        raise MarketLoadError(str(e)) from e
    return exchange, markets

def classify(markets: dict, symbol: str) -> tuple[str, str]:
    sym = symbol.upper()
    if sym not in markets:
        return "DELISTED", ""
    if markets[sym].get("active", True) is False:
        return "INACTIVE", "market.active is False"
    return "LISTED", ""
```
Updated existing functions:
- `load_exchange_markets` → calls `load_exchange_markets_safe`, catches `MarketLoadError`, print + `sys.exit(1)` (CLI behaviour preserved).
- `check_symbol` / `check_multiple` → call `classify(markets, symbol)`; printing unchanged.

### 5.2 `mysql_select_query.py`
Targeted changes only:
- Replace credential defaults with required positional args:
  ```python
  def __init__(self, host: str, user: str, password: str, database: str):
  ```
- Add `params` argument:
  ```python
  def fetch_query_results(self, query, params=None, return_header=False):
      ...
      cursor.execute(query, params) if params is not None else cursor.execute(query)
      ...
  ```
- Replaced the `__main__` block to read `.DBCreds.yaml` (section `crypto_db`) — no hard-coded creds remain anywhere in the helper.

## 6. Libraries

| Library | Why | Source |
|---|---|---|
| `pymysql` | Already used by helper; supports parameterised queries. | conda-forge / pypi |
| `ccxt` | Used by helper; authoritative for "is symbol listed" for ccxt-backed venues. | pypi |
| `pyyaml` | YAML creds + exchange map. | conda-forge / pypi |
| `truststore` | Runtime; makes Python honour the OS trust store instead of the Mozilla-only bundle. Required for ccxt HTTPS to succeed on corp networks whose SSL-inspection proxy re-signs traffic with an internal root CA. Called from `cli.run()` before dispatching to `main()`. Paired with a Chrome 126 desktop `User-Agent` set on every ccxt exchange in `check_delisted_symbol.load_exchange_markets_safe` so the proxy's non-browser-UA policy doesn't return an HTML block page instead of JSON. ERROR-row `detail` is single-lined and capped at 500 chars via `_sanitize_error_detail` in `validator.py` as a safety net. `_rewrite_proxy_block` in the same file recognises two block shapes and is called from both the ccxt path (`_classify_one_exchange`) and the custom-venue path (`_classify_custom_venue`): (1) Zscaler `wac_block.html` (URL-category deny, HTTP 403) responses, collapsed to a short "blocked by corporate proxy … — contact IT to allowlist this exchange host" hint with the HTML stripped; (2) TLS teardown signatures (`UNEXPECTED_EOF_WHILE_READING`, `EOF occurred in violation of protocol`), which indicate an SNI-keyed firewall drop before any certificate is exchanged — there is no block page to strip, so the original text is kept and prefixed with "likely blocked by corporate network policy (TLS handshake closed before certificate exchange)". Custom-venue fetch errors embed the failing URL so the hint names the host to allowlist. The full raw error is preserved at DEBUG-level log in both cases. | conda-forge / pypi |
| `cryptography` | Transitive dep of `ccxt`. Declared under `pixi.toml` `[dependencies]` (conda-forge) rather than left to PyPI resolution because recent PyPI wheels require `manylinux_2_28`, which the conda-forge Python doesn't advertise. Sourcing from conda-forge matches the interpreter's glibc. | conda-forge |
| `pip` | Declared under `pixi.toml` `[dependencies]` (conda-forge) because pixi conda envs don't ship pip by default. Required inside the deploy env so `pixi run -e find-expired-symbols pip install --no-deps --force-reinstall <wheel>` works. | conda-forge |
| `urllib` (stdlib) | HTTP for custom-venue checkers (Nado, Polymarket Perps, Vertex). | stdlib |
| `fastapi` | ASGI framework for the API service (`fh_symbol_check.api.server`). Provides route decorators, Pydantic-model request/response validation, and the auto-generated `/docs` (OpenAPI) console. Declared under `pixi.toml` `[dependencies]` (conda-forge) and `[project.dependencies]`. | conda-forge / pypi |
| `uvicorn[standard]` / `uvicorn-standard` | ASGI server that runs the FastAPI app. `uvicorn.run(app, host=..., port=...)` in `fh_symbol_check.api.main`. Under pixi it's the `uvicorn-standard` metapackage (equivalent of the `[standard]` extra: `httptools`, `uvloop`, `websockets`, `watchfiles`). | conda-forge / pypi |
| `jinja2` | Template engine for the HTMX views (`fh_symbol_check.api.views`). Uses the new Starlette `TemplateResponse(request, name, context)` signature. | conda-forge / pypi |
| `python-multipart` | Required by FastAPI whenever a route uses `Form(...)`. The HTMX form target (`POST /`) posts form-encoded data. | conda-forge / pypi |
| `pytest` | Tests. | dev only |
| `mypy` | Type-check new package; gate. | dev only |
| `ruff` | Lint new package; gate. | dev only |
| `types-PyYAML`, `types-PyMySQL` | mypy stubs. | dev only |
| `build` (PyPA) | Wheel builder — invoked as `python -m build --wheel`. | dev only |
| `setuptools`, `wheel` | PEP 517 build backend declared in `pyproject.toml`. | build-time |

Compatible-release ranges (`pymysql>=1.1`, `ccxt>=4`, `pyyaml>=6`, `truststore>=0.10`). `cryptography` and `pip` are unpinned in `pixi.toml` (conda-forge picks latest compatible with the resolved Python). No exact pins unless `pixi.lock` forces it.

## 7. Logging

```python
logging.basicConfig(
    level=level,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```
- One logger per module via `logging.getLogger(__name__)`.
- Credentials never reach `logger.*` (passed as `DBCreds` objects with masked repr).
- Unit test asserts the password substring does not appear in `caplog.text`.

## 8. VS Code Debug Config

`.vscode/launch.json`:
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Find Expired Symbols — TA-TKY-A-41",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/find_expired_symbols.py",
      "args": ["--hostname", "TA-TKY-A-41", "--log-level", "DEBUG"],
      "console": "integratedTerminal",
      "justMyCode": true
    },
    {
      "name": "Find Expired Symbols — JSON to file",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/find_expired_symbols.py",
      "args": [
        "--hostname", "TA-TKY-A-41",
        "--output", "json",
        "--output-file", "${workspaceFolder}/out.json"
      ],
      "console": "integratedTerminal"
    }
  ]
}
```

## 9. Verification Steps (per success criterion in requirements §6)

| # | Check | Command |
|---|---|---|
| 1 | Tool runs end-to-end (host filter) | `pixi run python find_expired_symbols.py --hostname TA-TKY-A-41` |
| 2 | `--all` works (no host/exchange filter) | `pixi run python find_expired_symbols.py --all` |
| 3 | `--exchange-name` filters at the DB level | `pixi run python find_expired_symbols.py --exchange-name BINANCE` |
| 4 | `--symbol` finds every occurrence of one symbol (implies `--all`) | `pixi run python find_expired_symbols.py --symbol IP/USDT-PERP` |
| 4b | `--symbol` accepts multiple space-separated values (OR semantics) | `pixi run python find_expired_symbols.py --symbol IP/USDT-PERP BTC/USDT-PERP` |
| 5 | `--exchange-grouping` swaps the summary table | `pixi run python find_expired_symbols.py --all --exchange-grouping` |
| 6 | JSON is valid | `pixi run python find_expired_symbols.py --all --output json --no-fail-on-invalid \| python -m json.tool >/dev/null` |
| 7 | CSV header correct | `pixi run python find_expired_symbols.py --all --output csv --no-fail-on-invalid \| head -n1` |
| 8 | Empty result | `pixi run python find_expired_symbols.py --hostname does-not-exist` → exit 0, empty report |
| 9 | Bad creds | rename creds file → exit 2, no password in stderr |
| 10 | No literal creds in new code | `rg -n '<password literal>' fh_symbol_check find_expired_symbols.py` → no matches |
| 11 | Parameterised SQL | `rg -n "f\"SELECT" fh_symbol_check/db.py` → no matches; `cursor.execute(sql, (param,))` present |
| 12 | Creds git-ignored | `git check-ignore .DBCreds.yaml` → ignored |
| 13 | Mapping NOT git-ignored | `git check-ignore fh_symbol_check/data/exchange_mapping.yaml` → empty (i.e. tracked) |
| 14 | Gates clean | `pixi run -e dev ruff check . && pixi run -e dev mypy fh_symbol_check && pixi run -e dev pytest -q` |
| 15 | Wheel builds and bundles mapping | `rm -rf dist/ && pixi run -e dev python -m build --wheel && pixi run -e dev python -m zipfile -l dist/*.whl \| rg exchange_mapping.yaml` |
| 16 | Console script installs + runs | `pip install dist/*.whl` (in a fresh venv) → `find-expired-symbols --help \| head -3` shows the argparse block |
| 17 | Bundled mapping resolves via wheel | `python -c "from fh_symbol_check.cli import DEFAULT_EXCHANGE_MAP; print(DEFAULT_EXCHANGE_MAP)"` points at `site-packages/fh_symbol_check/data/exchange_mapping.yaml` in the install |
| 18 | Service entry point installed | `find-expired-symbols-service --help \| head -3` shows the service argparse block |
| 19 | API JSON surface | `pixi run -e find-expired-symbols find-expired-symbols-service &` → `curl -sSf http://127.0.0.1:8000/health \| python -m json.tool` returns `status: "ok"` |
| 20 | API scan lifecycle | POST `/scans` with `{"exchange_name":"BINANCE"}` returns 202 with `id` + `state=queued`; polling `GET /scans/{id}` transitions to `state=done` with a populated `result` array |
| 21 | HTMX UI serves | `curl -sSf http://127.0.0.1:8000/` returns HTML with `<form id="scan-form"` and a `/static/htmx.min.js` script tag; `/static/htmx.min.js` is 200 |
| 22 | Systemd unit template ships | `python -m zipfile -l dist/*.whl \| rg find-expired-symbols.service` — the unit template is intentionally NOT in the wheel; it lives under `deploy/` in the repo. Operators grab it from the source tree. |
| 23 | Templates + static bundled | `python -m zipfile -l dist/*.whl \| rg 'fh_symbol_check/api/(templates\|static)'` shows both `templates/*.html` and `static/{htmx.min.js,app.css}` |

## 10. Out-of-Scope Reminders
- No persistent caching across runs.
- No FH config mutation.
- No remote scheduling.
- No auto-detection of translation rules — explicitly registered per FH `exchange_name`.
- No auto-discovery of custom-venue endpoints — each is hand-written in `custom_venues/<venue>.py` against the venue's documented API.

## 11. Packaging (pip-installable console script)

`pyproject.toml` structure (locked; edited only for version bumps and dep additions):

```toml
[build-system]
requires      = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name            = "find-expired-symbols"
version         = "0.1.0"
description     = "Report Feed Handler symbols that are no longer valid on their venues."
requires-python = ">=3.10,<3.14"
dependencies    = [
    "pymysql>=1.1", "ccxt>=4", "pyyaml>=6", "truststore>=0.10",
    "fastapi>=0.100", "uvicorn[standard]>=0.20", "jinja2>=3.1", "python-multipart>=0.0.6",
]

[project.scripts]
find-expired-symbols         = "fh_symbol_check.cli:run"
find-expired-symbols-service = "fh_symbol_check.api.main:run"

[tool.setuptools]
py-modules = ["check_delisted_symbol", "mysql_select_query"]

[tool.setuptools.packages.find]
include = ["fh_symbol_check*"]
exclude = ["tests*"]

[tool.setuptools.package-data]
fh_symbol_check = [
    "data/*.yaml",
    "api/templates/*.html",
    "api/static/*",
]
```

### Build & install flow (developer machine)

```bash
# One-time — install dev env (includes `build`)
pixi install -e dev

# Build the wheel
rm -rf dist/
pixi run -e dev python -m build --wheel

# Sanity check that the bundled mapping is in the wheel
pixi run -e dev python -m zipfile -l dist/find_expired_symbols-*.whl \
    | rg 'fh_symbol_check/data/exchange_mapping.yaml'
```

The wheel is portable pure-python (`py3-none-any`) — no per-platform build. Runtime deps (`pymysql`, `ccxt`, `pyyaml`, `truststore`) are resolved by pip at install time on the target.

### Runtime resolution of the bundled config

`fh_symbol_check.cli.DEFAULT_EXCHANGE_MAP` is computed at import time from `importlib.resources.files("fh_symbol_check") / "data" / "exchange_mapping.yaml"`. It works identically whether run from the source checkout or from an installed wheel. Overrideable by passing `--exchange-map`.

### Deploy story (pointer)

Everything about *how* to install and run the wheel on a Linux server — user-chosen install directory, pixi-managed env (`find-expired-symbols`) that provides Python + `pip` + all runtime deps, DB creds file placement (`$PIXI_PROJECT_ROOT/.DBCreds.yaml` via the task alias in `pixi.toml`), multi-user permissions appendix, scheduler wiring, and the `cryptography` `manylinux_2_28` workaround (both `cryptography` and `pip` are declared under `[dependencies]` in the shipped `pixi.toml` so they come from conda-forge) — lives in `deploy.md`. The API service track adds one systemd user unit (`deploy/find-expired-symbols.service`) and one follow-up command after each `pip install` (`systemctl --user restart find-expired-symbols`); full detail in `deploy.md`. Nothing about the deploy is checked in besides `pixi.toml`, `pixi.lock`, and the systemd unit template; those are the artefacts operators rsync alongside each new wheel.

## 12. API service (implementation notes)

The API service reuses `pipeline.run_scan` for the scan work and adds only three layers on top: a FastAPI HTTP surface, an in-memory JobStore, and a bounded ThreadPoolExecutor. See design §12 for the architecture-level view; the notes below record the implementation-level choices.

**Framework choice.** FastAPI + uvicorn + Jinja + HTMX — chosen for:
1. **Zero-JS UI.** HTMX polling replaces the need for WebSockets / SSE / a client-side JS framework. The whole UI is server-rendered HTML; the only client-side JS is the ~15-line inline chip filter in `result_table.html`.
2. **One dialect.** FastAPI's Pydantic-based request validation mirrors the CLI's argparse. `ScanRequest.@model_validator` reproduces the argparse error strings verbatim so both fronts emit the same wording — captured as a test in `tests/test_api_models.py`.
3. **Async lifespan.** FastAPI's `lifespan` context manager owns the background TTL sweeper as an `asyncio.Task`; on shutdown it's cancelled and the thread pool joined.

**Concurrency stacks.** Two independent pools compose:
- Outer: `ScanWorkers._executor` (default 2), one job per worker for the job's full lifetime.
- Inner: `classify_symbols` `ThreadPoolExecutor` (default 4), one `load_markets()` per worker.

Worst-case: `2 × 4 = 8` concurrent `load_markets` calls + 2 coordinators. All I/O-bound; the GIL isn't the bottleneck.

**Progress plumbing.** The new `on_group_done: Callable[[str], None] | None` kwarg on `classify_symbols` (see §4) fires from the coordinator thread after each `as_completed()` result. `pipeline.run_scan` wires it to bump an internal counter and re-emit a `ScanProgress` snapshot; the API's worker wires that further to `store.update_progress(job.id, p)`. Progress updates are lock-guarded (`JobStore._lock: threading.RLock`).

**TTL sweep.** `store.sweep()` iterates the dict once (holding the lock) and deletes completed jobs whose `completed_at` is older than TTL. In-flight jobs (`state in {queued, running}`) are never swept regardless of age. The sweeper runs on a fixed interval (default 60s) so a 1h TTL is honoured within a minute of expiry.

**Package data.** The `pyproject.toml` `[tool.setuptools.package-data]` glob covers `data/*.yaml`, `api/templates/*.html`, and `api/static/*` — so both `pip install` and `pixi run -e find-expired-symbols pip install --no-deps --force-reinstall` install the templates + `htmx.min.js` + `app.css` into `site-packages/fh_symbol_check/api/`. `build_app` resolves the packaged directories via `importlib.resources.files("fh_symbol_check").joinpath("api/{static,templates}")`, matching how `DEFAULT_EXCHANGE_MAP` resolves.

**Starlette version pin.** `TemplateResponse` requires the new `(request, name, context)` signature under Starlette >= 1.6 (shipped by `fastapi>=0.141`). This is what pixi resolves today; the templates would break on older Starlette. Not a compat concern in practice — we always deploy the shipped `pixi.lock`.

**Systemd unit rationale.** User unit rather than system unit for three reasons:
1. **No root required** — one `sudo` at first-time-setup (`loginctl enable-linger`) is the only privileged step; installs and restarts run entirely under the operator's account.
2. **Perms match the CLI** — the service reads `.DBCreds.yaml` as the operator user, using the same perms the CLI does.
3. **Portable across boxes** — the unit template only depends on `%h` (home) and pixi being on PATH; there's no `/opt/<app>` hard-coding to edit per host.

Version bumps: edit `[project.version]` in `pyproject.toml`, rebuild with `pixi run -e dev python -m build --wheel`, rsync the new wheel to `$FES_DIR` on the server, rerun `pixi run -e find-expired-symbols pip install --no-deps --force-reinstall <wheel>`. The exact same five steps serve first-time installs and updates.
