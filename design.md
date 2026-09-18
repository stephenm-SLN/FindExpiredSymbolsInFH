# Design — Feed Handler Symbol Validity Checker

## 1. High-Level Architecture

```
            ┌──────────────────┐
 CLI args ─►│   cli (argparse) │
            └────────┬─────────┘
                     │
                     ▼
            ┌──────────────────┐       ┌──────────────────────┐
            │   creds loader   │──────►│  .DBCreds.yaml       │  (section: crypto_db)
            └────────┬─────────┘       └──────────────────────┘
                     │
                     ▼
            ┌──────────────────┐       MySQL @ 10.50.12.8  (crypto_db)
            │     db client    │──────►   • fh_config       (source="fh")
            │  (--source picks │           SELECT service_id, fh_name, hostname,
            │   fh / rp / both)│                  exchange_name, cover_names
            │                  │           FROM crypto_db.fh_config
            │                  │──────►   • repeater_feeds  (source="repeater")
            └────────┬─────────┘           SELECT hostname, app_name,
                     │                            exchange_name, instruments
                     │                     FROM crypto_db.repeater_feeds
                     │                     Both queries share the same WHERE
                     │                       hostname LIKE %s
                     │                       AND UPPER(exchange_name) = UPPER(%s)
                     │                     (clauses appear iff the corresponding
                     │                      CLI filter is set; `--all` drops
                     │                      the WHERE clause). Repeater rows
                     │                      land in the same FeedHandlerRow
                     │                      type with source="repeater", so all
                     │                      downstream stages are source-agnostic.
                     ▼
            ┌──────────────────┐       ┌───────────────────────┐
            │ exchange mapper  │──────►│ exchange_mapping.yaml │  (BINANCE→binance,
            │  + custom-venue  │       │                       │   HUOBIDM→htx, …)
            │     dispatch     │──────►│ custom_venues/ pkg    │  (NADO,
            └────────┬─────────┘       └───────────────────────┘   POLYMARKETPERPS,
                                                                    VERTEX family, …)
                     │
                     ▼
            ┌──────────────────┐
            │ symbol translator│  per-`exchange_name` rules (default = identity)
            └────────┬─────────┘
                     │  tasks: [(service_id, fh_name, exchange_name, ccxt_id,
                     │          ccxt_symbol, original_symbol), …]
                     ▼
            ┌──────────────────┐
            │  --symbol filter │  (optional; keeps tasks/errors whose
            └────────┬─────────┘   original_symbol OR ccxt_symbol matches
                     │             ANY value in the --symbol list)
                     │
                     ▼
            ┌──────────────────┐       ┌─────────────────────┐
            │  validator       │──────►│ exchange metadata   │  ccxt: one load_markets()
            │   (ccxt OR       │       └─────────────────────┘  per ccxt_id, in-process
            │   custom-venue)  │       ┌─────────────────────┐
            │                  │──────►│ custom-venue HTTP   │  e.g. Nado, Polymarket Perps
            └────────┬─────────┘       └─────────────────────┘
                     │  results: [SymbolResult, …]
                     ▼
            ┌──────────────────┐
            │ reporter         │  text | json | csv → stdout / file
            │  + summary       │  Text: 2 detail sections (fh / repeater) + 2
            │     tables       │        summary tables (one per source present)
            │                  │        OR --exchange-grouping: 1 per-(exchange,
            │                  │        source) table
            │                  │  JSON/CSV: 1 row per symbol, incl. `source`
            │                  │  + global LISTED/INACTIVE/DELISTED/ERROR line
            └──────────────────┘
```

Single-process, library-style modules glued together by a thin CLI entry point.

The **API service** (see §12) is a second front-end on top of the same core.
CLI and API both call `pipeline.run_scan()` — same DB fetch, same
mapping/translation, same classification, same result rows. The only
difference is what the front-end does with those rows: the CLI renders
text/JSON/CSV and exits; the API stores them in an in-memory `JobStore`
and lets clients poll or watch a live HTMX card in the browser.

```
                          ┌────────────────────────────┐
                          │  pipeline.run_scan()       │
                          │  (DB → tasks → classify)   │
                          └───────▲────────┬───────────┘
                                  │        │
                     ┌────────────┘        └────────────┐
                     │ synchronous                       │ async (in worker thread)
              ┌──────┴──────┐                    ┌───────┴──────────┐
              │  CLI (main) │                    │  API workers     │
              └──────┬──────┘                    └───────┬──────────┘
                     │                                   │
                     ▼                                   ▼
             reporter.render()                   JobStore (in-memory, TTL=1h)
             → text/json/csv                    ├──► GET /scans/{id}   (JSON)
             → stdout/file                      └──► GET / , /partial  (HTMX)
```

## 2. Module Breakdown

```
FindExpiredSymbolsInFH/
├── pyproject.toml                 # packaging metadata + [project.scripts] console entry point
├── pixi.toml                      # dev environment (osx-arm64 + linux-64) + dev-only tools
├── find_expired_symbols.py        # source-checkout entry point (3-line wrapper around fh_symbol_check.cli.run)
├── fh_symbol_check/
│   ├── __init__.py
│   ├── cli.py                     # argparse + orchestration + run() console-script entry (--source flag)
│   ├── pipeline.py                # run_scan() — the shared DB→classify path invoked by CLI and API workers
│   ├── creds.py                   # load creds from YAML by section
│   ├── db.py                      # parameterised queries: fetch_feed_handlers + fetch_repeaters
│   ├── exchange_map.py            # load + lookup exchange_name → ccxt id (shared by both sources)
│   ├── symbol_translation.py      # per-exchange_name translators (shared by both sources)
│   ├── models.py                  # dataclasses: FeedHandlerRow, ResolvedTask, SymbolResult (all carry `source`)
│   ├── validator.py               # build_tasks, classify_symbols (+ on_group_done cb), filter_by_symbol
│   ├── reporter.py                # text/json/csv renderers; two-section detail + per-source summary tables
│   ├── logging_config.py          # structured logging setup
│   ├── data/
│   │   └── exchange_mapping.yaml  # DB exchange_name → ccxt id; ships inside the wheel as package data
│   ├── api/                       # optional REST + HTMX UI front-end (see §3.10 and §12)
│   │   ├── main.py                #   find-expired-symbols-service console entry (argparse + uvicorn)
│   │   ├── server.py              #   build_app() factory + TTL sweeper
│   │   ├── routes.py              #   JSON API (POST /scans, GET /scans, GET /scans/{id}, GET /health)
│   │   ├── views.py               #   HTMX endpoints (GET /, POST /, GET /scans/{id}/partial)
│   │   ├── models.py              #   Pydantic schemas (ScanRequest, ScanJobDetail, …)
│   │   ├── jobs.py                #   thread-safe in-memory JobStore + TTL sweep
│   │   ├── workers.py             #   bounded ThreadPoolExecutor around pipeline.run_scan
│   │   ├── templates/*.html       #   Jinja templates (base, home, scan_card, result_table, form_error)
│   │   └── static/{htmx.min.js,app.css}  # bundled static assets; shipped as package data
│   └── custom_venues/             # non-ccxt venue checkers (registry-driven, see §3.11)
│       ├── __init__.py            #   CUSTOM_VENUES registry + helpers (is_custom_venue, get_checker)
│       ├── arcus.py               #   Arcus (`https://api.arcus.xyz/v1/markets`)
│       ├── injective.py           #   Injective LCD REST (spot + derivative)
│       ├── nado.py                #   Nado (`https://archive.prod.nado.xyz/v2/symbols`)
│       ├── ondoperps.py           #   Ondo Perps (`https://api.ondoperps.xyz/v1/markets`)
│       ├── polymarket_perps.py    #   Polymarket Perps (`https://api.perpetuals.polymarket.com/v1/info/instruments`)
│       ├── rhlighter.py           #   Robinhood Chain Lighter (`https://api.rh.lighter.xyz/api/v1/orderBookDetails`)
│       └── vertex.py              #   Vertex family — shut down July 2025; raises VenueGone, no network
├── check_delisted_symbol.py       # EXISTING — refactored (option B, §4.1); shipped in wheel via `py-modules`
├── mysql_select_query.py          # EXISTING — refactored (option C, §4.2); shipped in wheel via `py-modules`
├── deploy/
│   └── find-expired-symbols.service  # systemd user unit template for the API service (see §11)
├── deploy.md                      # server runbook (see §11)
├── tests/                         # pytest suite (includes API tests + test_packaging.py smoke checks)
└── .vscode/launch.json            # VS Code debug config
```

Why a package, not a single script? Keeps the units small and testable. The CLI module stays thin.

## 3. Data Flow & Module Contracts

### 3.1 `creds.py`
```python
@dataclass(frozen=True)
class DBCreds:
    host: str
    user: str
    password: str
    database: str

