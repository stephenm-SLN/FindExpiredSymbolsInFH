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
            ┌──────────────────┐       MySQL (crypto_db.fh_config @ 10.50.12.8)
            │     db client    │──────►   SELECT service_id, fh_name, hostname,
            └────────┬─────────┘                exchange_name, cover_names
                     │                       FROM crypto_db.fh_config
                     │                       WHERE  hostname LIKE %s
                     │                          AND UPPER(exchange_name) = UPPER(%s)
                     │                       (clauses appear iff the
                     │                        corresponding CLI filter is set;
                     │                        `--all` drops the WHERE clause)
                     ▼
            ┌──────────────────┐       ┌──────────────────────┐
            │ exchange mapper  │──────►│ exchange_mapping.yaml │  (BINANCE→binance,
            │  + custom-venue  │       │                       │   HUOBIDM→htx, …)
            │     dispatch     │──────►│ custom_venues/ pkg    │  (NADO,
            └────────┬─────────┘       └──────────────────────┘   POLYMARKETPERPS, …)
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
            └────────┬─────────┘   original_symbol OR ccxt_symbol == --symbol)
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
            │  + summary       │  per-FH or per-exchange summary table
            │     tables       │  + global LISTED/INACTIVE/DELISTED/ERROR line
            └──────────────────┘
```

Single-process, library-style modules glued together by a thin CLI entry point.

## 2. Module Breakdown

```
FindExpiredSymbolsInFH/
├── find_expired_symbols.py        # CLI entry point (thin wrapper around fh_symbol_check.cli.main)
├── exchange_mapping.yaml          # DB exchange_name → ccxt exchange id (code-tracked)
├── fh_symbol_check/
│   ├── __init__.py
│   ├── cli.py                     # argparse + orchestration
│   ├── creds.py                   # load creds from YAML by section
│   ├── db.py                      # parameterised query against fh_config (hostname+exchange_name filters)
│   ├── exchange_map.py            # load + lookup exchange_name → ccxt id
│   ├── symbol_translation.py      # per-exchange_name translators (registry)
│   ├── models.py                  # dataclasses: FeedHandlerRow, ResolvedTask, SymbolResult
│   ├── validator.py               # build_tasks, classify_symbols, filter_by_symbol, custom-venue dispatch
│   ├── reporter.py                # text/json/csv renderers + per-FH and per-exchange summary tables
│   ├── logging_config.py          # structured logging setup
│   └── custom_venues/             # non-ccxt venue checkers (registry-driven, see §3.9)
│       ├── __init__.py            #   CUSTOM_VENUES registry + helpers (is_custom_venue, get_checker)
│       ├── nado.py                #   Nado (`https://archive.prod.nado.xyz/v2/symbols`)
│       └── polymarket_perps.py    #   Polymarket Perps (`https://api.perpetuals.polymarket.com/v1/info/instruments`)
├── check_delisted_symbol.py       # EXISTING — refactored (option B, §4.1)
├── mysql_select_query.py          # EXISTING — refactored (option C, §4.2)
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
@dataclass(frozen=True)
class FeedHandlerRow:
    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str          # raw DB value, e.g. "HUOBI"
    symbols: tuple[str, ...]    # parsed from cover_names CSV (whitespace-stripped, empties dropped)

class DBError(Exception): ...

