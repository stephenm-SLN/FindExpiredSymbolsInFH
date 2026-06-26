# Implementation Plan — Feed Handler Symbol Validity Checker

Concrete "how" — files, signatures, libraries, CLI shape, exit codes. Assumes `requirements.md` §8 decisions are locked and `design.md` is signed off.

## 1. Files to Create / Modify

### Create
| Path | Purpose |
|---|---|
| `find_expired_symbols.py` | Thin CLI entry point (`from fh_symbol_check.cli import main; sys.exit(main())`). |
| `exchange_mapping.yaml` | DB `exchange_name` → ccxt id. Code-tracked. Seed: `HUOBI: htx`, `WOODEX: woo`. |
| `fh_symbol_check/__init__.py` | Empty package marker. |
| `fh_symbol_check/cli.py` | Argparse + orchestration + exit code. |
| `fh_symbol_check/creds.py` | `load_creds(path, section)`, `DBCreds`, `CredsError`. |
| `fh_symbol_check/db.py` | `fetch_feed_handlers(creds, hostname_pattern)`, `FeedHandlerRow`, `DBError`. |
| `fh_symbol_check/exchange_map.py` | `load_exchange_map(path)`, `resolve(mapping, exchange_name)`, `ExchangeMapError`. |
| `fh_symbol_check/symbol_translation.py` | Per-ccxt-id `TRANSLATORS` registry + `translate(ccxt_id, symbol)`. |
| `fh_symbol_check/models.py` | `ResolvedTask`, `SymbolResult`, `SymbolStatus`. |
| `fh_symbol_check/validator.py` | `build_tasks(rows, mapping)`, `classify_symbols(tasks, concurrency)`. |
| `fh_symbol_check/reporter.py` | `render(results, fmt, stream, show_listed)`, `summary(results)`. |
| `fh_symbol_check/logging_config.py` | `setup_logging(level)`. |
| `tests/__init__.py` | Empty. |
| `tests/test_creds.py` | YAML load by section; password-mask in repr. |
| `tests/test_db.py` | Parameterised SQL assertions (cursor stubbed). |
| `tests/test_exchange_map.py` | Load + case-insensitive lookup. |
| `tests/test_symbol_translation.py` | `woo` rule + identity default. |
| `tests/test_validator.py` | LISTED / INACTIVE / DELISTED / ERROR classification. |
| `tests/test_reporter.py` | text / json / csv shapes. |
| `.vscode/launch.json` | Python debug config with `--hostname TA-TKY-A-41` example. |
| `README.md` (short) | Install (pixi), run, creds & mapping location, exit codes. |

### Modify (each via a diff I will post in chat for sign-off first)
| Path | Change | Rationale |
|---|---|---|
| `.gitignore` | Append `.DBCreds.*`, `__pycache__/`, `*.pyc`, `.pytest_cache/`. Confirm `exchange_mapping.yaml` is NOT ignored. | Prevent committing secrets and noise. **High priority — do first.** |
| `pixi.toml` | Add deps `pymysql`, `ccxt`, `pyyaml`. (Dev deps `pytest` mandatory; `mypy`/`ruff` optional — confirm.) | Tool needs these. |
| `mysql_select_query.py` | Design §4.2: drop hard-coded credential defaults, add `params=None` to `fetch_query_results`, migrate `__main__` to read from `.DBCreds.yaml` (`crypto_db` section). | Safety + parameterised queries. |
| `check_delisted_symbol.py` | Design §4.1: extract pure `classify(markets, symbol)` and `load_exchange_markets_safe(exchange_id)` raising `MarketLoadError`; keep existing CLI behaviour unchanged. | Library reuse without `sys.exit` hazards. |

## 2. CLI Interface