class CredsError(Exception): ...

def load_creds(path: Path, section: str = "crypto_db") -> DBCreds: ...
```
- YAML format expected:
  ```yaml
  crypto_db:
    host: 10.50.12.8
    database: crypto_db
    user: stephen.m
    password: '...'
  ```
- `DBCreds.__repr__` masks the password (`password='***'`).
- Errors raise `CredsError` with a generic message (never includes credential values).

### 3.2 `db.py`
```python
class DBError(Exception): ...

def fetch_feed_handlers(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]: ...

def fetch_repeaters(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]: ...
```
The two functions share the same signature and the same in-memory row type (`FeedHandlerRow` — see §3.5) and differ only in the source table and its column shape:

- **`fetch_feed_handlers`** — base SQL:
  ```sql
  SELECT service_id, fh_name, hostname, exchange_name, cover_names
  FROM crypto_db.fh_config
  ```
  Rows land in `FeedHandlerRow` with `source="fh"` and `service_id=<int>`.
- **`fetch_repeaters`** — base SQL:
  ```sql
  SELECT hostname, app_name, exchange_name, instruments
  FROM crypto_db.repeater_feeds
  ```
  Rows land in `FeedHandlerRow` with `source="repeater"`, `service_id=None`, and the DB `app_name` value stored in the `fh_name` slot (so downstream stages don't need to branch on source).

Both queries share the same helper (`_compose_sql`, `_run_query`) so the filter contract is identical:
- WHERE clauses are appended dynamically as `AND`-combined:
  - `hostname LIKE %s` when `hostname_pattern` is set (bound as `f"%{hostname_pattern}%"`).
  - `UPPER(exchange_name) = UPPER(%s)` when `exchange_name` is set (case-insensitive exact match).
- Both filters `None` ⇒ no WHERE clause (the `--all` path; full-table scan).
- Empty / null `cover_names` or `instruments` → row included with `symbols=()`, logged at DEBUG.
- Any pymysql exception → `DBError("failed to query <table>: …")`.

### 3.3 `exchange_map.py`
```python
class ExchangeMapError(Exception): ...

def load_exchange_map(path: Path) -> dict[str, str]: ...
def resolve(mapping: dict[str, str], exchange_name: str) -> str | None: ...
```
- File format (code-tracked, not a secret):
  ```yaml
  HUOBI: htx
  WOODEX: woo
  ```
- Lives at `fh_symbol_check/data/exchange_mapping.yaml` and ships inside the built wheel as package data (see `pyproject.toml`'s `[tool.setuptools.package-data]`). The console script's `--exchange-map` default resolves to this file via `importlib.resources.files("fh_symbol_check")`, so an installed operator picks it up automatically. Overrideable with an explicit `--exchange-map /path/to/other.yaml`.
- Lookup is **case-insensitive** on the DB key (DB values may drift in casing).
- Custom venues (see §3.11) are checked **before** the exchange-map lookup in `build_tasks` — `NADO`, `POLYMARKETPERPS`, etc. never need to be in `exchange_mapping.yaml`. The map is for ccxt-backed venues only.
- Unknown `exchange_name` (not in the map AND not in the custom-venue registry) → `resolve` returns `None`; the validator surfaces it as an `ERROR` row with `detail="unknown exchange_name=<X>; add it to exchange_mapping.yaml"`. The run continues.

### 3.4 `symbol_translation.py`
```python
Translator = Callable[[str], str]

# Registry — populated at module import time. Keyed by FH `exchange_name`
# (uppercase) so multiple FH venues sharing a single ccxt id can each have
# their own format. Defaults to identity for missing entries.
TRANSLATORS: dict[str, Translator] = {
    "BINANCEDM": _translate_perp_suffix,        # linear: BTC/USDT-PERP → BTC/USDT:USDT
    "BINANCEDMCOIN": _translate_perp_suffix_inverse,  # inverse: BTC/USD-PERP → BTC/USD:BTC
    "BYBITDM": _translate_perp_by_quote,        # mixed linear+inverse by quote
    "NADO": _translate_perp_strip_quote,        # custom venue: BTC/USD-PERP → BTC-PERP
    "POLYMARKETPERPS": _translate_polymarketperps,  # GOLD/USDC-PERP → GOLD-USD
    "INJECTIVE": _translate_injective,          # custom venue: BTC/USDC-PERP → BTC/USDC PERP
    "RHLIGHTER": _translate_rhlighter,          # custom venue: BTC/USDG-PERP → BTC
    "ARCUS": _translate_arcus,                  # custom venue: BTC/USD-PERP → BTC-USD
    "ONDOPERPS": _translate_ondoperps,          # custom venue: NVDA/USD-PERP → NVDA-USD.P
    # … see source for the full list
}

def translate(exchange_name: str, internal_symbol: str) -> str: ...
```

#### Why keyed by `exchange_name` and not `ccxt_id`
Multiple FH venues map to the same ccxt id with different conventions
(e.g. `HUOBI`, `HUOBIDM`, `HUOBICOINSWAP` all resolve to `htx` but use
different perp formats). Keying the registry by FH `exchange_name`
disambiguates these without collision.

#### Translator primitives currently shipped
| Primitive | Example | Used by (representative) |
|---|---|---|
| identity (default) | `BTC/USDT` → `BTC/USDT` | HUOBI, BINANCE, BYBIT spot, … |
| `_translate_perp_suffix` (linear) | `BTC/USDT-PERP` → `BTC/USDT:USDT` | BINANCEDM, GATEIODM, HUOBIDM, KUCOINDM, PHEMEXDMT, WOO, WOODEX, CBITL, CRYPTOCOMDM, KRAKENDM, WHITEBITDM |
| `_translate_perp_suffix_inverse` | `BTC/USD-PERP` → `BTC/USD:BTC` | BINANCEDMCOIN, HUOBICOINSWAP, PHEMEXDMCOIN |
| `_translate_perp_by_quote` | linear OR inverse depending on quote | BITGETDM, BYBITDM |
| `_translate_perp_strip_quote` | `BTC/USD-PERP` → `BTC-PERP` | NADO (custom venue) |
| `_translate_polymarketperps` | `GOLD/USDC-PERP` → `GOLD-USD` (+ `WTI→WTIOIL` remap) | POLYMARKETPERPS (custom venue) |
| `_translate_injective` | `BTC/USDC-PERP` → `BTC/USDC PERP` (spot identity) | INJECTIVE (custom venue) |
| `_translate_rhlighter` | `BTC/USDG-PERP` → `BTC` (spot identity) | RHLIGHTER (custom venue) |
| `_translate_arcus` | `BTC/USD-PERP` → `BTC-USD` | ARCUS (custom venue) |
| `_translate_ondoperps` | `NVDA/USD-PERP` → `NVDA-USD.P` | ONDOPERPS (custom venue) |

- Missing rule → identity. Logged at INFO once per `exchange_name` encountered, not per symbol.
- Pure functions, trivially unit-testable. New venues are added by registering a primitive (or a new one) in `TRANSLATORS`.

### 3.5 `models.py`
```python
SourceKind = Literal["fh", "repeater"]

