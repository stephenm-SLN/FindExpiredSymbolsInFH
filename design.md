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
                     │                       WHERE hostname LIKE %s
                     │
                     ▼
            ┌──────────────────┐       ┌──────────────────────┐
            │ exchange mapper  │──────►│ exchange_mapping.yaml │  (HUOBI→htx, WOODEX→woo, …)
            └────────┬─────────┘       └──────────────────────┘
                     │
                     ▼
            ┌──────────────────┐
            │ symbol translator│  per-ccxt-id rules (default = identity)
            └────────┬─────────┘
                     │  tasks: [(service_id, fh_name, ccxt_id, ccxt_symbol, original_symbol), …]
                     ▼
            ┌──────────────────┐       ┌─────────────────────┐
            │ ccxt validator   │──────►│ exchange metadata   │  (cached per ccxt_id, in-process)
            └────────┬─────────┘       └─────────────────────┘
                     │  results: [SymbolResult, …]
                     ▼
            ┌──────────────────┐
            │ reporter         │  text | json | csv → stdout / file
            └──────────────────┘
```

Single-process, library-style modules glued together by a thin CLI entry point.

## 2. Module Breakdown

```
FindExpiredSymbolsInFH/
├── find_expired_symbols.py        # CLI entry point (thin wrapper around fh_symbol_check.cli.main)
├── exchange_mapping.yaml          # NEW: DB exchange_name → ccxt exchange id (code-tracked)
├── fh_symbol_check/
│   ├── __init__.py
│   ├── cli.py                     # argparse + orchestration
│   ├── creds.py                   # load creds from YAML by section
│   ├── db.py                      # parameterised query against fh_config
│   ├── exchange_map.py            # load + lookup exchange_name → ccxt id
│   ├── symbol_translation.py      # per-ccxt-id translators (registry)
│   ├── models.py                  # dataclasses: FeedHandlerRow, ResolvedTask, SymbolResult
│   ├── validator.py               # wraps the refactored check_delisted_symbol.py
│   ├── reporter.py                # text/json/csv renderers
│   └── logging_config.py          # structured logging setup
├── check_delisted_symbol.py       # EXISTING — refactor approved (option B, design §4.1)
├── mysql_select_query.py          # EXISTING — refactor approved (option C, design §4.2)
└── .vscode/launch.json            # NEW: VS Code debug config
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

def fetch_feed_handlers(creds: DBCreds, hostname_pattern: str) -> list[FeedHandlerRow]: ...
```
- Locked SQL (single statement, bound parameter, no string formatting):
  ```sql
  SELECT service_id, fh_name, hostname, exchange_name, cover_names
  FROM crypto_db.fh_config
  WHERE hostname LIKE %s
  ```
  Executed as `cursor.execute(sql, (f"%{hostname_pattern}%",))`.
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
- Unknown `exchange_name` → `resolve` returns `None`; the validator surfaces it as an `ERROR` row with `detail="unknown exchange_name=<X>"`. The run continues.

### 3.4 `symbol_translation.py`
```python
Translator = Callable[[str], str]

# Registry — populated at module import time
TRANSLATORS: dict[str, Translator] = {
    "woo": _translate_woo,
    # default for missing entries = identity
}

def translate(ccxt_id: str, internal_symbol: str) -> str: ...
```
- Initial rules (subject to your sign-off):
  - `htx`: identity. `BTC/USDT` is already ccxt spot syntax.
  - `woo`: strip `-PERP` suffix, append `:<QUOTE>`. Example: `1000BONK/USDC-PERP` → `1000BONK/USDC:USDC`.
- Missing rule → identity. Logged at INFO once per ccxt id encountered, not per symbol.
- Pure functions, trivially unit-testable.

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
    """Returns (tasks_ready_for_ccxt, error_results_for_unmappable_rows)."""

def classify_symbols(
    tasks: Iterable[ResolvedTask],
    *,
    concurrency: int = 4,
) -> list[SymbolResult]: ...
```
- `build_tasks` is pure (no I/O) — applies exchange mapping + per-exchange symbol translation. Rows whose `exchange_name` is unknown produce `ERROR` results immediately; everything else becomes a `ResolvedTask`.
- `classify_symbols` groups tasks by `ccxt_id`, calls `load_exchange_markets_safe()` (from refactored `check_delisted_symbol.py`) **once per ccxt id**, then calls `classify(markets, ccxt_symbol)` per task.
- Errors caught **per exchange**: any `ccxt.NetworkError`, `ccxt.ExchangeError`, `MarketLoadError`, or unsupported-exchange condition produces `ERROR` rows for every task on that exchange. The run continues for other exchanges.
- Optional bounded concurrency across exchanges via `concurrent.futures.ThreadPoolExecutor` (ccxt sync I/O is GIL-friendly during network waits). Default `concurrency=4`.

### 3.7 `reporter.py`
```python
def render(
    results: list[SymbolResult],
    fmt: Literal["text", "json", "csv"],
    stream: TextIO,
    *,
    show_listed: bool = False,
) -> None: ...

def summary(results: list[SymbolResult]) -> dict[SymbolStatus, int]: ...
```
- Text: groups by `(hostname, fh_name, exchange_name)`, prints only `INACTIVE` / `DELISTED` / `ERROR` rows by default. Includes `original_symbol` so operators can grep the DB. Final summary line `LISTED=N INACTIVE=N DELISTED=N ERROR=N`.
- JSON: single array of `SymbolResult` objects (all of them, regardless of `show_listed`, so machine consumers always see everything).
- CSV: header + rows; safe for spreadsheets.