```
$ python find_expired_symbols.py --help

usage: find_expired_symbols.py --hostname HOSTNAME [options]

Required:
  --hostname HOSTNAME           Hostname substring for the SQL LIKE filter
                                (e.g. TA-TKY-A-41). No default.

Output:
  --output {text,json,csv}      Report format (default: text).
  --output-file PATH            Write report to PATH instead of stdout.
  --show-listed                 Include LISTED rows in text output (default: omit).

Behaviour:
  --concurrency N               Max parallel exchanges (default: 4).
  --fail-on-invalid             Exit 1 when any DELISTED/INACTIVE found (default).
  --no-fail-on-invalid          Always exit 0 unless an operational error occurs.

Config:
  --creds-file PATH             Credentials YAML (default: ./.DBCreds.yaml).
  --creds-section NAME          Section in the creds YAML (default: crypto_db).
  --exchange-map PATH           Exchange-name → ccxt-id map (default: ./exchange_mapping.yaml).

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

def fetch_feed_handlers(creds: DBCreds, hostname_pattern: str) -> list[FeedHandlerRow]: ...
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
TRANSLATORS: dict[str, Translator] = {"woo": _translate_woo}

def translate(ccxt_id: str, internal_symbol: str) -> str: ...
def _translate_woo(s: str) -> str: ...   # "1000BONK/USDC-PERP" -> "1000BONK/USDC:USDC"
```

```python
# fh_symbol_check/validator.py
def build_tasks(
    rows: Iterable[FeedHandlerRow],
    exchange_map: dict[str, str],
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    ...

def classify_symbols(
    tasks: Iterable[ResolvedTask],
    *,
    concurrency: int = 4,
) -> list[SymbolResult]: ...
```

```python
# fh_symbol_check/reporter.py
def render(
    results: list[SymbolResult],
    fmt: Literal["text", "json", "csv"],
    stream: TextIO,
    *,
    show_listed: bool = False,
) -> None: ...

def summary(results: list[SymbolResult]) -> dict[SymbolStatus, int]: ...
```

```python
# fh_symbol_check/cli.py
def main(argv: list[str] | None = None) -> int: ...
```

## 5. Helper-Script Refactors (approved — diffs go through review first)

### 5.1 `check_delisted_symbol.py`
Add (above existing functions):
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
Update existing functions:
- `load_exchange_markets` → call `load_exchange_markets_safe`, catch `MarketLoadError`, print + `sys.exit(1)` (CLI behaviour preserved).
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
- Replace the `__main__` block to read `.DBCreds.yaml` (section `crypto_db`) — no hard-coded creds remain anywhere in the helper.

I will post both diffs in chat for sign-off before applying.

## 6. Libraries

| Library | Why | Source |
|---|---|---|
| `pymysql` | Already used by helper; supports parameterised queries. | conda-forge / pypi |
| `ccxt` | Used by helper; authoritative for "is symbol listed". | pypi |
| `pyyaml` | YAML creds + exchange map. | conda-forge / pypi |
| `pytest` | Tests. | dev only |
| `mypy` (optional) | Type-check new package. | dev only |
| `ruff` (optional) | Lint new package. | dev only |

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
| 1 | Tool runs end-to-end | `python find_expired_symbols.py --hostname TA-TKY-A-41` |
| 2 | JSON is valid | `python find_expired_symbols.py --hostname TA-TKY-A-41 --output json --no-fail-on-invalid \| python -m json.tool >/dev/null` |
| 3 | CSV header correct | `python find_expired_symbols.py --hostname TA-TKY-A-41 --output csv --no-fail-on-invalid \| head -n1` |
| 4 | Empty result | `python find_expired_symbols.py --hostname does-not-exist`  → exit 0, empty report |
| 5 | Bad creds | rename creds file → exit 2, no password in stderr |
| 6 | No literal creds in new code | `rg -n 'ToTheMoon\|*%r4#j9z' fh_symbol_check find_expired_symbols.py` → no matches |
| 7 | Parameterised SQL | `rg -n "f\"SELECT" fh_symbol_check/db.py` → no matches; `cursor.execute(sql, (param,))` present |
| 8 | Creds git-ignored | `git check-ignore .DBCreds.yaml` → ignored |
| 9 | Mapping NOT git-ignored | `git check-ignore exchange_mapping.yaml` → empty (i.e. tracked) |

## 10. Out-of-Scope Reminders
- No persistent caching across runs.
- No FH config mutation.
- No remote scheduling.
- No auto-detection of translation rules — explicitly registered per ccxt id.