@dataclass(frozen=True)
class FeedHandlerRow:
    fh_name: str                # fh_config.fh_name OR repeater_feeds.app_name
    hostname: str
    exchange_name: str          # raw DB value, e.g. "HUOBI"
    symbols: tuple[str, ...]    # parsed from cover_names / instruments CSV
    source: SourceKind = "fh"   # "fh" | "repeater"
    service_id: int | None = None  # None for repeaters (no such column)

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

SymbolStatus = Literal["LISTED", "INACTIVE", "DELISTED", "ERROR"]

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

- Repeater rows are stored in the same `FeedHandlerRow` type as feed handlers — the `app_name` DB column value goes into the `fh_name` slot. `source` disambiguates them and the reporter uses that to label / split the output. This keeps `build_tasks`, `filter_by_symbol`, `classify_symbols`, and the ccxt / custom-venue dispatch strictly source-agnostic.
- `service_id` is `int | None` because `repeater_feeds` has no such column; the JSON / CSV renderers emit `null` / empty for repeaters.

### 3.6 `validator.py`
```python
def build_tasks(
    rows: Iterable[FeedHandlerRow],
    exchange_map: dict[str, str],
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Returns (tasks_ready_for_classification, error_results_for_unmappable_rows)."""

def filter_by_symbol(
    tasks: list[ResolvedTask],
    errors: list[SymbolResult],
    symbols: Iterable[str],
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Keep only tasks/errors whose original_symbol OR ccxt_symbol equals
    ANY value in ``symbols``. Case-sensitive, full-string equality. Pure
    function. Empty ``symbols`` filters everything out."""

def classify_symbols(
    tasks: Iterable[ResolvedTask],
    *,
    concurrency: int = 4,
) -> list[SymbolResult]: ...
```

- `build_tasks` is pure (no I/O), source-agnostic (accepts feed handler + repeater rows interleaved), and routes each row in priority order:
  1. **Custom venue** (`is_custom_venue(row.exchange_name)` is `True`): tasks are tagged with a sentinel `ccxt_id = "custom:<EXCHANGE_NAME>"` (e.g. `custom:NADO`); the exchange map is **not** consulted for these.
  2. **ccxt-backed venue** (`resolve(exchange_map, row.exchange_name)` returns a non-None id): tasks get the resolved ccxt id.
  3. **Unknown**: every symbol on that row is emitted as an `ERROR` result with detail `"unknown exchange_name=<X>; add it to exchange_mapping.yaml"`.

  In all three cases `row.source` (and `row.service_id`) is copied through to the emitted `ResolvedTask` / `SymbolResult` so the reporter can render fh vs. repeater rows in their correct sections.
- `filter_by_symbol` is the implementation of `--symbol`. Applied **between** `build_tasks` and `classify_symbols` so the downstream load_markets / custom-venue fetch is only invoked for the surviving tasks. Multiple symbols use OR semantics — a row is kept if it matches any of the provided values on either side (FH or ccxt form).
- `classify_symbols` groups tasks by `ccxt_id` and dispatches:
  - **ccxt path**: calls `load_exchange_markets_safe()` (from refactored `check_delisted_symbol.py`) **once per ccxt id**, then calls `classify(markets, ccxt_symbol)` per task.
  - **Custom-venue path** (`ccxt_id` starts with `custom:`): calls `_classify_custom_venue(custom_id, group)`, which fetches the venue's symbol universe via `get_checker(custom_id)()` and classifies each task: present + live → `LISTED`; present + not-live → `INACTIVE`; missing → `DELISTED`. A single fetch failure emits `ERROR` for every task in the group.
- Errors caught **per exchange** in both paths: any `ccxt.NetworkError`, `ccxt.ExchangeError`, `MarketLoadError`, custom-venue `Fetch*Error`, or unexpected exception produces `ERROR` rows for every task on that exchange. The run continues for other exchanges.
- Optional bounded concurrency across exchanges via `concurrent.futures.ThreadPoolExecutor` (ccxt and HTTP sync I/O are GIL-friendly during network waits). Default `concurrency=4`.

### 3.7 `reporter.py`
```python
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
def summary_by_exchange(results: list[SymbolResult]) -> list[ExchangeSummary]: ...     # (exchange, source) group key

def keep_fhs_with_errors(results: list[SymbolResult]) -> list[SymbolResult]:
    """Subset to producers (keyed by (source, hostname, fh_name, exchange_name))
    that have at least one ERROR row. All rows of an offending producer are
    retained. `source` is part of the key so a repeater and an FH with the same
    name are never conflated."""
```

#### Text mode
- Detail area: rows grouped by `(hostname, name, exchange_name)`, split into two labelled sections when both sources contribute — `--- Feed handlers ---` first, then `--- Repeaters ---`. Sections with no printable rows are omitted, so a pure-fh (or pure-repeater) run only shows one section (or none, if nothing broke). Prints only `INACTIVE` / `DELISTED` / `ERROR` rows by default; `--show-listed` (or the `--symbol` auto-enable) adds `LISTED` rows. Every line includes `original_symbol` and `ccxt_symbol` so operators can grep both the DB and the venue.
- Followed by psql-style summary table(s):
  - **Default** (`--exchange-grouping=False`): up to two tables — `Summary by feed handler` (column header `fh_name`) for `source="fh"` rows and `Summary by repeater` (column header `app_name`) for `source="repeater"` rows. Empty tables are elided, so a single-source run renders exactly one table. Both tables share the shape `<name> | hostname | exchange_name | active | inactive | delisted | error | total dead | total` with a `TOTAL` footer.
  - **`--exchange-grouping=True`**: one row per `(exchange_name, source)` with `exchange_name | source | feed_handlers | active | inactive | delisted | error | total dead | total`, and a `TOTAL` footer whose `source` cell is left blank. Per-symbol detail blocks are suppressed when `suppress_details=True` (i.e. on stdout — the CLI keeps them when `--output-file` is set so files retain the full triage data).
- Ends with the global line `Summary: LISTED=N INACTIVE=N DELISTED=N ERROR=N`.
- All summary-table renderers go through a shared `_render_psql_table` helper for consistent box-drawing.

#### JSON
- Single array of `SymbolResult` objects (all of them, regardless of `show_listed` / `exchange_grouping`, so machine consumers always see everything). One row per symbol. Each object carries `source` (`"fh"` or `"repeater"`) and a nullable `service_id`.

#### CSV
- Header + rows; safe for spreadsheets. `_CSV_FIELDS` mirrors `SymbolResult` with `source` as the first column so downstream pivots can split by producer type; `service_id` is empty for repeater rows.

