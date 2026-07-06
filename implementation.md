# Implementation Plan — Feed Handler Symbol Validity Checker

Concrete "how" — files, signatures, libraries, CLI shape, exit codes. Assumes `requirements.md` §8 decisions are locked and `design.md` is signed off.

## 1. Files to Create / Modify

### Create (all done)
| Path | Purpose |
|---|---|
| `find_expired_symbols.py` | Thin CLI entry point (`from fh_symbol_check.cli import main; sys.exit(main())`). |
| `exchange_mapping.yaml` | DB `exchange_name` → ccxt id. Code-tracked. See file for the full mapping. |
| `fh_symbol_check/__init__.py` | Empty package marker. |
| `fh_symbol_check/cli.py` | Argparse + orchestration + exit code; filter / report / behaviour / config arg groups. |
| `fh_symbol_check/creds.py` | `load_creds(path, section)`, `DBCreds`, `CredsError`. |
| `fh_symbol_check/db.py` | `fetch_feed_handlers(creds, hostname_pattern=None, *, exchange_name=None)`, `FeedHandlerRow`, `DBError`. |
| `fh_symbol_check/exchange_map.py` | `load_exchange_map(path)`, `resolve(mapping, exchange_name)`, `ExchangeMapError`. |
| `fh_symbol_check/symbol_translation.py` | Per-`exchange_name` `TRANSLATORS` registry + `translate(exchange_name, symbol)` + the `_translate_*` primitives. |
| `fh_symbol_check/models.py` | `FeedHandlerRow`, `ResolvedTask`, `SymbolResult`, `SymbolStatus`. |
| `fh_symbol_check/validator.py` | `build_tasks`, `filter_by_symbol`, `classify_symbols` (with custom-venue dispatch via `_classify_custom_venue`). |
| `fh_symbol_check/reporter.py` | `render`, `summary`, `summary_by_fh`, `summary_by_exchange`, `keep_fhs_with_errors`, `FeedHandlerSummary`, `ExchangeSummary`. |
| `fh_symbol_check/logging_config.py` | `setup_logging(level)`. |
| `fh_symbol_check/custom_venues/__init__.py` | `CUSTOM_VENUES` registry, `is_custom_venue`, `custom_id_for`, `get_checker`, `CUSTOM_VENUE_PREFIX`. |
| `fh_symbol_check/custom_venues/nado.py` | Nado checker (`fetch_symbols()`), `User-Agent`-spoofed `urllib`. |
| `fh_symbol_check/custom_venues/polymarket_perps.py` | Polymarket Perps checker (`fetch_symbols()`), same shape. |
| `tests/__init__.py` | Empty. |
| `tests/conftest.py` | Ensure workspace root is on `sys.path`. |
| `tests/test_creds.py` | YAML load by section; password-mask in repr; password absent from `caplog.text`. |
| `tests/test_db.py` | Stubbed `pymysql.connect`; dynamic-WHERE composition for all combinations of `hostname` / `exchange_name`. |
| `tests/test_exchange_map.py` | Load + case-insensitive lookup; YAML errors → `ExchangeMapError`. |
| `tests/test_symbol_translation.py` | Every registered translator primitive + dispatch tests for every key in `TRANSLATORS`. |
| `tests/test_validator.py` | LISTED / INACTIVE / DELISTED / ERROR classification (ccxt path); custom-venue dispatch; `filter_by_symbol` exhaustive cases. |
| `tests/test_reporter.py` | text / json / csv shapes; per-FH and per-exchange summary tables; `--errors-only` filter; suppress-details behaviour; JSON/CSV invariance under text-only flags. |
| `tests/test_cli.py` | Argparse-level flag acceptance + the validation gates (`--all` exclusivity, "at least one of" requirement). |
| `tests/test_custom_venues_nado.py` | Nado parser + `fetch_symbols` mocking. |
| `tests/test_custom_venues_polymarket_perps.py` | Polymarket Perps parser + `fetch_symbols` mocking. |
| `.vscode/launch.json` | Python debug configs. |
| `README.md` | User-facing docs: install (pixi), run, creds & mapping location, CLI reference, examples, exit codes. |

### Modify (each via a diff I will post in chat for sign-off first)
| Path | Change | Rationale |
|---|---|---|
| `.gitignore` | Append `.DBCreds.*`, `__pycache__/`, `*.pyc`, `.pytest_cache/`. Confirm `exchange_mapping.yaml` is NOT ignored. | Prevent committing secrets and noise. **High priority — do first.** |
| `pixi.toml` | Add deps `pymysql`, `ccxt`, `pyyaml`. (Dev deps `pytest` mandatory; `mypy`/`ruff` optional — confirm.) | Tool needs these. |
| `mysql_select_query.py` | Design §4.2: drop hard-coded credential defaults, add `params=None` to `fetch_query_results`, migrate `__main__` to read from `.DBCreds.yaml` (`crypto_db` section). | Safety + parameterised queries. |
| `check_delisted_symbol.py` | Design §4.1: extract pure `classify(markets, symbol)` and `load_exchange_markets_safe(exchange_id)` raising `MarketLoadError`; keep existing CLI behaviour unchanged. | Library reuse without `sys.exit` hazards. |

