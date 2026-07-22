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
            └────────┬─────────┘       └───────────────────────┘   POLYMARKETPERPS, …)
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

## 2. Module Breakdown

```
FindExpiredSymbolsInFH/
├── pyproject.toml                 # packaging metadata + [project.scripts] console entry point
├── pixi.toml                      # dev environment (osx-arm64 + linux-64) + dev-only tools
├── find_expired_symbols.py        # source-checkout entry point (3-line wrapper around fh_symbol_check.cli.run)
├── fh_symbol_check/
│   ├── __init__.py
│   ├── cli.py                     # argparse + orchestration + run() console-script entry (--source flag)
│   ├── creds.py                   # load creds from YAML by section
│   ├── db.py                      # parameterised queries: fetch_feed_handlers + fetch_repeaters
│   ├── exchange_map.py            # load + lookup exchange_name → ccxt id (shared by both sources)
│   ├── symbol_translation.py      # per-exchange_name translators (shared by both sources)
│   ├── models.py                  # dataclasses: FeedHandlerRow, ResolvedTask, SymbolResult (all carry `source`)
│   ├── validator.py               # build_tasks, classify_symbols, filter_by_symbol (source-agnostic)
│   ├── reporter.py                # text/json/csv renderers; two-section detail + per-source summary tables
│   ├── logging_config.py          # structured logging setup
│   ├── data/
│   │   └── exchange_mapping.yaml  # DB exchange_name → ccxt id; ships inside the wheel as package data
│   └── custom_venues/             # non-ccxt venue checkers (registry-driven, see §3.9)
│       ├── __init__.py            #   CUSTOM_VENUES registry + helpers (is_custom_venue, get_checker)
│       ├── nado.py                #   Nado (`https://archive.prod.nado.xyz/v2/symbols`)
│       └── polymarket_perps.py    #   Polymarket Perps (`https://api.perpetuals.polymarket.com/v1/info/instruments`)
├── check_delisted_symbol.py       # EXISTING — refactored (option B, §4.1); shipped in wheel via `py-modules`
├── mysql_select_query.py          # EXISTING — refactored (option C, §4.2); shipped in wheel via `py-modules`
├── deploy/
│   └── shared-workspace-pixi.toml # template for the multi-user pixi workspace on the server
├── deploy.md                      # server runbook (see §11)
├── tests/                         # pytest suite (includes test_packaging.py smoke checks)
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
- Custom venues (see §3.9) are checked **before** the exchange-map lookup in `build_tasks` — `NADO`, `POLYMARKETPERPS`, etc. never need to be in `exchange_mapping.yaml`. The map is for ccxt-backed venues only.
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

### 3.9 `custom_venues/` package

Non-ccxt venues (FH-side exchanges that ccxt does not implement) are
supported through a registry-driven framework rather than per-venue
special cases in `validator.py`.

```python
# fh_symbol_check/custom_venues/__init__.py

CustomVenueChecker = Callable[[], Mapping[str, bool]]

CUSTOM_VENUES: dict[str, CustomVenueChecker] = {
    "NADO": nado.fetch_symbols,
    "POLYMARKETPERPS": polymarket_perps.fetch_symbols,
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

#### Shipped checkers
| FH `exchange_name` | Endpoint | Live signal | Translator |
|---|---|---|---|
| `NADO` | `GET https://archive.prod.nado.xyz/v2/symbols` | `trading_status == "live"` | `_translate_perp_strip_quote` |
| `POLYMARKETPERPS` | `GET https://api.perpetuals.polymarket.com/v1/info/instruments` | every returned instrument is treated as live (no status field) | `_translate_polymarketperps` |

Both checkers ship with a `User-Agent: FindExpiredSymbolsInFH/1.0 (symbol-validation)`
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
8. **Custom-venue framework** (done — §3.9): registry-driven non-ccxt venues with per-venue checkers; Nado and Polymarket Perps shipped, more parked in `task.md` pending FH-side input.

## 10. Risks

- ccxt rate-limits or geo-blocks (e.g. HTX in some regions) → mitigated by per-exchange `ERROR` rows.
- DB schema drift (e.g. column rename) → caught at query time as a `DBError`; optional schema-drift guard in §9.7.
- Internal symbol formats drift (new suffix, new exchange) → caught as unknown translation; identity is applied and the symbol most likely shows up as `DELISTED`, which is a recoverable false positive (operator inspects, adds a translator rule).
- Unknown `exchange_name` from a newly added feed handler → shows up as `ERROR` rows, easy to spot in the report; fix is one line in `exchange_mapping.yaml` (ccxt-backed) or one new file + registration in `custom_venues/` (non-ccxt).
- Custom-venue API drift (Polymarket / Nado restructure endpoints, add/rename fields) → the venue's tasks fail uniformly as `ERROR`, never silently misclassify; checker module needs updating to track the new shape.

## 11. Packaging & Deployment (pointer)

Architecture-level notes only — operational detail lives in `deploy.md`.

- **Packaging shape**: pip-installable distribution declared in `pyproject.toml`. Building `pixi run -e dev python -m build --wheel` produces a wheel that bundles the `fh_symbol_check` package, the two top-level helpers (`check_delisted_symbol.py`, `mysql_select_query.py` via `[tool.setuptools] py-modules`), and the code-tracked `data/exchange_mapping.yaml` as package data.
- **Console script**: `[project.scripts]` maps `find-expired-symbols` → `fh_symbol_check.cli:run`. That's the single entry point operators call.
- **Server model**: the recommended deploy is a shared pixi workspace at `/opt/find-expired-symbols/` (template: `deploy/shared-workspace-pixi.toml`) that installs the wheel into a conda-forge Python. This sidesteps host-Python issues (missing `venv`/`ensurepip`, older glibc, `manylinux_2_28` mismatches with the `cryptography` PyPI wheel). See `deploy.md` for the full runbook.
- **Scheduler contract**: `absolute path + CLI flags + exit code`. Exit codes (`0/1/2/130`) are frozen by `tests/test_packaging.py::test_exit_codes_exported` — anything the scheduler branches on stays stable across releases.