#### `--errors-only` filtering
- Implemented in `keep_fhs_with_errors`: identifies the set of `(source, hostname, fh_name, exchange_name)` keys with at least one `ERROR` row and keeps **all** rows of those producers (so the surviving summary remains complete). Applied to the results just before `render()`. Does not change the global summary line or exit code.

### 3.8 `cli.py`
- Two entry points, both exit-code-returning:
  - `main(argv=None) -> int` — the argparse-driven orchestration; used by unit tests and by the source-tree launcher `find_expired_symbols.py`.
  - `run() -> int` — wraps `main()` with a `KeyboardInterrupt` handler (→ 130) and a catch-all exception guard (→ 2 with `logger.exception`). `run` is the `[project.scripts]` target in `pyproject.toml`, so both `pixi run python find_expired_symbols.py …` and the installed `find-expired-symbols` console script land on the same code path.
  - Before dispatching to `main()`, `run()` calls `_install_system_trust_store()` which imports `truststore` and invokes `truststore.inject_into_ssl()`. This monkey-patches `ssl.create_default_context` so all subsequent HTTPS calls (ccxt + custom-venue urllib) honour the OS-native trust store (macOS Keychain / Linux system CA bundle / Windows cert store) instead of the Mozilla-only bundle that conda-forge Python ships with. This is what makes ccxt calls succeed on corp networks whose SSL-inspection proxy (Zscaler, Palo Alto, Netskope, …) re-signs HTTPS with an internal root CA — the corp root is in the OS store but not in Mozilla's. Missing / raising truststore is downgraded to a WARNING log; the tool falls back to the bundled bundle rather than crashing.
- Module-level `DEFAULT_EXCHANGE_MAP: Path` is computed once via `importlib.resources.files("fh_symbol_check")` and used as the `--exchange-map` argparse default; source-tree and installed-wheel invocations resolve to the same on-disk file.
- Parses args; loads creds + exchange map; orchestrates modules.
- Filter args (at least one of the four required):
  - `--hostname <STR>`, `--exchange-name <STR>`, `--symbol <STR> [<STR> ...]`, `--all`
  - `--all` is mutually exclusive with `--hostname` and `--exchange-name`. `--symbol` takes one or more space-separated values (argparse `nargs='+'`) and composes with any combination of the other three; used alone it implies `--all`.
- Producer selection: `--source {fh, rp, both}` (default `both`) — which producer tables to query. `fh` = `crypto_db.fh_config` only; `rp` = `crypto_db.repeater_feeds` only; `both` = concatenate rows from both. Filters apply to whichever tables are scanned.
- Report-shaping args: `--output {text,json,csv}`, `--output-file <PATH>`, `--show-listed`, `--errors-only`, `--exchange-grouping`.
- Behaviour args: `--concurrency <N>`, `--fail-on-invalid` / `--no-fail-on-invalid`, `--log-level`, `-v`/`--verbose`.
- Config args: `--creds-file <PATH>`, `--creds-section <NAME>`, `--exchange-map <PATH>` (default = `DEFAULT_EXCHANGE_MAP`).
- Pipeline order (after argparse + validation):
  1. `load_creds` → `load_exchange_map`.
  2. Fetch producers per `--source`: `fetch_feed_handlers(...)` if `source in {fh, both}`; `fetch_repeaters(...)` if `source in {rp, both}`. A failure on either query aborts with exit `2`; the error is scoped in the log line to the offending table. Empty result on either scan → WARNING (not fatal).
  3. `build_tasks(fh_rows + rp_rows, exchange_map)` — the two lists are concatenated; `build_tasks` is source-agnostic.
  4. If `--symbol` is set: `filter_by_symbol(tasks, early_errors, args.symbol)` — applied here so we don't pay for ccxt load_markets / custom-venue HTTP calls on rows we'd discard. `args.symbol` is a `list[str]`.
  5. `classify_symbols(tasks, concurrency)`; results = `early_errors + classified`.
  6. INFO summary log line.
  7. If `--errors-only`: `keep_fhs_with_errors(results)`.
  8. `render(..., show_listed=show_listed or bool(args.symbol), exchange_grouping=…, suppress_details=…)`.
- Exit codes:
  - operational failure (creds / DB / args / exchange-map / I/O) → `2`
  - any `DELISTED` or `INACTIVE` symbols and `--fail-on-invalid` (default) → `1`
  - `ERROR` rows alone do **not** force exit 1 — they signal "couldn't determine", not "delisted".
  - else → `0`
  - `KeyboardInterrupt` → `130`

### 3.9 `pipeline.py`

Shared orchestration entry: the middle third of the CLI (DB fetch →
`build_tasks` → optional `--symbol` filter → `classify_symbols`)
extracted into a pure library function so the API's scan workers can
call the same code path.

Two data classes:

- `ScanFilters` — the CLI's filter args translated into a frozen
  dataclass (`hostname`, `exchange_name`, `all_producers`, `symbols`,
  `source`, `concurrency`). `.validate()` reproduces argparse's
  mutual-exclusion + at-least-one rules with the same error strings
  used at the CLI. Callers (CLI, Pydantic layer at the HTTP boundary,
  in-process tests) all funnel through this one validator so the
  wording never diverges.
- `ScanProgress` — an immutable snapshot the pipeline emits at four
  phases (`querying_db`, `building_tasks`, `classifying`, `done`) plus
  one tick per completed ccxt group during the classifying phase. The
  progress callback is opt-in (`on_progress: Callable[[ScanProgress], None]
  | None`), fires from the coordinator thread of `classify_symbols`
  (see `on_group_done`), and swallows any exception so a misbehaving
  observer can't kill the scan.

`run_scan(filters, creds, exchange_map, *, on_progress=None) ->
list[SymbolResult]`:

1. `filters.validate()` — cheap safety net; callers usually validated
   already.
2. `_fetch_rows` — respects `filters.source`, logs the same
   `no fh_config / no repeater_feeds rows matched` WARNINGs the CLI
   used to emit inline, and lets `DBError` propagate unchanged. The
   CLI catches it and returns exit 2; the API worker catches it and
   marks the job `failed`.
3. `build_tasks(rows, exchange_map)` — unchanged.
4. `filter_by_symbol` if `filters.symbols` is non-empty — with the same
   log lines as before (`matched N/M task(s)`, `0 occurrences found`).
5. `classify_symbols(tasks, concurrency=…, on_group_done=<tick>)` — the
   `on_group_done` callback bumps the progress counter and re-emits a
   `ScanProgress` snapshot per completed ccxt group.
6. Returns `early_errors + live_results` — the same shape the CLI used
   to build inline.

The CLI now delegates the whole DB→classify block to `run_scan` and
keeps its argparse / logging / rendering / exit-code translation. The
API workers construct `ScanFilters` from `ScanRequest`, submit
`run_scan` to a `ThreadPoolExecutor`, and hook `on_progress` into the
`JobStore` so HTMX polling sees live updates. Any bug fix in `run_scan`
benefits both fronts in one commit.

### 3.10 `api/` subpackage (optional service front-end)

Adds a FastAPI service + HTMX browser UI that reuses `pipeline.run_scan`
for the scan work. Kept as an optional install (extra CLI entry point,
extra pixi task, extra systemd unit) so the base CLI still works
unchanged for schedulers that don't need it.

Module map (each intentionally single-purpose so the surface is easy
to reason about):

- `models.py` — Pydantic schemas.
  `ScanRequest` mirrors the CLI's argparse fields (same names, same
  validation error wording via `@model_validator`). `.to_filters()`
  produces a `ScanFilters`. `ScanJobSummary` / `ScanJobDetail` /
  `ProgressOut` / `SymbolResultOut` are the shapes clients see; the
  JSON round-trips with the CLI's `--output json` payload so machine
  consumers can share code across fronts. `extra="forbid"` on
  `ScanRequest` catches typos early.
