# FindExpiredSymbolsInFH

For each symbol configured by any producer in the Feed Handler database — the
feed handlers themselves (`crypto_db.fh_config`) and the repeaters
(`crypto_db.repeater_feeds`) — ask the corresponding exchange (via
[`ccxt`](https://github.com/ccxt/ccxt)) whether the symbol is still listed
and active. Symbols that the exchange has delisted or marked inactive show
up in the report so they can be removed from the producer configuration.

Filters let you target:

- a single producer host (`--hostname`)
- every producer for a given exchange (`--exchange-name`)
- both at once
- everything in the selected producer tables (`--all`)
- one or more exact symbols anywhere across the fleet (`--symbol`)

By default the tool scans **both** producer tables (feed handlers and
repeaters). Use `--source {fh, rp, both}` to narrow the scan.

Output is text (with per-source summary tables — one for feed handlers, one
for repeaters — or a single per-`(exchange, source)` table under
`--exchange-grouping`), JSON, or CSV. JSON/CSV rows carry a `source` field
so downstream consumers can split feed-handler and repeater rows.

The same scanning logic is also exposed as a **REST API + HTMX browser UI**
via the `find-expired-symbols-service` console script — same wheel, same
DB creds, same exchange mapping. The API is async-poll (`POST /scans`
returns a job ID, `GET /scans/{id}` returns state/results); the UI wraps
it with a filter form, a live-updating scan card, and a filterable result
table. See [Running the API service](#running-the-api-service).

---

## Contents

- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration files](#configuration-files)
- [Usage](#usage)
- [Sample output](#sample-output)
- [CLI reference](#cli-reference)
- [Exit codes](#exit-codes)
- [How statuses are determined](#how-statuses-are-determined)
- [How symbol translation works](#how-symbol-translation-works)
- [Adding a new exchange](#adding-a-new-exchange)
- [Running the API service](#running-the-api-service)
- [Deploying on a Linux server](#deploying-on-a-linux-server)
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

If your outbound HTTPS goes through a corporate SSL-inspection proxy
(Zscaler, Palo Alto, Netskope, …) the tool uses
[`truststore`](https://truststore.readthedocs.io) at startup so Python
honours the **OS-native** trust store (macOS Keychain / Linux system CA
bundle / Windows cert store) instead of the Mozilla-only bundle bundled
with conda-forge Python. As long as IT has already installed the corp
root CA system-wide (the standard on any managed laptop or server),
nothing more is needed. If injection can't run for some reason it's
downgraded to a WARNING and the tool falls back to the bundled bundle —
you'll see it in the startup logs.

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

### `exchange_mapping.yaml` (tracked in git, shipped inside the wheel)

Maps the uppercase `exchange_name` stored in `fh_config` to the lowercase
`ccxt` exchange id. Lives at `fh_symbol_check/data/exchange_mapping.yaml`
and is bundled inside the wheel as package data — the installed
`find-expired-symbols` command picks it up automatically (resolved via
`importlib.resources`), so operators do not need to keep a separate
copy on the server. Override with `--exchange-map /path/to/custom.yaml`
if you need to point at a local, out-of-tree file. Unknown exchange
names appear as `ERROR` rows in the report. The shipped file currently
maps 45 FH exchange names, including the full Binance family used in
the examples below:

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

You must supply **at least one** of `--hostname`, `--exchange-name`,
`--symbol`, or `--all`. `--all` is mutually exclusive with `--hostname` /
`--exchange-name`. All filters compose across whichever producer tables
`--source` selects.

By default `--source both` — every filter runs against both `fh_config`
and `repeater_feeds`. Pass `--source fh` (feed handlers only) or
`--source rp` (repeaters only) to narrow the scan.

### Example 1 — single host

Report every producer symbol on `TA-TKY-A-41` (substring match against
`hostname`). Scans both feed handlers and repeaters by default:

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

Scan every row in both `fh_config` and `repeater_feeds`. Useful for a
full-fleet audit; expect more exchange API calls and a longer runtime.

```bash
pixi run python find_expired_symbols.py --all
```

To limit the scan to one producer table, pass `--source`:

```bash
pixi run python find_expired_symbols.py --all --source fh   # feed handlers only
pixi run python find_expired_symbols.py --all --source rp   # repeaters only
pixi run python find_expired_symbols.py --all --source both # explicit default
```

When both tables are scanned, the text report emits **two summary tables** —
`Summary by feed handler` (column header `fh_name`) and `Summary by
repeater` (column header `app_name`) — and the detail block is split into
`--- Feed handlers ---` and `--- Repeaters ---` sections. Empty sections
and empty tables are elided, so a single-source run renders exactly one.
JSON / CSV rows carry a `source` field (`"fh"` or `"repeater"`), and
`service_id` is `null` / empty for repeater rows.

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

### Example 14 — find every occurrence of one or more specific symbols

Locate one or more symbols across the entire fleet (handy when you suspect
a single ticker is misbehaving or you want to know which FHs subscribe to
it). `--symbol` takes one or more space-separated values:

```bash
# One symbol
pixi run python find_expired_symbols.py --symbol IP/USDT-PERP

# Multiple symbols in one run (OR semantics \u2014 keeps rows matching any)
pixi run python find_expired_symbols.py --symbol IP/USDT-PERP BTC/USDT-PERP ETH/USDT-PERP
```

When used alone, `--symbol` implies `--all`, so the scan covers every
producer row selected by `--source` (both feed handlers and repeaters
by default). Matching is **case-sensitive and exact** (full-string
equality). Each value is checked against both the producer-side symbol
(the `cover_names` entry for feed handlers, the `instruments` entry for
repeaters \u2014 e.g. `IP/USDT-PERP`) and the translated venue-side symbol
(e.g. `IP/USDT:USDT`), so you can pass either form \u2014 or a mix of both \u2014
and the lookup just works:

```bash
pixi run python find_expired_symbols.py --symbol "IP/USDT:USDT"   # ccxt form
pixi run python find_expired_symbols.py --symbol "IP/USDT-PERP"   # FH form
pixi run python find_expired_symbols.py --symbol "IP/USDT-PERP" "BTC/USDT:USDT"  # mixed
```

When `--symbol` is set, LISTED rows are auto-included in the text report
\u2014 the tool is being used as a search, so every occurrence (LISTED,
INACTIVE, DELISTED, ERROR) is shown.

Combines naturally with the existing filter flags:

```bash
# Narrow to a single host
pixi run python find_expired_symbols.py --symbol IP/USDT-PERP --hostname TA-TKY-A-41

# Narrow to a single exchange
pixi run python find_expired_symbols.py --symbol IP/USDT-PERP BTC/USDT-PERP --exchange-name BINANCE

# Per-exchange roll-up of where the symbols live
pixi run python find_expired_symbols.py --symbol IP/USDT-PERP BTC/USDT-PERP --exchange-grouping

# Only surface FHs where any of the symbols are broken
pixi run python find_expired_symbols.py --symbol IP/USDT-PERP BTC/USDT-PERP --errors-only
```

If no FH carries any of the given symbols, you get a single WARNING line
and an empty report rather than an error.

### Example 15 — per-exchange roll-up across the fleet

For a fleet-wide health check across many feed handlers, collapse the
per-FH summary into a per-exchange one:

```bash
pixi run python find_expired_symbols.py --all --exchange-grouping
```

The per-symbol detail block is suppressed on stdout (you're looking at
fleet stats, not individual rows), and the per-source summary tables
(`Summary by feed handler`, `Summary by repeater`) are replaced by a
single per-`(exchange, source)` table: `exchange_name | source |
feed_handlers | active | inactive | delisted | error | total dead |
total`. The `source` column is `fh` or `repeater`; the `feed_handlers`
column counts distinct `(hostname, name)` pairs reporting under each
`(exchange, source)` bucket. If both a feed handler and a repeater report
against the same exchange you'll see two rows for that exchange.

To keep the per-symbol detail for triage, also pass `--output-file`:

```bash
pixi run python find_expired_symbols.py \
    --all --exchange-grouping --output-file fleet-report.txt
```

The on-screen summary table is unchanged, but the written file also
contains every `INACTIVE` / `DELISTED` / `ERROR` row grouped by producer
(split into `--- Feed handlers ---` / `--- Repeaters ---` sections) so
you can drill in.

`--exchange-grouping` is a **text-only** flag — JSON and CSV output are
unaffected and continue to emit one row per symbol.

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

`--output json` emits one object per checked symbol. `source` is `"fh"`
for rows sourced from `crypto_db.fh_config` and `"repeater"` for rows
sourced from `crypto_db.repeater_feeds`; `service_id` is `null` for
repeater rows (the DB table has no such column).

```json
[
  {
    "fh_name": "fh_binancedm_4007",
    "hostname": "TA-TKY-A-41_LOCAL",
    "exchange_name": "BINANCEDM",
    "ccxt_id": "binanceusdm",
    "original_symbol": "BTC/USDT-PERP",
    "ccxt_symbol": "BTC/USDT:USDT",
    "status": "LISTED",
    "detail": "",
    "source": "fh",
    "service_id": 4007
  },
  {
    "fh_name": "rp_binancedm_a",
    "hostname": "TA-TKY-B-01",
    "exchange_name": "BINANCEDM",
    "ccxt_id": "binanceusdm",
    "original_symbol": "SOMEOLD/USDT-PERP",
    "ccxt_symbol": "SOMEOLD/USDT:USDT",
    "status": "DELISTED",
    "detail": "not found in binanceusdm markets",
    "source": "repeater",
    "service_id": null
  }
]
```

### CSV

`--output csv` writes the same fields as a header row plus one row per
symbol, suitable for a spreadsheet or `xsv` / `pandas`. `source` is the
first column so you can pivot / filter by producer type; `service_id` is
empty for repeater rows:

```csv
source,service_id,fh_name,hostname,exchange_name,ccxt_id,original_symbol,ccxt_symbol,status,detail
fh,4006,fh_binance_4006,TA-TKY-A-41_LOCAL,BINANCE,binance,BTC/USDT,BTC/USDT,LISTED,
fh,4006,fh_binance_4006,TA-TKY-A-41_LOCAL,BINANCE,binance,OLDSPOT/USDT,OLDSPOT/USDT,INACTIVE,active=False
fh,4007,fh_binancedm_4007,TA-TKY-A-41_LOCAL,BINANCEDM,binanceusdm,BTC/USDT-PERP,BTC/USDT:USDT,LISTED,
repeater,,rp_binancedm_a,TA-TKY-B-01,BINANCEDM,binanceusdm,SOMEOLD/USDT-PERP,SOMEOLD/USDT:USDT,DELISTED,not found in binanceusdm markets
```

---

## CLI reference

| Flag                                  | Default                  | Description                                                                              |
| ------------------------------------- | ------------------------ | ---------------------------------------------------------------------------------------- |
| `--hostname <STR>`                    | —                        | Substring matched via `hostname LIKE %<STR>%` (applied to whichever tables `--source` selects) |
| `--exchange-name <STR>`               | —                        | Case-insensitive exact match against `exchange_name` (applied to whichever tables `--source` selects) |
| `--all`                               | off                      | Scan every row; mutually exclusive with `--hostname` / `--exchange-name`                 |
| `--source {fh,rp,both}`               | `both`                   | Which producer tables to query. `fh` = `crypto_db.fh_config` only; `rp` = `crypto_db.repeater_feeds` only; `both` = concatenate rows from both |
| `--symbol <STR> [<STR> ...]`          | —                        | Find every occurrence of one or more exact symbols (space-separated; OR semantics; case-sensitive, matched against both producer and venue forms). Used alone implies `--all`; combines with the other filters. Auto-enables `--show-listed` in text output |
| `--output {text,json,csv}`            | `text`                   | Report format                                                                            |
| `--output-file <PATH>`                | stdout                   | Write report here instead of stdout                                                      |
| `--show-listed`                       | off                      | Include `LISTED` rows in the text report                                                 |
| `--errors-only`                       | off                      | Filter report to producers with at least one `ERROR` row (producer identity is `(source, hostname, name, exchange_name)` so an FH and a repeater with the same name are not conflated). Log + exit code unchanged |
| `--exchange-grouping`                 | off                      | Text only: replace the per-source summary tables with a single per-`(exchange, source)` one (adds a `source` column); suppresses the per-symbol detail block on stdout unless `--output-file` is also set |
| `--concurrency <N>`                   | `4`                      | Max parallel ccxt exchanges (one `load_markets` per exchange, shared across its symbols) |
| `--fail-on-invalid` / `--no-fail-on-invalid` | `--fail-on-invalid` | Exit 1 when any `DELISTED` / `INACTIVE` row is present                                   |
| `--creds-file <PATH>`                 | `./.DBCreds.yaml`        | Credentials YAML                                                                         |
| `--creds-section <STR>`               | `crypto_db`              | Section name within the creds YAML                                                       |
| `--exchange-map <PATH>`               | `./exchange_mapping.yaml`| Override the FH-name → ccxt-id mapping file                                              |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `INFO`                | Log level                                                                                |
| `-v`, `--verbose`                     | off                      | Shortcut for `--log-level DEBUG`                                                         |

At least one of `--hostname`, `--exchange-name`, `--symbol`, or `--all` is
required; omitting all four is rejected by `argparse` (exit 2). `--symbol`
on its own implies `--all` for the host/exchange scan.

## Exit codes

| Code  | Meaning                                                                       |
| ----- | ----------------------------------------------------------------------------- |
| `0`   | Ran successfully; no invalid symbols (or `--no-fail-on-invalid`)              |
| `1`   | Ran successfully; at least one `DELISTED` / `INACTIVE` symbol was found       |
| `2`   | Operational failure (bad args, creds, DB, exchange map, I/O, unhandled exc.) |
| `130` | Interrupted (`Ctrl-C`)                                                        |

`ERROR` rows in the report do **not** affect the exit code — see
[How statuses are determined](#how-statuses-are-determined) below.

## How statuses are determined

Each symbol in the report ends up with exactly one of four statuses. The
rule used depends on whether the venue is ccxt-backed (the default path)
or checked through a custom-venue module.

### ccxt-backed venues

For every ccxt id in scope, the tool calls `ccxt.<id>().load_markets()`
once and looks each FH symbol up (uppercased) in the resulting
`{symbol: market}` dict:

| Status     | Rule                                                                       | `detail` field           |
| ---------- | -------------------------------------------------------------------------- | ------------------------ |
| `LISTED`   | symbol exists in `markets`; `markets[sym]["active"]` is `True` or missing  | empty                    |
| `INACTIVE` | symbol exists in `markets`, but `markets[sym]["active"]` is `False`        | `market.active is False` |
| `DELISTED` | symbol does **not** exist in `markets`                                     | empty                    |

`active` is ccxt's pass-through of the venue's own "this market is still
trading" flag. A symbol the venue still describes but has flagged as no
longer tradable surfaces as `INACTIVE` rather than `DELISTED`.

This is the only path that can produce `INACTIVE` for ccxt-backed venues —
if you see `INACTIVE` rows in the report, the venue itself reported the
market as `active=False` (typically a precursor to delisting on most
CEXes).

### Custom-venue checkers

The shipped custom venues map their venue-specific signals onto the same
triple but with different rules:

- **NADO** — `trading_status == "live"` → `LISTED`. Any other status
  (`post_only`, `reduce_only`, `soft_reduce_only`, `not_tradable`) →
  `INACTIVE`. Symbol absent from `/v2/symbols` → `DELISTED`.
- **POLYMARKETPERPS** — present in the response → `LISTED`; absent →
  `DELISTED`. The `/v1/info/instruments` endpoint exposes no active flag,
  so this venue **never produces `INACTIVE`**.
- **INJECTIVE** — LCD `status == "Active"` → `LISTED`. `Paused` /
  `Expired` → `INACTIVE`. `Demolished` is not fetched, so those (and
  anything else absent from Active/Paused/Expired) → `DELISTED`.
- **RHLIGHTER** — venue `status == "active"` → `LISTED`. `inactive` →
  `INACTIVE`. Absent from `/api/v1/orderBookDetails` → `DELISTED`. This
  is Robinhood Chain Lighter (`api.rh.lighter.xyz`), **not** the
  mainnet `LIGHTER` → ccxt `lighter` mapping.
- **ARCUS** — `status == "ONLINE"` → `LISTED`. `OFFLINE` → `INACTIVE`
  (docs: returned for visibility, not tradable). Absent from
  `/v1/markets` → `DELISTED`.
- **ONDOPERPS** — present in `/v1/markets` `tradingPairs` → `LISTED`.
  Absent → `DELISTED`. The endpoint exposes no active flag, so this
  venue **never produces `INACTIVE`**.
- **VERTEX family** — no fetch. Vertex Protocol shut down July 2025
  (Ink Foundation merger). Every configured symbol is `DELISTED` with
  the shutdown reason in `detail`. Not an `ERROR`.

See [Adding a custom (non-ccxt) venue](#adding-a-custom-non-ccxt-venue)
for how the registered checkers plug in.

### `ERROR`

`ERROR` is **not** a venue-side status — it means the tool itself could
not classify the symbol. The `detail` field explains why:

| Trigger                                                                                                       | `detail` text starts with                    |
| ------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| FH `exchange_name` has no entry in `exchange_mapping.yaml` and is not a registered custom venue               | `unknown exchange_name=...`                  |
| ccxt's `load_markets()` raised a recognised `MarketLoadError` (network, rate-limit, unsupported exchange, …)  | `load_markets failed: ...`                   |
| ccxt raised an unexpected exception                                                                           | `unexpected ccxt error: ...`                 |
| A custom venue's `fetch_symbols()` raised                                                                     | `custom venue fetch failed: ...`             |
| Internal: a `custom:NAME` sentinel id with no registered checker                                              | `no custom-venue checker registered for ...` |

`ERROR` rows are recorded in the report so you can investigate, but they
do **not** affect the exit code — only `DELISTED + INACTIVE` counts
trigger exit `1` under `--fail-on-invalid`. Use `--errors-only` to filter
the report to feed handlers that have at least one `ERROR` row.

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
  `BITGETDM`, `BYBITDM`, `OKEX`. These venues host both linear
  (`BTC/USDT-PERP`) and inverse (`BTC/USD-PERP`) perps under a single FH
  `exchange_name`. `OKEX` additionally carries **spot** symbols under the
  same `exchange_name` — those have no `-PERP` suffix, so the translator
  passes them through unchanged (`BTC/USDT` → `BTC/USDT`) and they match
  ccxt-okx's spot markets directly.

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
- `INJECTIVE` — `<BASE>/<QUOTE>-PERP` → `<BASE>/<QUOTE> PERP` (space); spot is identity
- `RHLIGHTER` — `<BASE>/<QUOTE>-PERP` or `<BASE>-PERP` → `<BASE>` (venue perps are a bare base); spot is identity
- `ARCUS` — `<BASE>/<QUOTE>-PERP` or `<BASE>/<QUOTE>` → `<BASE>-<QUOTE>` (`BTC/USD-PERP` → `BTC-USD`)
- `ONDOPERPS` — `<BASE>/<QUOTE>-PERP` or `<BASE>/<QUOTE>` → `<BASE>-<QUOTE>.P` (`NVDA/USD-PERP` → `NVDA-USD.P`)

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

   The key must match the `exchange_name` value stored in the producer
   tables (`fh_config` and/or `repeater_feeds` — both share the same
   value set) exactly, uppercase by convention. The value must be a
   valid `ccxt` exchange id; verify with
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

   The key must match the `exchange_name` value stored in the producer
   tables (`fh_config` and/or `repeater_feeds` — both share the same
   value set), uppercase. Custom-venue routing happens **before** the
   `exchange_mapping.yaml` lookup, so you do
   **not** need a YAML entry for these.

3. Tests should exercise the parser against fixture JSON (no network); see
   `tests/test_custom_venues_nado.py` for the pattern.

### Shipped custom venues

| FH `exchange_name` | Source                                                                  | Symbol translation                                                                       | Status mapping                                                                                                                                              |
| ------------------ | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NADO`             | `GET https://archive.prod.nado.xyz/v2/symbols`                          | `<BASE>/<QUOTE>-PERP` → `<BASE>-PERP`                                                    | `trading_status == "live"` → LISTED; `post_only` / `reduce_only` / `soft_reduce_only` / `not_tradable` → INACTIVE; symbol not in response → DELISTED |
| `POLYMARKETPERPS`  | `GET https://api.perpetuals.polymarket.com/v1/info/instruments`         | `<BASE>/USDC-PERP` → `<BASE>-USD` (with `WTI`→`WTIOIL` base remap)                       | Present → LISTED; absent → DELISTED. Endpoint exposes no active flag, so no INACTIVE state.                                                                  |
| `VERTEX` (Arbitrum), `AVAVERTEX` (Avalanche), `BERAVERTEX` (Berachain), `MNTVERTEX` (Mantle), `SOVERTEX` (Sonic) | **No fetch.** Vertex Protocol shut down July 2025 (Ink Foundation merger). Former `archive.*.vertexprotocol.com` hosts are gone; leftover DNS produced a TLS EOF that looked like a proxy block. | Passthrough | Every configured symbol is `DELISTED` with `detail` naming the shutdown and the former archive host. Not an `ERROR`. |
| `INJECTIVE`        | `GET https://sentry.lcd.injective.network/injective/exchange/v1beta1/{spot,derivative}/markets?status={Active,Paused,Expired}` (6 calls; `Demolished` omitted) | `<BASE>/<QUOTE>-PERP` → `<BASE>/<QUOTE> PERP`; spot is identity | `Active` → LISTED; `Paused` / `Expired` → INACTIVE; absent (including demolished) → DELISTED |
| `RHLIGHTER`        | `GET https://api.rh.lighter.xyz/api/v1/orderBookDetails` (Robinhood Chain Lighter — not ccxt `lighter`) | `<BASE>/<QUOTE>-PERP` or `<BASE>-PERP` → `<BASE>`; spot (`META/USDG`) is identity | `status == "active"` → LISTED; `inactive` → INACTIVE; absent → DELISTED |
| `ARCUS`            | `GET https://api.arcus.xyz/v1/markets` (dYdX Labs DEX; not in ccxt) | `<BASE>/<QUOTE>-PERP` or `<BASE>/<QUOTE>` → `<BASE>-<QUOTE>` | `ONLINE` → LISTED; `OFFLINE` → INACTIVE; absent → DELISTED |
| `ONDOPERPS`        | `GET https://api.ondoperps.xyz/v1/markets` (Ondo Perps; not in ccxt) | `<BASE>/<QUOTE>-PERP` or `<BASE>/<QUOTE>` → `<BASE>-<QUOTE>.P` | Present → LISTED; absent → DELISTED. Endpoint exposes no active flag, so no INACTIVE state. |

In `SymbolResult` output, custom-venue rows carry `ccxt_id="custom:<NAME>"`
(e.g. `custom:NADO`) so downstream consumers can tell where the
classification came from.

## Running the API service

The `find-expired-symbols-service` console script starts a FastAPI +
uvicorn service that runs the same scans the CLI does, but async and
polled. It's optional — install and use only if you want the browser
UI or need machine consumers to submit scans over HTTP; the CLI keeps
working unchanged.

### Local dev

```bash
# Uses the shipped pixi task alias, which pre-fills --creds-file
pixi run -e find-expired-symbols find-expired-symbols-service
```

Then browse to <http://127.0.0.1:8000/> for the UI and
<http://127.0.0.1:8000/docs> for the interactive OpenAPI console.

### API endpoints

| Method | Path                     | Purpose                                                   |
| ------ | ------------------------ | --------------------------------------------------------- |
| POST   | `/scans`                 | Submit a scan; returns 202 + job summary + `Location:`    |
| GET    | `/scans`                 | List recent (in-memory) jobs                              |
| GET    | `/scans/{id}`            | Get one job's state, progress, and (when done) results    |
| GET    | `/health`                | Liveness + version + active/recent counters               |
| GET    | `/`                      | HTMX-driven UI (form + scan cards + results table)        |
| GET    | `/docs`                  | FastAPI's auto-generated OpenAPI console                  |

The `POST /scans` body accepts the same filter fields as the CLI's
argparse (same field names, same validation error wording), plus an
integer `concurrency` (1–32, default 4):

```json
{
  "all": true,
  "source": "both",
  "errors_only": true
}
```

`POST /scans` with `--all` combined with `--hostname` (or empty
filters) returns HTTP 422 with the same error string the CLI's
argparse emits. This is deliberate — one dialect for both fronts.

### Persistence & lifecycle

- **In-memory only.** Jobs live in a dict guarded by a lock. There is
  no database; a process restart wipes everything.
- **TTL sweep.** Completed jobs (`done` / `failed`) are dropped 1 hour
  after `completed_at`. Configurable with `--job-ttl-seconds` on the
  service CLI. In-flight jobs are never swept.
- **Bounded concurrency.** `--max-concurrent-scans` (default 2) caps
  in-flight scans; excess submissions queue behind them and stay in
  the `queued` state until a worker frees up.
- **No auth.** The service is intended for behind-the-perimeter use
  and binds `127.0.0.1` by default. Front it with a reverse proxy
  (nginx / traefik / caddy) if you need TLS or basic-auth.

### CLI reference (service)

```
find-expired-symbols-service [--bind ADDR] [--port N]
                             [--creds-file PATH] [--creds-section NAME]
                             [--exchange-map PATH]
                             [--job-ttl-seconds SECONDS]
                             [--max-concurrent-scans N]
                             [--log-level LEVEL] [-v]
```

Server-side deployment (systemd user unit, restart-on-update, linger)
is documented in [`deploy.md`](deploy.md).

## Deploying on a Linux server

The project ships as a wheel that installs into a pixi-managed env.
The deploy directory is user-chosen — `/opt/find-expired-symbols/`,
`/home/<user>/api/find-expired-symbols/`, `/srv/…/`, whatever fits.
The same five steps run for a fresh install and for every upgrade:

```bash
# On the dev box:
# 1. Build the wheel
pixi run -e dev python -m build --wheel

# 2. Rsync wheel + pixi manifest + lockfile to the install directory
rsync dist/find_expired_symbols-*.whl pixi.toml pixi.lock \
    stephen.m@your-server:/home/stephen.m/api/find-expired-symbols/

# On the server:
# 3. SSH
ssh stephen.m@your-server

# 4. Change to the install directory
cd /home/stephen.m/api/find-expired-symbols

# 5. Install the wheel into the pixi env
pixi run -e find-expired-symbols pip install --no-deps --force-reinstall \
    find_expired_symbols-0.1.0-py3-none-any.whl
```

pixi provides the Python interpreter, `pip`, and every runtime dep
declared in `pixi.toml` — no system Python required. `--no-deps` keeps
pip from re-resolving deps from PyPI (which would fail on servers where
`cryptography` PyPI wheels want a newer glibc than the conda-forge
Python advertises); `--force-reinstall` cleanly swaps the wheel out on
updates.

**First-time-only extras.** Create `.DBCreds.yaml` in the install
directory:

```bash
cat > .DBCreds.yaml <<'EOF'
crypto_db:
  host: <FH MySQL hostname or IP>
  database: crypto_db
  user: <username>
  password: <password>
EOF
chmod 600 .DBCreds.yaml
```

The `pixi.toml` includes a task alias that auto-picks it up via
`$PIXI_PROJECT_ROOT/.DBCreds.yaml`, so users never have to pass
`--creds-file` on the command line and the install is fully
relocatable.

The wheel installs two console scripts:

- `find-expired-symbols` — the CLI
- `find-expired-symbols-service` — the API + UI service

Any scheduler (Airflow, Jenkins, cron, systemd timers, …) can invoke
the CLI console script by absolute path:

```bash
FES_DIR=/home/stephen.m/api/find-expired-symbols
"$FES_DIR/.pixi/envs/find-expired-symbols/bin/find-expired-symbols" \
    --creds-file "$FES_DIR/.DBCreds.yaml" \
    --all \
    --output json \
    --output-file /var/log/find-expired-symbols/$(date +%F).json
```

Exit codes (see [Exit codes](#exit-codes)) are stable and safe to key
alerting off of:

- `0` = clean run, nothing invalid found
- `1` = ran successfully, but reported at least one non-`LISTED` symbol
- `2` = operational failure (bad creds, DB down, malformed mapping, …)
- `130` = interrupted (SIGINT)

The API service is supervised as a systemd **user** unit —
`deploy/find-expired-symbols.service` in the repo ships a template.
First-time setup: `systemctl --user daemon-reload && systemctl --user
enable --now find-expired-symbols && sudo loginctl enable-linger $USER`.
On subsequent deploys, follow step 5 with `systemctl --user restart
find-expired-symbols` to pick up the new wheel.

**Full runbook**, including the API service systemd wiring,
multi-user permissions, verification / smoke tests, uninstall /
rollback, and a Zscaler-heavy troubleshooting cheatsheet:
[`deploy.md`](deploy.md).

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
├── pyproject.toml              # packaging metadata + console script entry point
├── pixi.toml                   # dev environment (osx-arm64 + linux-64)
├── find_expired_symbols.py     # source-checkout entry point (delegates to run())
├── fh_symbol_check/            # package with the actual logic
│   ├── cli.py                  # argparse + orchestration + run() console entry
│   ├── pipeline.py             # run_scan() — shared entry for CLI + API service
│   ├── creds.py                # .DBCreds.yaml loader + DBCreds dataclass
│   ├── db.py                   # fetch_feed_handlers(...) + fetch_repeaters(...)
│   ├── exchange_map.py         # exchange_mapping.yaml loader + resolve()
│   ├── symbol_translation.py   # per-FH-exchange-name translators
│   ├── validator.py            # build_tasks + classify_symbols
│   ├── reporter.py             # text/json/csv rendering + summary table
│   ├── logging_config.py
│   ├── models.py               # dataclasses (FeedHandlerRow, SymbolResult, ...)
│   ├── data/
│   │   └── exchange_mapping.yaml  # tracked; ships inside the wheel as package data
│   ├── api/                    # FastAPI service + HTMX UI (see "Running the API service")
│   │   ├── main.py             # `find-expired-symbols-service` console entry
│   │   ├── server.py           # build_app() factory + TTL sweeper
│   │   ├── routes.py           # JSON API: /scans, /health, ...
│   │   ├── views.py            # HTMX endpoints: GET /, POST /, GET /scans/{id}/partial
│   │   ├── models.py           # Pydantic schemas (ScanRequest, ScanJobDetail, ...)
│   │   ├── jobs.py             # thread-safe in-memory JobStore + TTL sweep
│   │   ├── workers.py          # bounded ThreadPoolExecutor running pipeline.run_scan
│   │   ├── templates/          # Jinja templates (base, home, scan_card, result_table)
│   │   └── static/             # bundled htmx.min.js + app.css
│   └── custom_venues/          # non-ccxt venue checkers (ARCUS, NADO, ONDOPERPS, POLYMARKETPERPS, INJECTIVE, RHLIGHTER, VERTEX family, …)
│       ├── __init__.py         # CUSTOM_VENUES registry + sentinel helpers
│       ├── arcus.py            # api.arcus.xyz /v1/markets client (dYdX Labs DEX)
│       ├── injective.py        # Injective LCD REST (spot + derivative)
│       ├── nado.py             # archive.prod.nado.xyz /v2/symbols client
│       ├── ondoperps.py        # api.ondoperps.xyz /v1/markets client
│       ├── polymarket_perps.py # api.perpetuals.polymarket.com /v1/info/instruments client
│       ├── rhlighter.py        # api.rh.lighter.xyz orderBookDetails (Robinhood Chain)
│       └── vertex.py           # Vertex family — venue gone (July 2025); no network
├── deploy/
│   └── find-expired-symbols.service  # systemd user unit template for the API service
├── tests/                      # pytest suite (includes test_packaging.py + API tests)
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
| Every symbol on **one specific exchange** is `ERROR` with `detail: load_markets failed: <ex> GET https://…` | The exchange's `load_markets()` call is failing group-wide. On corp networks, three common causes, all about the SSL-inspection proxy (Zscaler, Palo Alto, Netskope): **(a)** the corp root CA isn't in Python's trust store — the tool calls `truststore.inject_into_ssl()` at startup, so as long as IT has added the corp root to macOS Keychain / the Linux system CA bundle / the Windows cert store this "just works"; **(b)** the proxy policy blocks non-browser User-Agents on Cryptocurrency-categorised hosts and returns an HTML block page — the tool now sends a Chrome 126 desktop UA on all ccxt calls to sidestep this; **(c)** the proxy denies the host outright via URL category (typical for HTX/Huobi — sanctioned exchange) with HTTP 403 + `wac_block.html`. Case (c) can't be fixed in code — the tool detects the sentinel and emits a clean hint (`blocked by corporate proxy (Zscaler wac_block.html); … — contact IT to allowlist this exchange host`), but the actual unblock needs IT. Re-run with `-v` to see the full underlying error at DEBUG level. |
| Every symbol on a **custom venue** (`NADO`, `POLYMARKETPERPS`, `INJECTIVE`, `RHLIGHTER`, `ARCUS`, `ONDOPERPS`) is `ERROR` with `detail: custom venue fetch failed: likely blocked by corporate network policy (TLS handshake closed before certificate exchange); …` | A firewall in the egress path is dropping the connection mid-TLS-handshake based on the SNI hostname — TCP connects, the ClientHello goes out, and the peer returns zero bytes with no certificate. Distinct from case (c) above: nothing decrypted the request, so there's no 403 and no block page. Confirm with `openssl s_client -connect <host>:443 -servername <host> </dev/null` (look for `read 0 bytes` + `no peer certificate available`), then check a different custom venue from the same machine to show the block is destination-specific. Needs a firewall/URL-policy **allow** rule from IT — an SSL-inspection bypass won't help, since no inspection is happening. A genuine venue outage looks identical, which is why the hint says "likely". **Not Vertex** — see the next row. |
| Every **VERTEX** / **AVAVERTEX** / **BERAVERTEX** / **MNTVERTEX** / **SOVERTEX** symbol is `DELISTED` with `detail` mentioning July 2025 / Ink Foundation | Expected. Vertex Protocol shut down in July 2025; the archive hosts are gone. The checker does not call them. Remove those symbols from FH / repeater config. |
| Many `ERROR` rows across **many** exchanges                          | Either the `exchange_name` is missing from `exchange_mapping.yaml` (each row's `detail` starts with `unknown exchange_name=`), or you disabled `truststore` and the OS trust store lookup failed at startup — check the startup logs for `truststore injection failed`. |
| Symbols you expect to be valid show as `DELISTED`                    | Almost always a symbol-translation mismatch — re-run with `-v` and compare `internal_symbol` vs `ccxt_symbol` in the report. Vertex-family rows are the exception: those are `DELISTED` because the venue shut down. |
| `creds error: section 'crypto_db' not found`                         | Wrong `--creds-section` or your YAML is missing that section.                                                                   |
| Exit code 2 with `specify --hostname, --exchange-name, --symbol, or --all` | You ran the tool with no filter. Pick one; `--all` is the explicit "scan everything" option.                              |
