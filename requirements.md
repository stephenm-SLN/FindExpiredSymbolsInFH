# Requirements — Feed Handler Symbol Validity Checker

## 1. Purpose
Pull every symbol configured in the Feed Handlers from the `crypto_db.fh_config` table and report any symbol that is no longer a valid (i.e. delisted / inactive) symbol on its corresponding exchange. Scope is selected per-run by host / exchange / symbol filters, or by passing `--all` to scan the whole fleet.

## 2. Inputs

### Filter args (at least one of the four is required)
- `--hostname <STRING>` — substring used in the SQL `LIKE` filter, e.g. `TA-TKY-A-41`. Bound as a parameter, not string-formatted, to avoid SQL injection.
- `--exchange-name <STRING>` — case-insensitive exact match against `fh_config.exchange_name`, e.g. `BINANCE`. SQL-side filter.
- `--symbol <STRING>` — find every occurrence of this exact symbol (case-sensitive, full-string equality, matched against both the FH `cover_names` form and the translated venue form). Used alone implies `--all`; composes with the other filter flags.
- `--all` — no host/exchange filter; scan every row in `fh_config`. Mutually exclusive with `--hostname` and `--exchange-name`.

Omitting all four is rejected by `argparse` (exit 2).

### Report-shaping args (all optional)
- `--output {text,json,csv}` (default `text`).
- `--output-file <PATH>` (default stdout).
- `--show-listed` — include LISTED rows in text output (default: omit). Auto-enabled when `--symbol` is set so every occurrence is visible.
- `--errors-only` — filter the report (text/JSON/CSV) to feed handlers that have at least one `ERROR` row. Does not affect the summary log line or the exit code.
- `--exchange-grouping` — text only; replace the per-feed-handler summary table with a per-exchange one. Per-symbol detail rows are suppressed on stdout (use `--output-file` to keep them in the written file). No effect on JSON/CSV.

### Behaviour args (all optional)
- `--fail-on-invalid` / `--no-fail-on-invalid` (default `--fail-on-invalid` → exit 1 when any `DELISTED`/`INACTIVE` is found).
- `--concurrency <INT>` for ccxt market-load fan-out (default 4).
- `--log-level {DEBUG,INFO,WARNING,ERROR}` (default `INFO`), `-v`/`--verbose` as a `DEBUG` shortcut.

### Config args (all optional)
- `--creds-file <PATH>` (default `./.DBCreds.yaml`).
- `--creds-section <NAME>` (default `crypto_db`) — top-level YAML key under which the DB creds live.
- `--exchange-map <PATH>` (default `./exchange_mapping.yaml`) — maps `exchange_name` values from the DB to ccxt exchange ids.

### Config / secrets
DB credentials are read **only** from the credentials file. Never hard-coded, never logged.

## 3. Outputs

### Text (default)
- One human-readable detail block per FH (grouped by `(hostname, fh_name, exchange_name)`) listing every `INACTIVE` / `DELISTED` / `ERROR` row (and `LISTED` rows too if `--show-listed` is set or `--symbol` auto-enables it).
- A psql-style **summary table** at the bottom:
  - Default: one row per feed handler with `fh_name | hostname | exchange_name | active | inactive | delisted | error | total dead | total`, plus a `TOTAL` footer row.
  - With `--exchange-grouping`: one row per exchange with `exchange_name | feed_handlers | active | inactive | delisted | error | total dead | total`, plus a `TOTAL` footer row. Detail blocks are suppressed on stdout in this mode (kept when writing to `--output-file`).
- A one-line **global summary**: `Summary: LISTED=N INACTIVE=N DELISTED=N ERROR=N`.

### Machine-readable (`--output json|csv`)
One record per checked symbol with the fields:
- `service_id`, `fh_name`, `hostname`, `exchange_name`, `ccxt_id`
- `original_symbol` (FH-side, e.g. `BTC/USDT-PERP`)
- `ccxt_symbol` (translated venue-side, e.g. `BTC/USDT:USDT`)
- `status` ∈ {`LISTED`, `INACTIVE`, `DELISTED`, `ERROR`}
- `detail` (free-text reason; populated for `INACTIVE` / `DELISTED` / `ERROR`)

JSON and CSV are unaffected by `--show-listed` / `--exchange-grouping` (they always emit one row per symbol).