def fetch_feed_handlers(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]: ...
```
- Base SQL (single statement, bound parameters, no string formatting):
  ```sql
  SELECT service_id, fh_name, hostname, exchange_name, cover_names
  FROM crypto_db.fh_config
  ```
- WHERE clauses are appended dynamically as `AND`-combined:
  - `hostname LIKE %s` when `hostname_pattern` is set (bound as `f"%{hostname_pattern}%"`).
  - `UPPER(exchange_name) = UPPER(%s)` when `exchange_name` is set (case-insensitive exact match).
- Both filters `None` ⇒ no WHERE clause (the `--all` path; full-table scan).
- Empty / null `cover_names` → row included with `symbols=()`, logged at DEBUG.
- Connection is opened in a context-manager helper; cursor + connection guaranteed closed on exception.

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
@dataclass(frozen=True)
class FeedHandlerRow:   # also defined in db.py (re-exported here for type imports)
    ...

@dataclass(frozen=True)
class ResolvedTask:
    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str        # raw DB value
    ccxt_id: str              # mapped value, e.g. "htx"
    original_symbol: str      # raw cover_names value, e.g. "1000BONK/USDC-PERP"
    ccxt_symbol: str          # translated value, e.g. "1000BONK/USDC:USDC"

SymbolStatus = Literal["LISTED", "INACTIVE", "DELISTED", "ERROR"]

@dataclass(frozen=True)
class SymbolResult:
    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str
    ccxt_id: str
    original_symbol: str
    ccxt_symbol: str
    status: SymbolStatus
    detail: str = ""
```

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
    symbol: str,
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Keep only tasks/errors whose original_symbol OR ccxt_symbol equals
    ``symbol``. Case-sensitive, full-string equality. Pure function."""

def classify_symbols(
    tasks: Iterable[ResolvedTask],
    *,
    concurrency: int = 4,
) -> list[SymbolResult]: ...
```

- `build_tasks` is pure (no I/O) and routes each row in priority order:
  1. **Custom venue** (`is_custom_venue(row.exchange_name)` is `True`): tasks are tagged with a sentinel `ccxt_id = "custom:<EXCHANGE_NAME>"` (e.g. `custom:NADO`); the exchange map is **not** consulted for these.
  2. **ccxt-backed venue** (`resolve(exchange_map, row.exchange_name)` returns a non-None id): tasks get the resolved ccxt id.
  3. **Unknown**: every symbol on that row is emitted as an `ERROR` result with detail `"unknown exchange_name=<X>; add it to exchange_mapping.yaml"`.
- `filter_by_symbol` is the implementation of `--symbol`. Applied **between** `build_tasks` and `classify_symbols` so the downstream load_markets / custom-venue fetch is only invoked for the surviving tasks.
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

def summary_by_fh(results: list[SymbolResult]) -> list[FeedHandlerSummary]: ...
def summary_by_exchange(results: list[SymbolResult]) -> list[ExchangeSummary]: ...

def keep_fhs_with_errors(results: list[SymbolResult]) -> list[SymbolResult]:
    """Subset to FHs (keyed by (hostname, fh_name, exchange_name)) that have
    at least one ERROR row. All rows of an offending FH are retained."""
```

#### Text mode
- Groups by `(hostname, fh_name, exchange_name)`. Prints only `INACTIVE` / `DELISTED` / `ERROR` rows by default; `--show-listed` (or the `--symbol` auto-enable) adds `LISTED` rows. Includes `original_symbol` and `ccxt_symbol` so operators can grep the DB and the venue.
- Followed by a psql-style summary table:
  - **Default** (`--exchange-grouping=False`): one row per FH (`fh_name | hostname | exchange_name | active | inactive | delisted | error | total dead | total`), with a `TOTAL` footer row.
  - **`--exchange-grouping=True`**: one row per exchange (`exchange_name | feed_handlers | active | inactive | delisted | error | total dead | total`), with a `TOTAL` footer. The per-symbol detail block is suppressed when `suppress_details=True` (i.e. on stdout — the CLI keeps it when `--output-file` is set so files retain the full triage data).
- Ends with the global line `Summary: LISTED=N INACTIVE=N DELISTED=N ERROR=N`.
- Both summary-table renderers go through a shared `_render_psql_table` helper for consistent box-drawing.

#### JSON
- Single array of `SymbolResult` objects (all of them, regardless of `show_listed` / `exchange_grouping`, so machine consumers always see everything). One row per symbol.

#### CSV
- Header + rows; safe for spreadsheets. Same `_CSV_FIELDS` shape as a `SymbolResult` minus the typed status (kept as a plain string).

#### `--errors-only` filtering
- Implemented in `keep_fhs_with_errors`: identifies the set of `(hostname, fh_name, exchange_name)` keys with at least one `ERROR` row and keeps **all** rows of those FHs (so the surviving FH summary remains complete). Applied to the results just before `render()`. Does not change the global summary line or exit code.

### 3.8 `cli.py`
- Parses args; loads creds + exchange map; orchestrates modules.
- Filter args (at least one of the four required):
  - `--hostname <STR>`, `--exchange-name <STR>`, `--symbol <STR>`, `--all`
  - `--all` is mutually exclusive with `--hostname` and `--exchange-name`. `--symbol` composes with any combination of the other three; used alone it implies `--all`.
- Report-shaping args: `--output {text,json,csv}`, `--output-file <PATH>`, `--show-listed`, `--errors-only`, `--exchange-grouping`.
- Behaviour args: `--concurrency <N>`, `--fail-on-invalid` / `--no-fail-on-invalid`, `--log-level`, `-v`/`--verbose`.
- Config args: `--creds-file <PATH>`, `--creds-section <NAME>`, `--exchange-map <PATH>`.
- Pipeline order (after argparse + validation):
  1. `load_creds` → `load_exchange_map` → `fetch_feed_handlers`.
  2. `build_tasks(rows, exchange_map)`.
  3. If `--symbol` is set: `filter_by_symbol(tasks, early_errors, symbol)` — applied here so we don't pay for ccxt load_markets / custom-venue HTTP calls on rows we'd discard.
  4. `classify_symbols(tasks, concurrency)`; results = `early_errors + classified`.
  5. INFO summary log line.
  6. If `--errors-only`: `keep_fhs_with_errors(results)`.
  7. `render(..., show_listed=show_listed or bool(args.symbol), exchange_grouping=…, suppress_details=…)`.
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
| DB | connect / auth / network / SQL error | `DBError`, exit `2` |
| DB | zero matching rows | log WARNING, empty report, exit `0` |
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
- `test_db.py` — stubbed `pymysql.connect`; asserts the SQL uses `%s`, the bound params match the typed filters, and the dynamic-WHERE composition is correct for all four combinations of (`--hostname` set/unset × `--exchange-name` set/unset).
- `test_exchange_map.py` — load mapping, case-insensitive lookup, unknown name returns None, YAML error → `ExchangeMapError`.
- `test_symbol_translation.py` — every registered translator primitive against representative samples (linear / inverse / by-quote / strip-quote / polymarket-remap), plus dispatch tests covering every key in `TRANSLATORS`.
- `test_validator.py` — fake markets dict → LISTED / INACTIVE / DELISTED; simulated `MarketLoadError` → ERROR rows for that exchange only; unmapped `exchange_name` → ERROR row before ccxt is touched; custom-venue dispatch (LISTED/INACTIVE/DELISTED and fetch-failure ERROR); `filter_by_symbol` (both sides, case-sensitivity, exact-not-substring, no mutation, multi-FH survival).
- `test_reporter.py` — text contains all DELISTED rows; json parses; csv header matches; per-FH summary table arithmetic and TOTAL row; per-exchange summary table (`--exchange-grouping`) replaces the per-FH one and aggregates `feed_handlers`; suppress-details on stdout vs file; JSON/CSV unaffected by text-only flags.
- `test_cli.py` — argparse-level acceptance of every permitted flag combination; preservation of the `--all` vs `--hostname`/`--exchange-name` exclusivity rule; widened "at least one of" gate accepting `--symbol`.
- `test_custom_venues_nado.py` — fixture-JSON `_parse_symbols`, network mocking for `fetch_symbols`, happy path / invalid JSON / network errors.
- `test_custom_venues_polymarket_perps.py` — same shape, against the Polymarket Perps instrument-list fixture.

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