- `jobs.py` — in-memory `JobStore` guarded by a `threading.RLock`.
  One dataclass `Job` per `POST /scans`, keyed by a UUID4 hex. State
  machine: `queued → running → done|failed`. `sweep()` evicts
  completed jobs whose `completed_at` is older than TTL (default 1h);
  in-flight jobs are never swept. Attempting to update a job that's
  been swept is a no-op logged at DEBUG — that's the race the worker
  can hit under contention and it must not crash the scan.
- `workers.py` — `ScanWorkers` owns a bounded `ThreadPoolExecutor`
  (default `max_workers=2`, tunable via
  `--max-concurrent-scans`). `.submit(filters, request_dump)` creates
  a `Job`, schedules `_run_job`, and returns the queued record.
  `_run_job` (on a pool thread): `mark_running` → `run_scan(…,
  on_progress=…)` → `mark_done(result_json=[asdict(r) for r in …])` or
  `mark_failed(error="<Type>: <msg>")`. The `on_progress` callback
  writes into the `JobStore`, so polling clients see the counter tick
  as each ccxt group finishes.
- `routes.py` — the JSON API surface.
  `POST /scans` → 202 + `Location: /scans/{id}` header + summary body.
  `GET /scans` → list of summaries, newest first.
  `GET /scans/{id}` → detail with `result: list[SymbolResultOut] |
  null` (populated once `state == "done"`).
  `GET /health` → `{status, version, active_scans, recent_scans}`.
  All routes read `store` / `workers` off `request.app.state`
  (populated by the factory in `server.py`) so tests can swap in
  different creds / maps / TTLs without touching module globals.
- `views.py` — the HTMX endpoints.
  `GET /` renders `home.html` (form + list of recent jobs). `POST /`
  parses the form-encoded body, converts to `ScanRequest`, and — on
  validation failure — returns the `form_error.html` partial with the
  CLI wording; on success returns the `scan_card.html` partial for
  the newly-submitted job for HTMX to insert at the top of the list.
  `GET /scans/{id}/partial` is the polling target; while `state in
  (queued, running)` the card carries `hx-get + hx-trigger="every
  1s"`, so it self-polls. When the swap replaces it with a done /
  failed version those attributes disappear and polling stops
  naturally. A swept-during-poll job returns an empty body → HTMX
  removes the card.
- `server.py` — the factory.
  `build_app(creds, exchange_map, *, job_ttl_seconds, max_concurrent_scans,
  sweep_interval_seconds, static_dir, templates_dir)` — every dep is
  passed in explicitly (no globals) so tests can drive isolated apps.
  Registers a lifespan-managed background asyncio task that calls
  `store.sweep()` on a fixed interval. Mounts `/static` iff the
  packaged static directory exists on disk (a wheel without the
  Checkpoint 3 assets would otherwise 500 on `/static/*`; graceful
  degrade to 404 is friendlier).
- `main.py` — the console script `find-expired-symbols-service`.
  argparse args mirror the CLI's creds / log flags plus service-specific
  ones (`--bind`, `--port`, `--job-ttl-seconds`, `--max-concurrent-scans`).
  Reuses `fh_symbol_check.cli._install_system_trust_store` so any ccxt
  call the worker makes benefits from the OS-native trust store on
  corp networks. Blocks on `uvicorn.run(app, host=…, port=…)` until
  the process is signalled.

Templates (all under `fh_symbol_check/api/templates/`, shipped as
package data):

- `base.html` — HTML shell; loads `/static/htmx.min.js` and
  `/static/app.css`. Header with app name / API-docs / recent-scans
  links; footer with version + job-TTL note.
- `home.html` — extends `base.html`; filter form (source / hostname /
  exchange_name / symbol / concurrency + checkboxes for all /
  show_listed / errors_only), then a scans list that's the HTMX
  target for `POST /`.
- `scan_card.html` — one card for one job. State-aware:
  `queued` / `running` shows a progress bar + phase / rows-scanned /
  N/M exchanges text and carries the polling triggers;
  `done` shows completion time + row count + a JSON link + the
  result table; `failed` shows the error string in a red panel.
- `result_table.html` — status-filter chip row (`All`, `LISTED`,
  `INACTIVE`, `DELISTED`, `ERROR`) plus a stripped-down table with
  status + source + hostname + fh_name + exchange + original + ccxt +
  detail. A ~15-line inline `<script>` toggles a `data-status`
  attribute on the container; a CSS attribute selector hides
  non-matching rows. No JS framework dependency beyond HTMX itself.
- `form_error.html` — inline validation error card returned in place
  of a scan card when the form fails Pydantic validation.

Static assets (shipped as package data):

- `htmx.min.js` — HTMX 1.9.12, MIT-licensed, ~48KB minified. Vendored
  intentionally so the browser never hits a CDN.
- `app.css` — ~200 lines. CSS variables for light/dark theme
  (`prefers-color-scheme: dark`), status pills, progress bar,
  responsive form grid.

### 3.11 `custom_venues/` package

Non-ccxt venues (FH-side exchanges that ccxt does not implement) are
supported through a registry-driven framework rather than per-venue
special cases in `validator.py`.

```python
# fh_symbol_check/custom_venues/__init__.py

CustomVenueChecker = Callable[[], Mapping[str, bool]]

CUSTOM_VENUES: dict[str, CustomVenueChecker] = {
    "NADO": nado.fetch_symbols,
    "POLYMARKETPERPS": polymarket_perps.fetch_symbols,
    "INJECTIVE": injective.fetch_symbols,
    "RHLIGHTER": rhlighter.fetch_symbols,
    "ARCUS": arcus.fetch_symbols,
    "ONDOPERPS": ondoperps.fetch_symbols,
    "VERTEX": vertex.fetch_vertex,
    "AVAVERTEX": vertex.fetch_avavertex,
    "BERAVERTEX": vertex.fetch_beravertex,
    "MNTVERTEX": vertex.fetch_mntvertex,
    "SOVERTEX": vertex.fetch_sovertex,
}

CUSTOM_VENUE_PREFIX = "custom:"   # sentinel prefix on ResolvedTask.ccxt_id

def is_custom_venue(exchange_name: str) -> bool: ...
def custom_id_for(exchange_name: str) -> str:     # "NADO" → "custom:NADO"
    ...
def get_checker(custom_id: str) -> CustomVenueChecker: ...
```

#### Contract per checker module
Each `fh_symbol_check/custom_venues/<venue>.py` must expose:

```python
def fetch_symbols() -> dict[str, bool]:
    """Return {VENUE_SYMBOL_UPPER: is_live} for the venue's whole universe.

    Raise on any failure to fetch / parse — the validator catches it and
    emits ERROR rows for every task on that venue."""
```

The checker is the single source of truth for that venue's universe:
- Keys in the returned dict are uppercased symbol strings exactly as the
  venue's API names them (e.g. `BTC-PERP`, `GOLD-USD`).