### Exit codes
- `0` — ran successfully; no invalid symbols (or `--no-fail-on-invalid` was set).
- `1` — ran successfully; at least one `DELISTED` or `INACTIVE` symbol found and `--fail-on-invalid` is set.
- `2` — operational failure (bad args, DB error, creds error, exchange-map I/O error, unhandled exception, output-file I/O error).
- `130` — `KeyboardInterrupt`.

`ERROR` rows alone do **not** force exit 1 — they signal "couldn't determine" (typically a load_markets failure on one exchange or a custom-venue API failure), not "delisted".

## 4. Functional Requirements
1. Accept at least one of `--hostname` / `--exchange-name` / `--symbol` / `--all` at runtime. Hostnames / exchange names / symbols are runtime values; never hard-coded.
2. Query `crypto_db.fh_config` for rows matching the active filter(s) using parameterised SQL — `hostname LIKE %s` and/or `UPPER(exchange_name) = UPPER(%s)`. With `--all`, no `WHERE` clause is added.
3. From each returned row, extract the columns:
   - `service_id`
   - `fh_name`
   - `hostname`
   - `exchange_name` (uppercase DB value, e.g. `HUOBI`, `WOODEX`, `NADO`, `POLYMARKETPERPS`) → routed via either the `exchange_mapping.yaml` (ccxt-backed venues) or the custom-venue registry (non-ccxt venues)
   - `cover_names` (comma-separated list of internal-format symbols)
4. For every symbol in `cover_names`, translate it to the venue-expected format using a per-`exchange_name` translator (see Design §3.4). Pass-through is the default rule when no translator is registered.
5. For each `(venue, ccxt_symbol)` pair, determine validity:
   - **ccxt-backed venues**: call `exchange.load_markets()` once per ccxt id and look up the symbol. `LISTED` if present and `market.active != False`; `INACTIVE` if present but `market.active == False`; `DELISTED` if not present.
   - **Custom venues** (Nado, Polymarket Perps, …): call the registered custom checker once per venue and look up the symbol in its returned mapping. `LISTED` if present and live; `INACTIVE` if present but flagged not-live; `DELISTED` if not present.
   - **Either path** can produce `ERROR` rows on transport / parse / market-load failures; these are scoped to one venue and never crash the run.
6. Apply post-fetch filters in Python, in order:
   - `--symbol`: keep only tasks/errors whose `original_symbol` OR `ccxt_symbol` equals the typed value (case-sensitive exact).
7. Produce the chosen report (text / JSON / CSV) and apply the `--errors-only` / `--exchange-grouping` text-mode adjustments described in §3.
8. Exit with the documented exit code based on findings and `--fail-on-invalid`.

Note: there is no `enabled`/`active` flag on `fh_config`; every row matching the filter set is processed.

## 5. Non-Functional Requirements
- **Reliability**: a single delisted symbol, a single network blip on one exchange, or a single malformed DB row must NOT crash the run. Failures are logged and reported per-symbol; the run completes.
- **Secrets handling**: credentials are only loaded from the creds file; never printed (even at DEBUG); the creds file must be git-ignored before any commit.
- **Performance**: at most one `load_markets()` call per exchange per run (cache the market dict in-process, keyed by exchange id). Optional bounded concurrency across exchanges.
- **Observability**: structured logging (level + timestamp + module + message). DEBUG level shows per-symbol decisions; INFO level shows per-exchange summaries and the final report.
- **Idempotency**: running twice on the same host yields the same result (subject to live ccxt market state).
- **Portability**: runs under the existing `pixi` environment on macOS/Linux. Python ≥ 3.10 (helper already uses `list[str]` PEP-604 syntax).

## 6. Success Criteria (verifiable)
1. `pixi run python find_expired_symbols.py --hostname TA-TKY-A-41` exits without raising, prints a report, and (if any symbol is `DELISTED` / `INACTIVE`) exits non-zero.
2. `pixi run python find_expired_symbols.py --all` scans the whole fleet without requiring a host filter.
3. `pixi run python find_expired_symbols.py --exchange-name BINANCE` filters by exchange at the DB level.
4. `pixi run python find_expired_symbols.py --symbol IP/USDT-PERP` finds every FH carrying that exact symbol; used alone it implies `--all`.
5. `pixi run python find_expired_symbols.py --all --exchange-grouping` replaces the per-FH summary table with a per-exchange one.
6. With `--output json` the stdout is valid JSON parseable by `json.loads` (one row per symbol; unaffected by `--show-listed` / `--exchange-grouping`).
7. With `--output csv` the stdout is a CSV whose header matches the fields listed in §3.
8. Running the tool with a non-matching filter produces an empty report and exit code `0`.
9. Running with bad creds produces a clear error message and exit code `2`, with **no credential values in the output**.
10. No literal credential strings appear anywhere in the new code (verifiable by `rg`).
11. The hostname / exchange-name / etc. appear in the SQL via bound parameters, not via f-string/`%`/`+` (verifiable by code review).
12. `.DBCreds.*` is listed in `.gitignore` before any commit touches it.