### 3.8 `cli.py`
- Parses args; loads creds + exchange map; orchestrates modules.
- Exit codes:
  - operational failure (creds / DB / args / I/O) → `2`
  - any `DELISTED` or `INACTIVE` symbols and `--fail-on-invalid` (default) → `1`
  - `ERROR` rows alone do **not** force exit 1 — they signal "couldn't determine", not "delisted".
  - else → `0`
  - `KeyboardInterrupt` → `130`

## 4. Integration with the Two Helper Scripts (refactors approved)

Per `requirements.md` §8.7, option (b) is approved. The diffs are minimal and preserve existing CLI behaviour. I will still post the diffs in chat for sign-off before applying them.

### 4.1 `check_delisted_symbol.py` — proposed minimal refactor

Extract two pure-library entry points; keep existing functions as thin wrappers; `sys.exit` calls move into `main()` only.

```python
class MarketLoadError(Exception): ...

def load_exchange_markets_safe(exchange_id: str) -> tuple[ccxt.Exchange, dict]:
    """Raises MarketLoadError instead of sys.exit. Pure library function."""

def classify(markets: dict, symbol: str) -> tuple[Literal["LISTED","INACTIVE","DELISTED"], str]:
    """Return (status, detail). Pure function; no I/O, no prints."""
```

`check_symbol`, `check_multiple`, and the existing CLI behave bit-for-bit the same as today.

### 4.2 `mysql_select_query.py` — proposed minimal refactor

- Remove credential defaults from `__init__` (callers must pass them explicitly — prevents accidental connections with embedded creds).
- Add an optional `params` argument to `fetch_query_results(self, query, params=None, return_header=False)` so callers can use `%s` placeholders.
- The `if __name__ == "__main__":` block is updated to read creds from `.DBCreds.yaml` (section `crypto_db`) so its behaviour stays useful without baked-in creds.

## 5. Error Handling

| Layer | Failure | Behaviour |
|---|---|---|
| CLI | bad args | argparse prints help, exit `2` |
| Creds | file missing / unreadable / malformed / missing section | `CredsError`, log without values, exit `2` |
| DB | connect / auth / network / SQL error | `DBError`, exit `2` |
| DB | zero matching rows | log WARNING, empty report, exit `0` |
| Exchange map | file missing / malformed | `ExchangeMapError`, exit `2` |
| Exchange map | unknown `exchange_name` | per-row `ERROR` result, run continues |
| Translator | no rule for ccxt id | identity, log INFO once per ccxt id |
| Validator | per-exchange `ccxt.NetworkError` / `ExchangeError` / `MarketLoadError` | all that exchange's tasks → `ERROR` rows, run continues |
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

Unit tests live in `tests/`. No live DB or ccxt network calls.

- `test_creds.py` — load by section, missing file, missing section, malformed YAML, repr masks password.
- `test_db.py` — stubbed `pymysql.connect`; assert the SQL uses `%s` and the bound param equals `%<hostname>%`.
- `test_exchange_map.py` — load mapping, case-insensitive lookup, unknown name returns None.
- `test_symbol_translation.py` — `woo` rule: `1000BONK/USDC-PERP` → `1000BONK/USDC:USDC`; identity for unknown ccxt id.
- `test_validator.py` — fake markets dict → LISTED / INACTIVE / DELISTED; simulated `MarketLoadError` → ERROR rows for that exchange only; unmapped exchange_name → ERROR row before ccxt is touched.
- `test_reporter.py` — text contains all DELISTED rows; json parses; csv header matches.

Manual smoke test (documented in `task.md`) against the real host.

## 9. Suggested Improvements (proactive — for your decision)

These are suggestions, **not** committed changes.

1. **`mysql_select_query.py`** future-proofing (beyond the agreed refactor):
   - Use `with` context-manager pattern (`__enter__`/`__exit__`) so connections are guaranteed closed even on exception.
   - Add `connect_timeout`/`read_timeout` to `pymysql.connect`.
2. **`check_delisted_symbol.py`** future-proofing (beyond the agreed refactor):
   - Cache `load_markets()` results in-process so multiple calls with the same `exchange_id` don't re-fetch (the new validator does this at its layer, so this is duplication of concern — only worth doing if the helper is reused elsewhere).
   - Add an option to enable `exchange.enableRateLimit = True`.
   - Replace some prints with `logging` so library callers can route output.
3. **Symbol translation**: a `reverse_translate` for cases where the report should show both formats side-by-side (already covered — `SymbolResult` keeps both `original_symbol` and `ccxt_symbol`).
4. **Output**: ANSI colours for `text` mode when stdout is a TTY (red for `DELISTED`/`INACTIVE`).
5. **Type checking + lint**: `mypy --strict` and `ruff` over the new package.
6. **Pre-commit hook**: block commits that contain the literal password from `.DBCreds.yaml`.
7. **Schema drift guard**: at startup, `DESCRIBE crypto_db.fh_config` and assert the expected columns exist; print a clear error if the schema changes.

## 10. Risks

- ccxt rate-limits or geo-blocks (e.g. HTX in some regions) → mitigated by per-exchange `ERROR` rows.
- DB schema drift (e.g. column rename) → caught at query time as a `DBError`; optional schema-drift guard in §9.7.
- Internal symbol formats drift (new suffix, new exchange) → caught as unknown translation; identity is applied and the symbol most likely shows up as `DELISTED`, which is a recoverable false positive (operator inspects, adds a translator rule).
- Unknown `exchange_name` from a newly added feed handler → shows up as `ERROR` rows, easy to spot in the report; fix is one line in `exchange_mapping.yaml`.
