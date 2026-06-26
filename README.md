# FindExpiredSymbolsInFH

For each symbol configured in the Feed Handler database (`crypto_db.fh_config`),
ask the corresponding exchange (via [`ccxt`](https://github.com/ccxt/ccxt))
whether the symbol is still listed and active. Symbols that the exchange has
delisted or marked inactive show up in the report so they can be removed from
the FH configuration.

Filters let you target:

- a single FH host (`--hostname`)
- every FH for a given exchange (`--exchange-name`)
- both at once
- everything in `fh_config` (`--all`)

Output is text (with a per-feed-handler summary table), JSON, or CSV.

---

## Contents

- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration files](#configuration-files)
- [Usage](#usage)
- [Sample output](#sample-output)
- [CLI reference](#cli-reference)
- [Exit codes](#exit-codes)
- [How symbol translation works](#how-symbol-translation-works)
- [Adding a new exchange](#adding-a-new-exchange)
- [Development](#development)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- Python 3.10 – 3.13
- [`pixi`](https://pixi.sh) for dependency / environment management
- Network access to the FH MySQL host listed in `.DBCreds.yaml`
- Outbound network access to each exchange's public REST API

You do **not** need exchange API keys: the tool only calls public
`load_markets()` endpoints.

## Setup

```bash
# 1. Install dependencies into a pixi env
pixi install

# 2. Copy the example creds file and fill it in (see next section)
#    File must be at the repo root and called .DBCreds.yaml.
#    It is git-ignored by default.

# 3. (One-off) make an initial commit so ccxt's vendored toolz can read git
#    metadata at import time — otherwise you'll see a harmless
#    `fatal: bad revision 'HEAD'` line at the top of every run.
git add -A && git commit -m "init"
```

## Configuration files

### `.DBCreds.yaml` (required, git-ignored)

One section per database. The default section name is `crypto_db`, override
with `--creds-section`.

```yaml
crypto_db:
  host: <FH MySQL hostname or IP>
  database: crypto_db
  user: <username>
  password: <password>            # never printed; masked in DBCreds.__repr__
```

Notes:

- The file is matched by `.DBCreds.*` in `.gitignore`; do not remove that rule.
- Connection errors are wrapped in `DBError` and exit with code 2.

### `exchange_mapping.yaml` (tracked in git)

Maps the uppercase `exchange_name` stored in `fh_config` to the lowercase
`ccxt` exchange id. Unknown exchange names appear as `ERROR` rows in the
report. The shipped file currently maps 45 FH exchange names, including
the full Binance family used in the examples below:

```yaml
APEX: apex
ASTER: aster
BACKPACK: backpack
BINANCE: binance
BINANCEDM: binanceusdm
BINANCEDMCOIN: binancecoinm
BITGET: bitget
BITGETDM: bitget
BITHUMB: bithumb
BITSTAMP: bitstamp
BITVAVO: bitvavo
BYBIT: bybit
BYBITDM: bybit
CBITL: coinbaseinternational
COINBASE: coinbase
COINONE: coinone
CRYPTOCOM: cryptocom
CRYPTOCOMDM: cryptocom
DYDXV4: dydx
GATEIO: gate
GATEIODM: gate
GRVT: grvt
HLCASH: hyperliquid
HLFLX: hyperliquid
HLKM: hyperliquid
HLXYZ: hyperliquid
HUOBI: htx
HUOBICOINSWAP: htx
HUOBIDM: htx
HYPERLIQUID: hyperliquid
KRAKEN: kraken
KRAKENDM: krakenfutures
KUCOIN: kucoin
KUCOINDM: kucoinfutures
LIGHTER: lighter
MEXC: mexc
OKEX: okx
PARADEX: paradex
PHEMEX: phemex
PHEMEXDMCOIN: phemex
PHEMEXDMT: phemex
UPBIT: upbit
WHITEBITDM: whitebit
WOO: woo
WOODEX: woofipro
```

---

## Usage

The entry point is `find_expired_symbols.py`. All examples below use
`pixi run python` so they pick up the project environment.

You must supply **at least one** of `--hostname`, `--exchange-name`, or
`--all`. `--all` is mutually exclusive with the other two.

### Example 1 — single host

Report every FH symbol on `TA-TKY-A-41` (substring match against `hostname`):

```bash
pixi run python find_expired_symbols.py --hostname TA-TKY-A-41
```

### Example 2 — every Binance spot FH across all hosts

```bash
pixi run python find_expired_symbols.py --exchange-name BINANCE
```

`--exchange-name` is a case-insensitive **exact** match against
`fh_config.exchange_name`, so `BINANCE` matches `BINANCE` but **not**
`BINANCEDM` or `BINANCEDMCOIN`. If you want all three, run them separately
or use `--all` and filter the report yourself.

### Example 3 — every Binance USDⓈ-M (USDT-margined) FH

```bash
pixi run python find_expired_symbols.py --exchange-name BINANCEDM
```

The tool translates `BTC/USDT-PERP` → `BTC/USDT:USDT` before checking it
against `ccxt.binanceusdm`. See
[How symbol translation works](#how-symbol-translation-works).

### Example 4 — every Binance COIN-M (coin-margined) FH

```bash
pixi run python find_expired_symbols.py --exchange-name BINANCEDMCOIN
```

COIN-M perps are **inverse** contracts, so the tool translates
`BTC/USD-PERP` → `BTC/USD:BTC` (settled in the base) before checking it
against `ccxt.binancecoinm`.

### Example 5 — Binance USDⓈ-M on one specific host

Combine the two filters:

```bash
pixi run python find_expired_symbols.py \
    --hostname TA-TKY-A-41 \
    --exchange-name BINANCEDM
```

### Example 6 — everything, everywhere

Scan every row in `fh_config`. Useful for a full-fleet audit; expect more
exchange API calls and a longer runtime.

```bash
pixi run python find_expired_symbols.py --all
```

### Example 7 — JSON output to a file

```bash
pixi run python find_expired_symbols.py \
    --exchange-name BINANCEDMCOIN \
    --output json \
    --output-file reports/binance-coinm.json
```

### Example 8 — CSV output to a file

```bash
pixi run python find_expired_symbols.py \
    --exchange-name BINANCE \
    --output csv \
    --output-file reports/binance-spot.csv
```

### Example 9 — show every symbol (including LISTED)

By default the text report omits `LISTED` rows so it stays focused on what
needs attention. Pass `--show-listed` to include them, e.g. to audit the full
inventory for a single FH host:

```bash
pixi run python find_expired_symbols.py \
    --hostname TA-TKY-A-41 \
    --exchange-name BINANCEDMCOIN \
    --show-listed
```

### Example 10 — only show feed handlers that have ERROR rows

Triage which feed handlers are misbehaving (unmapped `exchange_name`,
exchange API down, `load_markets` failure, etc.) without the report being
diluted by clean FHs:

```bash
pixi run python find_expired_symbols.py --all --errors-only
```

The filter applies to the report itself (text / JSON / CSV) at the feed
handler granularity — any FH that has at least one `ERROR` row is shown
in full (including its `LISTED` / `INACTIVE` / `DELISTED` rows so its
summary line stays meaningful); FHs with zero errors disappear. The
overall summary log line and the `--fail-on-invalid` exit code are
**not** affected — they still reflect the full run.

### Example 11 — don't fail the process when invalid symbols are found

CI / cron jobs that just want the report regardless of state:

```bash
pixi run python find_expired_symbols.py --all --no-fail-on-invalid
```

Default behaviour is `--fail-on-invalid`: exit 1 if any `DELISTED` or
`INACTIVE` symbol is found, so a non-zero exit is an actionable signal.

### Example 12 — verbose debugging

Logs the SQL filter, every ccxt market load, and which translator was used
per symbol:

```bash
pixi run python find_expired_symbols.py --exchange-name BINANCEDM -v
```

### Example 13 — alternative creds / mapping locations

```bash
pixi run python find_expired_symbols.py \
    --exchange-name BINANCE \
    --creds-file /secrets/fh-prod.yaml \
    --creds-section crypto_db_prod \
    --exchange-map ./mapping-overrides.yaml
```

---

## Sample output

### Text (default)

`fh_binance_*` snapshots from a hypothetical `TA-TKY-A-41` run, with one
delisted symbol mocked up to show the output shape:

```text
[2026-06-26 10:14:01] INFO fh_symbol_check.cli: filter: hostname LIKE %TA-TKY-A-41%
[2026-06-26 10:14:02] INFO fh_symbol_check.validator: loading markets for binance (38 symbols)
[2026-06-26 10:14:04] INFO fh_symbol_check.validator: loading markets for binanceusdm (52 symbols)
[2026-06-26 10:14:06] INFO fh_symbol_check.validator: loading markets for binancecoinm (12 symbols)

Invalid symbols (3):
  fh_binancedm_4007    BINANCEDM      SOMEOLD/USDT-PERP   (ccxt: SOMEOLD/USDT:USDT)   DELISTED   not found in binanceusdm markets
  fh_binancedmcoin_4009 BINANCEDMCOIN OLDCOIN/USD-PERP    (ccxt: OLDCOIN/USD:OLDCOIN) DELISTED   not found in binancecoinm markets
  fh_binance_4006       BINANCE       OLDSPOT/USDT                                    INACTIVE   active=False

Summary by feed handler:
+----------------------+-------------------+---------------+--------+----------+----------+-------+------------+-------+
| fh_name              | hostname          | exchange_name | active | inactive | delisted | error | total dead | total |
+----------------------+-------------------+---------------+--------+----------+----------+-------+------------+-------+
| fh_binance_4006      | TA-TKY-A-41_LOCAL | BINANCE       |     37 |        1 |        0 |     0 |          1 |    38 |
| fh_binancedm_4007    | TA-TKY-A-41_LOCAL | BINANCEDM     |     51 |        0 |        1 |     0 |          1 |    52 |
| fh_binancedmcoin_4009| TA-TKY-A-41_LOCAL | BINANCEDMCOIN |     11 |        0 |        1 |     0 |          1 |    12 |
+----------------------+-------------------+---------------+--------+----------+----------+-------+------------+-------+
| TOTAL                |                   |               |     99 |        1 |        2 |     0 |          3 |   102 |
+----------------------+-------------------+---------------+--------+----------+----------+-------+------------+-------+

[2026-06-26 10:14:09] INFO fh_symbol_check.cli: summary: LISTED=99 INACTIVE=1 DELISTED=2 ERROR=0
```

Columns:

| Column          | Meaning                                                                   |
| --------------- | ------------------------------------------------------------------------- |
| `active`        | `LISTED` symbols (exchange has them and `active != False`)                |
| `inactive`      | Exchange has the symbol but `active=False`                                |
| `delisted`      | Exchange does not have the symbol at all                                  |
| `error`         | Lookup failed (unknown `exchange_name`, network error, etc.)              |
| `total dead`    | `inactive + delisted`                                                     |
| `total`         | Symbols on this FH                                                        |

The invariant `active + inactive + delisted + error == total` always holds.

### JSON

`--output json` emits one object per checked symbol:

```json
[
  {
    "service_id": 4007,
    "fh_name": "fh_binancedm_4007",
    "hostname": "TA-TKY-A-41_LOCAL",
    "exchange_name": "BINANCEDM",
    "ccxt_id": "binanceusdm",
    "internal_symbol": "BTC/USDT-PERP",
    "ccxt_symbol": "BTC/USDT:USDT",
    "status": "LISTED",
    "detail": ""
  },
  {
    "service_id": 4007,
    "fh_name": "fh_binancedm_4007",
    "hostname": "TA-TKY-A-41_LOCAL",
    "exchange_name": "BINANCEDM",
    "ccxt_id": "binanceusdm",
    "internal_symbol": "SOMEOLD/USDT-PERP",
    "ccxt_symbol": "SOMEOLD/USDT:USDT",
    "status": "DELISTED",
    "detail": "not found in binanceusdm markets"
  }
]
```

### CSV

`--output csv` writes the same fields as a header row plus one row per
symbol, suitable for a spreadsheet or `xsv` / `pandas`:

```csv
service_id,fh_name,hostname,exchange_name,ccxt_id,internal_symbol,ccxt_symbol,status,detail
4006,fh_binance_4006,TA-TKY-A-41_LOCAL,BINANCE,binance,BTC/USDT,BTC/USDT,LISTED,
4006,fh_binance_4006,TA-TKY-A-41_LOCAL,BINANCE,binance,OLDSPOT/USDT,OLDSPOT/USDT,INACTIVE,active=False
4007,fh_binancedm_4007,TA-TKY-A-41_LOCAL,BINANCEDM,binanceusdm,BTC/USDT-PERP,BTC/USDT:USDT,LISTED,
```

---

## CLI reference

| Flag                                  | Default                  | Description                                                                              |
| ------------------------------------- | ------------------------ | ---------------------------------------------------------------------------------------- |
| `--hostname <STR>`                    | —                        | Substring matched via `hostname LIKE %<STR>%`                                            |
| `--exchange-name <STR>`               | —                        | Case-insensitive exact match against `fh_config.exchange_name`                           |
| `--all`                               | off                      | Scan every row; mutually exclusive with `--hostname` / `--exchange-name`                 |
| `--output {text,json,csv}`            | `text`                   | Report format                                                                            |
| `--output-file <PATH>`                | stdout                   | Write report here instead of stdout                                                      |
| `--show-listed`                       | off                      | Include `LISTED` rows in the text report                                                 |
| `--errors-only`                       | off                      | Filter report to feed handlers with at least one `ERROR` row (log + exit code unchanged) |
| `--concurrency <N>`                   | `4`                      | Max parallel ccxt exchanges (one `load_markets` per exchange, shared across its symbols) |
| `--fail-on-invalid` / `--no-fail-on-invalid` | `--fail-on-invalid` | Exit 1 when any `DELISTED` / `INACTIVE` row is present                                   |
| `--creds-file <PATH>`                 | `./.DBCreds.yaml`        | Credentials YAML                                                                         |
| `--creds-section <STR>`               | `crypto_db`              | Section name within the creds YAML                                                       |
| `--exchange-map <PATH>`               | `./exchange_mapping.yaml`| Override the FH-name → ccxt-id mapping file                                              |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO`                | Log level                                                                                |
| `-v`, `--verbose`                     | off                      | Shortcut for `--log-level DEBUG`                                                         |

At least one of `--hostname`, `--exchange-name`, or `--all` is required;
omitting all three is rejected by `argparse` (exit 2).

## Exit codes

| Code  | Meaning                                                                       |
| ----- | ----------------------------------------------------------------------------- |
| `0`   | Ran successfully; no invalid symbols (or `--no-fail-on-invalid`)              |
| `1`   | Ran successfully; at least one `DELISTED` / `INACTIVE` symbol was found       |
| `2`   | Operational failure (bad args, creds, DB, exchange map, I/O, unhandled exc.) |
| `130` | Interrupted (`Ctrl-C`)                                                        |

## How symbol translation works

The FH database stores symbols in its own internal convention; `ccxt` uses
its own. For each FH row the tool runs the symbol through a translator
keyed off the FH **`exchange_name`** (not the ccxt id — multiple FH names
can resolve to the same ccxt id while needing different formats):

- `BINANCE` (spot) — identity (e.g. `BTC/USDT` stays as `BTC/USDT`)
- `BINANCEDM` — `BTC/USDT-PERP` → `BTC/USDT:USDT` (linear, settled in quote)
- `BINANCEDMCOIN` — `BTC/USD-PERP` → `BTC/USD:BTC` (inverse, settled in base)

Three `-PERP`-suffix translators cover the various derivative-market
feed handlers:

- **Linear** (`<BASE>/<QUOTE>-PERP` → `<BASE>/<QUOTE>:<QUOTE>`):
  `BINANCEDM`, `CBITL`, `CRYPTOCOMDM`, `GATEIODM`, `HUOBIDM`, `KRAKENDM`,
  `KUCOINDM`, `PHEMEXDMT`, `WHITEBITDM`, `WOO`, `WOODEX`
- **Inverse** (`<BASE>/<QUOTE>-PERP` → `<BASE>/<QUOTE>:<BASE>`):
  `BINANCEDMCOIN`, `HUOBICOINSWAP`, `PHEMEXDMCOIN`
- **By-quote** — picks linear or inverse based on the quote currency
  (`USD` → inverse, anything else → linear):
  `BITGETDM`, `BYBITDM`. Bybit and Bitget each host both linear
  (`BTC/USDT-PERP`) and inverse (`BTC/USD-PERP`) perps under a single FH
  `exchange_name`.

Two of the linear-translator venues use `USD` as both quote and settlement
(`BTC/USD:USD` rather than `BTC/USDT:USDT`):

- `CRYPTOCOMDM` — `cryptocom`'s linear perps are USD-quoted and USD-settled
  (no USDT/USDC variants).
- `KRAKENDM` — `krakenfutures`'s linear perps are USD-quoted and USD-settled.
  The venue also has 14 *inverse* USD-quoted perps (`BTC/USD:BTC`) that
  share the same FH `BTC/USD-PERP` shape; those will surface as `DELISTED`
  with the current translator until/if we learn FH stores them under a
  distinguishable `cover_names` format.

Custom (non-`-PERP`) translators handle venues with their own conventions:

- `NADO` — `<BASE>/<QUOTE>-PERP` → `<BASE>-PERP` (no quote)
- `POLYMARKETPERPS` — `<BASE>/USDC-PERP` → `<BASE>-USD` (with a `WTI`→`WTIOIL` base remap)

Any `exchange_name` without a registered translator falls through to
identity (no transformation) and logs a `DEBUG` line — that's the right
behaviour for spot exchanges (`BINANCE`, `HUOBI`, `KUCOIN`, `GATEIO`,
`PHEMEX`, …) which store symbols in ccxt-native form already.

The report shows both the internal symbol and the translated `ccxt_symbol`
when they differ, so it is obvious what was actually checked.

## Adding a new exchange

1. Add a row to `exchange_mapping.yaml`:

   ```yaml
   NEWEX: ccxtid
   ```

   The key must match `fh_config.exchange_name` exactly (uppercase by
   convention). The value must be a valid `ccxt` exchange id; verify with
   `pixi run python -c "import ccxt; print('ccxtid' in ccxt.exchanges)"`.

2. If the FH stores symbols in a non-ccxt format, register a translator in
   `fh_symbol_check/symbol_translation.py`. The registry is keyed by the
   uppercase FH `exchange_name`, not the ccxt id:

   ```python
   TRANSLATORS["NEWEX"] = _translate_perp_suffix          # linear perps
   TRANSLATORS["NEWEXDMCOIN"] = _translate_perp_suffix_inverse  # inverse
   # or write a new one for an exchange with a different convention
   ```

3. Add a parametrised case to `tests/test_symbol_translation.py`.

4. Run the gates: `pixi run -e dev ruff check . && pixi run -e dev mypy fh_symbol_check && pixi run -e dev pytest -q`.

## Adding a custom (non-ccxt) venue

Some venues (e.g. **NADO**) aren't in `ccxt`, so we hit their public APIs
directly. The framework lives in `fh_symbol_check/custom_venues/`:

1. Create a module, e.g. `fh_symbol_check/custom_venues/newvenue.py`, that
   exposes `fetch_symbols() -> dict[str, bool]` — uppercase venue symbol →
   `is_live`. Raise on any network/JSON/schema failure; the validator catches
   exceptions and emits `ERROR` rows for every task on that venue, leaving
   the rest of the run intact.

2. Register it in `fh_symbol_check/custom_venues/__init__.py`:

   ```python
   from . import newvenue
   CUSTOM_VENUES["NEWVENUE"] = newvenue.fetch_symbols
   ```

   The key must match `fh_config.exchange_name` (uppercase). Custom-venue
   routing happens **before** the `exchange_mapping.yaml` lookup, so you do
   **not** need a YAML entry for these.

3. Tests should exercise the parser against fixture JSON (no network); see
   `tests/test_custom_venues_nado.py` for the pattern.

### Shipped custom venues

| FH `exchange_name` | Source                                                                  | Symbol translation                                                                       | Status mapping                                                                                                                                              |
| ------------------ | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NADO`             | `GET https://archive.prod.nado.xyz/v2/symbols`                          | `<BASE>/<QUOTE>-PERP` → `<BASE>-PERP`                                                    | `trading_status == "live"` → LISTED; `post_only` / `reduce_only` / `soft_reduce_only` / `not_tradable` → INACTIVE; symbol not in response → DELISTED |
| `POLYMARKETPERPS`  | `GET https://api.perpetuals.polymarket.com/v1/info/instruments`         | `<BASE>/USDC-PERP` → `<BASE>-USD` (with `WTI`→`WTIOIL` base remap)                       | Present → LISTED; absent → DELISTED. Endpoint exposes no active flag, so no INACTIVE state.                                                                  |

In `SymbolResult` output, custom-venue rows carry `ccxt_id="custom:<NAME>"`
(e.g. `custom:NADO`) so downstream consumers can tell where the
classification came from.

## Development

```bash
# Install runtime + dev deps (pytest, mypy, ruff, type stubs)
pixi install -e dev

# Quality gates
pixi run -e dev ruff check .
pixi run -e dev mypy fh_symbol_check
pixi run -e dev pytest -q
```

`.vscode/launch.json` includes ready-to-use debug configurations for the
common filter modes.

## Project layout

```
.
├── find_expired_symbols.py     # thin CLI entry point
├── fh_symbol_check/            # package with the actual logic
│   ├── cli.py                  # argparse + orchestration
│   ├── creds.py                # .DBCreds.yaml loader + DBCreds dataclass
│   ├── db.py                   # fetch_feed_handlers(...)
│   ├── exchange_map.py         # exchange_mapping.yaml loader + resolve()
│   ├── symbol_translation.py   # per-FH-exchange-name translators
│   ├── validator.py            # build_tasks + classify_symbols
│   ├── reporter.py             # text/json/csv rendering + summary table
│   ├── logging_config.py
│   ├── models.py               # dataclasses (FeedHandlerRow, SymbolResult, ...)
│   └── custom_venues/          # non-ccxt venue checkers (NADO, POLYMARKETPERPS, …)
│       ├── __init__.py         # CUSTOM_VENUES registry + sentinel helpers
│       ├── nado.py             # archive.prod.nado.xyz /v2/symbols client
│       └── polymarket_perps.py # api.perpetuals.polymarket.com /v1/info/instruments client
├── tests/                      # pytest suite
├── exchange_mapping.yaml       # tracked
├── .DBCreds.yaml               # git-ignored (you create this)
├── check_delisted_symbol.py    # reusable helper; also has its own CLI
├── mysql_select_query.py       # reusable MySQL client
├── requirements.md
├── design.md
├── implementation.md
└── task.md
```

## Troubleshooting

| Symptom                                                              | Likely cause / fix                                                                                                              |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `fatal: bad revision 'HEAD'` printed once at the top of every run    | `ccxt` imports a vendored `toolz` that probes git at import time. Make an initial commit (`git add -A && git commit -m init`).  |
| Many `ERROR` rows for a single exchange                              | Either the `exchange_name` is missing from `exchange_mapping.yaml`, or the exchange's REST API is rate-limiting / unreachable.  |
| Symbols you expect to be valid show as `DELISTED`                    | Almost always a symbol-translation mismatch — re-run with `-v` and compare `internal_symbol` vs `ccxt_symbol` in the report.    |
| `creds error: section 'crypto_db' not found`                         | Wrong `--creds-section` or your YAML is missing that section.                                                                   |
| Exit code 2 with `specify --hostname, --exchange-name, or --all`     | You ran the tool with no filter. Pick one; `--all` is the explicit "scan everything" option.                                    |