## 7. Assumptions
- A1. The Feed Handler database is the MySQL instance at `10.50.12.8`, database `crypto_db`, with credentials in `.DBCreds.yaml` under the `crypto_db` section. **(confirmed)**
- A2. The `fh_config` table contains one row per (host, feed handler) service. **(confirmed by sample SELECT *)**
- A3. The symbol list per row is a comma-separated string in `cover_names`. **(confirmed by sample SELECT *)**
- A4. `exchange_name` is uppercase and not a ccxt id; it must be translated via `exchange_mapping.yaml`. **(confirmed)**
- A5. `ccxt`'s `load_markets()` is the authoritative source for "is this symbol still listed".
- A6. The tool runs interactively from a developer machine that has network access to both the DB and the public exchange APIs.
- A7. Symbols in `cover_names` are in an internal format that may require per-exchange translation to ccxt format. **(confirmed)**
- A8. There is no `enabled`/`active` column on `fh_config`; every row returned by the LIKE query is processed.

## 8. Resolved Decisions

1. **Credentials file** — `.DBCreds.yaml` (capital `C`, capital `B`). YAML is a **map of named sections**; the section used by this tool is `crypto_db`. Loader signature: `load_creds(path, section)`.

2. **Schema of `crypto_db.fh_config`** — columns used by this tool:
   - `service_id` (int)
   - `fh_name` (e.g. `fh_huobi_4002`)
   - `hostname` (e.g. `TA-TKY-A-41_LOCAL` — so `%TA-TKY-A-41%` LIKE matches as expected)
   - `exchange_name` (uppercase, **not** a ccxt id — e.g. `HUOBI`, `WOODEX`)
   - `cover_names` (comma-separated internal-format symbols)

   Sample rows captured below for traceability:
   ```
   4002, fh_huobi_4002,  TA-TKY-A-41_LOCAL, HUOBI,  "BTC/USDT,PEPE/USDT,ETH/USDT,…"
   4003, fh_woodex_4003, TA-TKY-A-41_LOCAL, WOODEX, "1000BONK/USDC-PERP,APT/USDC-PERP,…"
   4004, fh_huobi_4004,  TA-TKY-A-41_LOCAL, HUOBI,  "SATS/USDT,MASA/USDT,…"
   ```

3. **DB host** — `10.50.12.8`, database `crypto_db`, credentials in `.DBCreds.yaml` (`crypto_db` section).

4. **Symbol column** — `cover_names`. No others.

5. **Symbol format** — internal; requires per-exchange translation to the venue-side format. Translation rules live in `fh_symbol_check/symbol_translation.py` as a registry keyed by FH `exchange_name` (default = identity). See Design §3.4 for the current translator primitives.

6. **Disabled / inactive feed handlers** — there is **no** `enabled` column on `fh_config`. Every row returned by the LIKE query is processed.

7. **Helper-script refactors** — **option (b) approved**: minimal surgical refactors to expose pure-library functions.
   Per the prompt, I will still post the proposed diffs in chat for sign-off *before* applying them to either helper.

8. **`exchange_name` → ccxt id mapping** — lives in a separate, code-tracked config file `exchange_mapping.yaml` next to `.DBCreds.yaml`. Initial seed (subject to your confirmation when I post the diff):
   ```yaml
   HUOBI: htx
   WOODEX: woo
   ```
   Unknown `exchange_name` values produce per-row `ERROR` results, never crash the run.

## 9. Out of Scope (for this iteration)
- Writing to / mutating the Feed Handler DB.
- Auto-remediation (e.g. removing delisted symbols from `fh_config`).
- Persistent caching of exchange metadata across runs (in-process cache only).
- A web UI / scheduled job. CLI only.
- Auto-detecting symbol-format translation rules — they are explicitly registered per FH `exchange_name`.

(Note: non-ccxt exchanges are now supported through the custom-venue framework — see Design §3.9. Nado and Polymarket Perps ship with their own checkers; `POLYMARKETINT` and a handful of `*DM` venues remain parked pending FH-side data, see `task.md` Phase 7d/7f for the open list.)