- Values are `True` for live and `False` for present-but-not-tradable
  (when the venue exposes such a flag). Venues without an
  active/inactive flag (e.g. Polymarket Perps' `/v1/info/instruments`)
  always return `True`; they can only produce `LISTED` and `DELISTED`,
  never `INACTIVE`.

#### How a custom venue is dispatched
1. `build_tasks` checks `is_custom_venue(row.exchange_name)` **before**
   the `exchange_map` lookup. If true, every symbol on the row becomes a
   `ResolvedTask` with `ccxt_id = "custom:<EXCHANGE_NAME>"` and the
   symbol passes through the same `translate(exchange_name, sym)` step
   as ccxt-backed venues (so the registered `_translate_*` primitives
   apply).
2. `classify_symbols` groups by `ccxt_id` as usual; the sentinel
   `custom:*` prefix is detected in `_classify_one_exchange` which then
   routes to `_classify_custom_venue(custom_id, group)`.
3. `_classify_custom_venue` calls the checker **once** for the whole
   group, then per-task classifies as LISTED / INACTIVE / DELISTED.
   A checker that raises `VenueGone` (venue permanently shut down)
   emits DELISTED for every task with the exception message as
   `detail` — not ERROR.

#### Shipped checkers
| FH `exchange_name` | Endpoint | Live signal | Translator |
|---|---|---|---|
| `NADO` | `GET https://archive.prod.nado.xyz/v2/symbols` | `trading_status == "live"` | `_translate_perp_strip_quote` |
| `POLYMARKETPERPS` | `GET https://api.perpetuals.polymarket.com/v1/info/instruments` | every returned instrument is treated as live (no status field) | `_translate_polymarketperps` |
| `VERTEX` / `AVAVERTEX` / `BERAVERTEX` / `MNTVERTEX` / `SOVERTEX` | **No fetch.** Vertex Protocol shut down July 2025 (Ink Foundation merger). Checker raises `VenueGone`; validator emits `DELISTED` for every task. Former hosts `archive.*.vertexprotocol.com` are recorded only so the detail can name them. | n/a (venue gone) | passthrough |
| `INJECTIVE` | `GET https://sentry.lcd.injective.network/injective/exchange/v1beta1/{spot,derivative}/markets?status={Active,Paused,Expired}` (6 calls; `Demolished` omitted so those classify as DELISTED) | `status == "Active"` | `_translate_injective` (`BTC/USDC-PERP` → `BTC/USDC PERP`; spot identity) |
| `RHLIGHTER` | `GET https://api.rh.lighter.xyz/api/v1/orderBookDetails` — Robinhood Chain Lighter, **not** ccxt `lighter` / mainnet | `status == "active"` | `_translate_rhlighter` (`BTC/USDG-PERP` / `BTC-PERP` → `BTC`; spot identity) |
| `ARCUS` | `GET https://api.arcus.xyz/v1/markets` — dYdX Labs DEX, not in ccxt | `status == "ONLINE"` | `_translate_arcus` (`BTC/USD-PERP` → `BTC-USD`) |
| `ONDOPERPS` | `GET https://api.ondoperps.xyz/v1/markets` — Ondo Perps, not in ccxt | every returned `tradingPairs` entry is treated as live (no status field) | `_translate_ondoperps` (`NVDA/USD-PERP` → `NVDA-USD.P`) |

All checkers ship with a `User-Agent: FindExpiredSymbolsInFH/1.0 (symbol-validation)`
header (Nado's WAF blocks the default `Python-urllib/3.x`).

## 4. Integration with the Two Helper Scripts (refactors completed)

Per `requirements.md` §8.7, option (b) was approved and applied. The diffs were minimal and preserve existing CLI behaviour.

### 4.1 `check_delisted_symbol.py` — minimal refactor (done)

Extracted two pure-library entry points; kept existing functions as thin wrappers; `sys.exit` calls live in `main()` only.

```python
class MarketLoadError(Exception): ...

def load_exchange_markets_safe(exchange_id: str) -> tuple[ccxt.Exchange, dict]:
    """Raises MarketLoadError instead of sys.exit. Pure library function."""

def classify(markets: dict, symbol: str) -> tuple[Literal["LISTED","INACTIVE","DELISTED"], str]:
    """Return (status, detail). Pure function; no I/O, no prints."""
```

`check_symbol`, `check_multiple`, and the existing CLI behave bit-for-bit the same as today.

### 4.2 `mysql_select_query.py` — minimal refactor (done)

- Credential defaults removed from `__init__` (callers must pass them explicitly — prevents accidental connections with embedded creds).
- Optional `params` argument added to `fetch_query_results(self, query, params=None, return_header=False)` so callers can use `%s` placeholders.
- The `if __name__ == "__main__":` block reads creds from `.DBCreds.yaml` (section `crypto_db`) so its behaviour stays useful without baked-in creds.

## 5. Error Handling

| Layer | Failure | Behaviour |
|---|---|---|
| CLI | bad args | argparse prints help, exit `2` |
| Creds | file missing / unreadable / malformed / missing section | `CredsError`, log without values, exit `2` |
| DB | connect / auth / network / SQL error on either `fh_config` or `repeater_feeds` | `DBError` (message scopes the failing table), exit `2` |
| DB | zero matching rows (either or both scans) | log WARNING per empty scan, empty report, exit `0` |
| Exchange map | file missing / malformed | `ExchangeMapError`, exit `2` |
| Exchange map | unknown `exchange_name` (also not in custom-venue registry) | per-row `ERROR` result, run continues |
| Translator | no rule for `exchange_name` | identity, log DEBUG once per `exchange_name` |
| Validator (ccxt) | per-exchange `ccxt.NetworkError` / `ExchangeError` / `MarketLoadError` | all that exchange's tasks → `ERROR` rows, run continues |
| Validator (custom venue) | checker raises (HTTP / parse failure) | all that venue's tasks → `ERROR` rows with `detail="custom venue fetch failed: …"`, run continues |
| Validator (custom venue) | checker raises `VenueGone` (venue permanently shut down) | all that venue's tasks → `DELISTED` with the shutdown message as `detail` |
| Validator (custom venue) | checker not registered for sentinel `custom:X` | all that venue's tasks → `ERROR` rows, run continues |
| Reporter | I/O error on `--output-file` | log + exit `2` |
| Anywhere | unexpected exception | logged with stack trace at ERROR, exit `2` |
| Anywhere | `KeyboardInterrupt` | friendly message, exit `130` |

## 6. Config & Secrets Handling

- `.DBCreds.yaml` is loaded once at startup; `DBCreds` is passed explicitly — no module-level globals.
- `DBCreds.__repr__` masks password.
- No `logger.*` call ever receives the full `DBCreds` object. A unit test asserts the password substring does not appear in `caplog.text`.
- `.DBCreds.*` is git-ignored as a Phase-1 task. `exchange_mapping.yaml` is **NOT** git-ignored (code-tracked config).

## 7. Logging

Standard `logging`. One logger per module. Root handler configured by `cli.main`:
```
%(asctime)s %(levelname)-7s %(name)s: %(message)s
```
- DEBUG: per-symbol decisions, the bound LIKE-pattern (without param values to be paranoid), per-exchange counts.
- INFO: high-level progress (e.g. `loaded N feed-handler rows`, `loaded markets for htx (1234 symbols)`).
- WARNING: empty result, unknown exchange_name, missing translator rule.
- ERROR: per-exchange failure, malformed row.

## 8. Testing Strategy

Unit tests live in `tests/`. No live DB or ccxt network calls. Test count
grows with the project; see `pytest -q` for the current total. Files:

- `test_creds.py` — load by section, missing file, missing section, malformed YAML, repr masks password, password never appears in `caplog.text`.
- `test_db.py` — stubbed `pymysql.connect`; asserts the SQL uses `%s`, the bound params match the typed filters, and the dynamic-WHERE composition is correct for all four combinations of (`--hostname` set/unset × `--exchange-name` set/unset). Covers both `fetch_feed_handlers` (source="fh", int service_id) and `fetch_repeaters` (source="repeater", `app_name` in the `fh_name` slot, `service_id=None`, DBError message references `repeater_feeds`).
- `test_exchange_map.py` — load mapping, case-insensitive lookup, unknown name returns None, YAML error → `ExchangeMapError`.
- `test_symbol_translation.py` — every registered translator primitive against representative samples (linear / inverse / by-quote / strip-quote / polymarket-remap), plus dispatch tests covering every key in `TRANSLATORS`.
- `test_validator.py` — fake markets dict → LISTED / INACTIVE / DELISTED; simulated `MarketLoadError` → ERROR rows for that exchange only; unmapped `exchange_name` → ERROR row before ccxt is touched; custom-venue dispatch (LISTED/INACTIVE/DELISTED and fetch-failure ERROR); `filter_by_symbol` (both sides, case-sensitivity, exact-not-substring, no mutation, multi-FH survival, multi-value OR semantics, mixed FH/ccxt forms in one call, duplicate deduplication, empty-iterable behaviour); source propagation from `FeedHandlerRow` → `ResolvedTask` → `SymbolResult` for both fh and repeater inputs (including unknown-exchange error path and post-classify path).
- `test_reporter.py` — text contains all DELISTED rows; json parses; csv header matches (`source` first); per-source summary tables (fh vs repeater; each renders only when non-empty; column headers `fh_name` vs `app_name`); per-exchange summary table (`--exchange-grouping`) replaces the per-source tables and groups by `(exchange, source)` with an added `source` column and TOTAL row aggregation; two-labelled-detail-section split (`--- Feed handlers ---` / `--- Repeaters ---`) with strict ordering; `keep_fhs_with_errors` disambiguates by `(source, hostname, fh_name, exchange_name)` so a repeater ERROR doesn't drag in a same-named FH; suppress-details on stdout vs file; JSON/CSV unaffected by text-only flags.
- `test_cli.py` — argparse-level acceptance of every permitted flag combination; preservation of the `--all` vs `--hostname`/`--exchange-name` exclusivity rule; widened "at least one of" gate accepting `--symbol`; `--source` accepts `fh`/`rp`/`both`, defaults to `both`, and rejects unknown choices.
- `test_custom_venues_nado.py` — fixture-JSON `_parse_symbols`, network mocking for `fetch_symbols`, happy path / invalid JSON / network errors.
- `test_custom_venues_polymarket_perps.py` — same shape, against the Polymarket Perps instrument-list fixture.
- `test_custom_venues_vertex.py` — historical `_parse_symbols` fixture tests kept; `_EDGES` distinct-hostname + expected-subdomain checks; `CUSTOM_VENUES` registration; `_fetch` raises `VenueGone` naming the edge and former host (no network); unknown edge still `KeyError`.
- `test_custom_venues_injective.py` — LCD spot + derivative parser (Active/Paused/Expired/Demolished, trailing-space ticker strip, wrapped vs unwrapped market dicts), 6-URL fetch composition (3 statuses × 2 kinds, Demolished omitted), Active-wins-over-Paused merge, UA + Accept headers, invalid-JSON / network-error (URL in message), `CUSTOM_VENUES` registration.
- `test_custom_venues_rhlighter.py` — RH Lighter parser (perp + spot, active/inactive, ticker strip), fetch URL + headers, invalid-JSON / network-error (URL in message), `CUSTOM_VENUES` registration.
- `test_custom_venues_arcus.py` — Arcus parser (ONLINE/OFFLINE, ticker strip), fetch URL + headers, invalid-JSON / network-error (URL in message), `CUSTOM_VENUES` registration.
- `test_custom_venues_ondoperps.py` — Ondo Perps parser (market field, ticker strip, success=false), fetch URL + headers, invalid-JSON / network-error (URL in message), `CUSTOM_VENUES` registration.
- `test_packaging.py` — smoke checks for the pip-installable packaging contract: `run` is importable and is the exact callable that `pyproject.toml`'s `[project.scripts]` points at; `--help` succeeds and mentions every filter flag; `DEFAULT_EXCHANGE_MAP` exists on disk, lives inside `fh_symbol_check/data/`, and loads as a `{str: str}` YAML dict; exit-code constants are frozen at `0/1/2/130`.

Manual smoke tests against the real host are documented in `task.md`.

## 9. Suggested Improvements

Status legend: ✅ done, ⏳ open.

1. **`mysql_select_query.py`** future-proofing (beyond the agreed refactor):
   - ⏳ Use `with` context-manager pattern (`__enter__`/`__exit__`) so connections are guaranteed closed even on exception.
   - ⏳ Add `connect_timeout`/`read_timeout` to `pymysql.connect`.
2. **`check_delisted_symbol.py`** future-proofing (beyond the agreed refactor):
   - ⏳ Cache `load_markets()` results in-process so multiple calls with the same `exchange_id` don't re-fetch (the validator does this at its layer, so this is duplication of concern — only worth doing if the helper is reused elsewhere).
   - ⏳ Add an option to enable `exchange.enableRateLimit = True`.
   - ⏳ Replace some prints with `logging` so library callers can route output.
3. **Symbol translation**: a `reverse_translate` for cases where the report should show both formats side-by-side — **already covered** by `SymbolResult` keeping both `original_symbol` and `ccxt_symbol`.
4. ⏳ **Output**: ANSI colours for `text` mode when stdout is a TTY (red for `DELISTED`/`INACTIVE`).
5. ✅ **Type checking + lint**: `mypy` (per `mypy.ini`) and `ruff` are now part of the gate set; both pass.
6. ⏳ **Pre-commit hook**: block commits that contain the literal password from `.DBCreds.yaml`.
7. ⏳ **Schema drift guard**: at startup, `DESCRIBE crypto_db.fh_config` and assert the expected columns exist; print a clear error if the schema changes.
8. **Custom-venue framework** (done — §3.11): registry-driven non-ccxt venues with per-venue checkers; Nado, Polymarket Perps, Injective, Robinhood Lighter, Arcus, Ondo Perps, and the Vertex family shipped, more parked in `task.md` pending FH-side input.

## 10. Risks

- ccxt rate-limits or geo-blocks (e.g. HTX in some regions) → mitigated by per-exchange `ERROR` rows.
- DB schema drift (e.g. column rename) → caught at query time as a `DBError`; optional schema-drift guard in §9.7.
- Internal symbol formats drift (new suffix, new exchange) → caught as unknown translation; identity is applied and the symbol most likely shows up as `DELISTED`, which is a recoverable false positive (operator inspects, adds a translator rule).
- Unknown `exchange_name` from a newly added feed handler → shows up as `ERROR` rows, easy to spot in the report; fix is one line in `exchange_mapping.yaml` (ccxt-backed) or one new file + registration in `custom_venues/` (non-ccxt).
- Custom-venue API drift (Polymarket / Nado restructure endpoints, add/rename fields) → the venue's tasks fail uniformly as `ERROR`, never silently misclassify; checker module needs updating to track the new shape.

## 11. Packaging & Deployment (pointer)

Architecture-level notes only — operational detail lives in `deploy.md`.

- **Packaging shape**: pip-installable distribution declared in `pyproject.toml`. Building `pixi run -e dev python -m build --wheel` produces a wheel that bundles the `fh_symbol_check` package (including `api/`), the two top-level helpers (`check_delisted_symbol.py`, `mysql_select_query.py` via `[tool.setuptools] py-modules`), the code-tracked `data/exchange_mapping.yaml`, and the API's Jinja templates + static assets (`htmx.min.js`, `app.css`) as package data.
- **Console scripts**: `[project.scripts]` maps two entry points — `find-expired-symbols` → `fh_symbol_check.cli:run` (the CLI, unchanged) and `find-expired-symbols-service` → `fh_symbol_check.api.main:run` (the API service). Operators pick either or both.
- **Server model**: deploy is a user-chosen directory (`/opt/find-expired-symbols/`, `/home/<user>/api/find-expired-symbols/`, etc.) containing the built wheel + the repo's own `pixi.toml` + `pixi.lock` (rsync'd verbatim). A dedicated `find-expired-symbols` pixi environment declared in `pixi.toml` provides the Python interpreter and every runtime dep from conda-forge (Python, `pymysql`, `pyyaml`, `truststore`, `cryptography`, `pip`, `fastapi`, `uvicorn-standard`, `jinja2`, `python-multipart`) plus `ccxt` from PyPI. The wheel is installed on top with `pixi run -e find-expired-symbols pip install --no-deps --force-reinstall …`, which lets the same five steps serve both fresh installs and updates. Sourcing `cryptography` from conda-forge sidesteps the `manylinux_2_28` mismatch with the conda-forge Python's `manylinux_2_26` wheel tag; `pip` in `[dependencies]` is what makes the `pip install` step work at all (pixi conda envs otherwise ship without pip). The API service is supervised via `deploy/find-expired-symbols.service` — a systemd **user** unit invoked with `systemctl --user enable --now` + `loginctl enable-linger`. See `deploy.md` for the full runbook.
- **Scheduler contract**: `absolute path + CLI flags + exit code`. Exit codes (`0/1/2/130`) are frozen by `tests/test_packaging.py::test_exit_codes_exported` — anything the scheduler branches on stays stable across releases. Machine consumers can equivalently `POST /scans` to the API and poll `GET /scans/{id}` for state; both routes converge on the same result rows.

## 12. Service architecture (API + HTMX UI)

The service is a **second front-end** on top of the pipeline — nothing in
`pipeline.py` or below knows about HTTP. It exists because interactive
users (browser) and machine consumers (other services) both wanted an
on-demand scan surface that doesn't require SSH + CLI. The trade-off
is deliberate: no persistence, no auth, no cross-node coordination.

**Non-goals for v1** (spelled out here so they don't accidentally
happen later):

- Persistent storage — jobs and results live in a `dict[str, Job]` and
  are dropped 1h after completion, or on process restart.
- Authentication — none. Rely on the network perimeter / a reverse proxy.
- Multi-node — one process, one memory space. Horizontal scaling would
  require moving `JobStore` to shared storage (Redis, Postgres); out of
  scope.
- Cancellation of an in-flight scan — no `DELETE /scans/{id}`. Users
  who really need this can `systemctl --user restart` and lose ~15s of
  work.
- WebSockets / SSE — HTMX polling (1s) is good enough at v1 scan durations
  (seconds to a few minutes), removes an entire failure mode
  (long-lived connections through corp proxies), and keeps the client-side
  code to zero JS.

### 12.1 Request lifecycle

```
   POST /scans (JSON)                             POST / (form)
       │                                              │
       │  ScanRequest.model_validate                  │  Form(...) fields → ScanRequest
       │  ↓                                           │  ↓
       └──────────►  ScanRequest.to_filters() ────────┘
                              │
                              ▼
                     ScanWorkers.submit(filters, request_dump)
                              │
                              │  (thread pool, bounded)
                              ▼
                     Job (state=queued) → JobStore
                              │
                              ▼
                     store.mark_running(job.id)
                              │
                              ▼
                     pipeline.run_scan(filters, creds, exchange_map,
                                       on_progress=lambda p: store.update_progress(...))
                              │
                              │  every ccxt group finished:
                              │  store.update_progress(job.id, ScanProgress(…))
                              │
                              ▼
                     store.mark_done(job.id, result_json=[asdict(r) for r in …])
                              │
                              ▼
                    (poll target)
                              │
   GET /scans/{id}    ────────┼───►   ScanJobDetail (JSON)
                              │
   GET /scans/{id}/partial ───┴───►   scan_card.html (state-aware HTML)

   TTL sweeper (asyncio task, every 60s):
        for j in store where j.completed_at < now - TTL: del store[j.id]
```

### 12.2 Concurrency model

Two independent pools stack up:

1. **API scan pool** (`ScanWorkers._executor`, default 2 workers) —
   caps how many scans run in parallel at the service level. Each
   worker runs `pipeline.run_scan` on one job for its full lifetime.
2. **ccxt exchange pool** (`validator.classify_symbols` internal
   `ThreadPoolExecutor`, default 4 workers per scan) — caps how many
   `load_markets()` calls fly in parallel *within* one scan.

Nesting is deliberate: the outer pool bounds server-wide fan-out;
the inner pool bounds per-scan fan-out. Worst case: `2 × 4 = 8`
concurrent `load_markets` calls plus 2 coordinator threads. The
`GIL` isn't the bottleneck — this is all I/O-bound HTTP.

`JobStore` uses `threading.RLock`; every mutation is atomic. Progress
callbacks fire from the coordinator thread of `classify_symbols` (the
`as_completed` loop), which lives inside the top-level `ScanWorkers`
thread — so ordering is well-defined: `mark_running` before any
`update_progress`, `update_progress` monotonic on `completed_producers`,
`mark_done`/`mark_failed` last.

### 12.3 HTMX interaction pattern

The UI leans on one HTMX capability: **polling that stops itself when
the swapped-in element no longer has an `hx-trigger` attribute**.

- `POST /` returns a `scan_card.html` fragment. If the job is
  `queued` / `running`, that fragment carries
  `hx-get="/scans/{id}/partial" hx-trigger="every 1s"
  hx-swap="outerHTML"`. HTMX polls itself.
- Each poll returns the current state. While still running, the
  swapped-in element has the same hx-* attrs, so polling continues.
- When `state == "done"` or `"failed"`, the returned fragment has
  **no** hx-* attrs. HTMX swaps in, polling stops naturally.
- If the job was TTL-swept during polling, `GET /scans/{id}/partial`
  returns an empty body — HTMX replaces the card with nothing and
  stops polling.

This gives us live updates without WebSockets, without long polling,
without a single line of custom JavaScript beyond the ~15-line inline
status-chip filter in `result_table.html`.

### 12.4 Testing surface

Test files stack the same way as the module split:

- `tests/test_api_models.py` — Pydantic validation. Locks the CLI /
  API error-string parity.
- `tests/test_api_jobs.py` — `JobStore` state machine, TTL sweep,
  concurrent-update thread safety.
- `tests/test_api_routes.py` — end-to-end JSON API via `TestClient`;
  stubs `pipeline.fetch_feed_handlers` / `pipeline.fetch_repeaters`
  and `validator.load_exchange_markets_safe` so scans run offline in
  milliseconds. Checks 202 + Location header, poll-to-done, DB-error
  path, 404 on unknown id.
- `tests/test_api_templates.py` — HTMX rendering; asserts on:
  form present + HTMX bootstrap + empty state; scan card has polling
  trigger while running; done card has result rows and no trigger;
  form_error partial carries the CLI wording; static assets serve.
- `tests/test_packaging.py::test_service_run_is_a_second_console_script_entry_point`
  — locks the `find-expired-symbols-service = fh_symbol_check.api.main:run`
  entry point so a rename can't silently break the systemd unit.