## 2. CLI Interface

```
$ pixi run python find_expired_symbols.py --help

usage: find_expired_symbols.py [options]

Filter args (at least one is required):
  --hostname STRING             Hostname substring for the SQL LIKE filter
                                (e.g. TA-TKY-A-41). Bound as a parameter.
  --exchange-name STRING        Case-insensitive exact match against
                                fh_config.exchange_name (e.g. BINANCE).
  --symbol STRING               Find every occurrence of this exact symbol.
                                Case-sensitive; matched against both the FH
                                form (cover_names) and the translated venue
                                form. Used alone implies --all.
  --all                         Scan every fh_config row (no filters).
                                Mutually exclusive with --hostname and
                                --exchange-name.

Report-shaping args:
  --output {text,json,csv}      Report format (default: text).
  --output-file PATH            Write report to PATH instead of stdout.
  --show-listed                 Include LISTED rows in text output. Auto-
                                enabled when --symbol is set.
  --errors-only                 Filter the report to feed handlers that
                                have at least one ERROR row. Does not
                                change the summary log line or exit code.
  --exchange-grouping           Text only: replace the per-FH summary
                                table with a per-exchange one; suppress
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
  --exchange-map PATH           Exchange-name → ccxt-id map (default:
                                ./exchange_mapping.yaml).

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

@dataclass(frozen=True)
class ResolvedTask:
    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str
    ccxt_id: str
    original_symbol: str
    ccxt_symbol: str

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
@dataclass(frozen=True)
class FeedHandlerRow:
    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str
    symbols: tuple[str, ...]

class DBError(Exception): ...

def fetch_feed_handlers(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]: ...
```

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
    symbol: str,
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Keep only tasks/errors where original_symbol OR ccxt_symbol == symbol.
    Case-sensitive, full-string equality."""

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
    feed_handlers: int   # distinct (hostname, fh_name) pairs
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
def summary_by_fh(results: list[SymbolResult]) -> list[FeedHandlerSummary]: ...
def summary_by_exchange(results: list[SymbolResult]) -> list[ExchangeSummary]: ...
def keep_fhs_with_errors(results: list[SymbolResult]) -> list[SymbolResult]: ...
```

```python
# fh_symbol_check/cli.py
def main(argv: list[str] | None = None) -> int: ...
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
| `urllib` (stdlib) | HTTP for custom-venue checkers (Nado, Polymarket Perps). | stdlib |
| `pytest` | Tests. | dev only |
| `mypy` | Type-check new package; gate. | dev only |
| `ruff` | Lint new package; gate. | dev only |
| `types-PyYAML`, `types-PyMySQL` | mypy stubs. | dev only |

Compatible-release ranges (`pymysql>=1.1`, `ccxt>=4`, `pyyaml>=6`). No exact pins unless `pixi.lock` forces it.

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
| 4 | `--symbol` finds every occurrence (implies `--all`) | `pixi run python find_expired_symbols.py --symbol IP/USDT-PERP` |
| 5 | `--exchange-grouping` swaps the summary table | `pixi run python find_expired_symbols.py --all --exchange-grouping` |
| 6 | JSON is valid | `pixi run python find_expired_symbols.py --all --output json --no-fail-on-invalid \| python -m json.tool >/dev/null` |
| 7 | CSV header correct | `pixi run python find_expired_symbols.py --all --output csv --no-fail-on-invalid \| head -n1` |
| 8 | Empty result | `pixi run python find_expired_symbols.py --hostname does-not-exist` → exit 0, empty report |
| 9 | Bad creds | rename creds file → exit 2, no password in stderr |
| 10 | No literal creds in new code | `rg -n '<password literal>' fh_symbol_check find_expired_symbols.py` → no matches |
| 11 | Parameterised SQL | `rg -n "f\"SELECT" fh_symbol_check/db.py` → no matches; `cursor.execute(sql, (param,))` present |
| 12 | Creds git-ignored | `git check-ignore .DBCreds.yaml` → ignored |
| 13 | Mapping NOT git-ignored | `git check-ignore exchange_mapping.yaml` → empty (i.e. tracked) |
| 14 | Gates clean | `pixi run -e dev ruff check . && pixi run -e dev mypy fh_symbol_check && pixi run -e dev pytest -q` |

## 10. Out-of-Scope Reminders
- No persistent caching across runs.
- No FH config mutation.
- No remote scheduling.
- No auto-detection of translation rules — explicitly registered per FH `exchange_name`.
- No auto-discovery of custom-venue endpoints — each is hand-written in `custom_venues/<venue>.py` against the venue's documented API.
