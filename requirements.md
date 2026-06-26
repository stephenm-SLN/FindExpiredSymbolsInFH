# Requirements — Feed Handler Symbol Validity Checker

## 1. Purpose
For a given Feed Handler host, pull every symbol configured in the Feed Handlers from the `crypto_db.fh_config` table and report any symbol that is no longer a valid (i.e. delisted / inactive) symbol on its corresponding exchange.

## 2. Inputs
- **Required CLI arg**: `--hostname <STRING>` — substring used in the SQL `LIKE` filter, e.g. `TA-TKY-A-41`. No default. Bound as a parameter, not string-formatted, to avoid SQL injection.
- **Optional CLI args** (see `implementation.md` for full list):
  - `--output {text,json,csv}` (default `text`)
  - `--output-file <PATH>` (default stdout)
  - `--log-level {DEBUG,INFO,WARNING,ERROR}` (default `INFO`)
  - `--fail-on-invalid / --no-fail-on-invalid` (default fail-on-invalid → non-zero exit when any delisted symbol is found)
  - `--concurrency <INT>` for ccxt market-load fan-out (default conservative, e.g. 4)
  - `--creds-file <PATH>` (default `./.DBCreds.yaml`)
  - `--creds-section <NAME>` (default `crypto_db`) — top-level YAML key under which the DB creds live
  - `--exchange-map <PATH>` (default `./exchange_mapping.yaml`) — maps `exchange_name` values from the DB to ccxt exchange ids
- **Config / secrets**: DB credentials read **only** from the credentials file. Never hard-coded, never logged.

## 3. Outputs
- **Console (text mode, default)**: one human-readable line per delisted symbol, grouped by exchange / feed handler; final summary line with counts.
- **Machine-readable (`--output json|csv`)**: one record per checked symbol with at minimum:
  - `hostname`
  - `service_id` (feed handler identifier)
  - `exchange` (ccxt exchange id)
  - `symbol`
  - `status` ∈ {`LISTED`, `INACTIVE`, `DELISTED`, `ERROR`}
  - `detail` (e.g. error message for `ERROR` rows)
- **Exit codes**:
  - `0` — ran successfully, no invalid symbols (or `--no-fail-on-invalid` was set).
  - `1` — ran successfully, at least one invalid symbol found, and `--fail-on-invalid` is set.
  - `2` — operational failure (bad args, DB error, creds error, unrecoverable ccxt failure for *every* exchange).

## 4. Functional Requirements
1. Accept `--hostname` as a required runtime argument. The hostname value must not be hard-coded anywhere.
2. Query `crypto_db.fh_config` for rows where `hostname LIKE %<hostname>%` using a parameterized query.
3. From each returned row, extract the columns:
   - `service_id`
   - `fh_name`
   - `hostname`
   - `exchange_name` (uppercase DB value, e.g. `HUOBI`, `WOODEX`) → mapped to a ccxt exchange id via `exchange_mapping.yaml`
   - `cover_names` (comma-separated list of internal-format symbols)
4. For every internal symbol in `cover_names`, translate it to ccxt format using a per-exchange translator (see Design §3.7). Pass-through is the default rule when no translator is registered.
5. For each `(ccxt_exchange_id, ccxt_symbol)` pair, determine validity using the (refactored) `check_delisted_symbol.py`. A symbol is **invalid** if:
   - it is not present in `exchange.load_markets()`, **or**
   - it is present but `market["active"]` is `False`.
6. Produce a clear report in the chosen format showing every invalid symbol with its hostname / service_id / fh_name / exchange.
7. Exit with the documented exit code based on findings and `--fail-on-invalid`.

Note: there is no `enabled`/`active` flag on `fh_config`; every row matching the LIKE filter is processed.

## 5. Non-Functional Requirements
- **Reliability**: a single delisted symbol, a single network blip on one exchange, or a single malformed DB row must NOT crash the run. Failures are logged and reported per-symbol; the run completes.
- **Secrets handling**: credentials are only loaded from the creds file; never printed (even at DEBUG); the creds file must be git-ignored before any commit.
- **Performance**: at most one `load_markets()` call per exchange per run (cache the market dict in-process, keyed by exchange id). Optional bounded concurrency across exchanges.
- **Observability**: structured logging (level + timestamp + module + message). DEBUG level shows per-symbol decisions; INFO level shows per-exchange summaries and the final report.
- **Idempotency**: running twice on the same host yields the same result (subject to live ccxt market state).
- **Portability**: runs under the existing `pixi` environment on macOS/Linux. Python ≥ 3.10 (helper already uses `list[str]` PEP-604 syntax).

## 6. Success Criteria (verifiable)
1. `python find_expired_symbols.py --hostname TA-TKY-A-41` exits without raising, prints a report, and (if any symbol is delisted) exits non-zero.
2. With `--output json` the stdout is valid JSON parseable by `json.loads`.
3. With `--output csv` the stdout is a CSV whose header matches the fields listed in §3.
4. Running the tool with a non-existent hostname produces an empty report and exit code `0`.
5. Running with bad creds produces a clear error message and exit code `2`, with **no credential values in the output**.
6. No literal credential strings appear anywhere in the new code (verifiable by `rg`).
7. The hostname appears in the SQL via a bound parameter, not via f-string/`%`/`+` (verifiable by code review).
8. `.DBCreds.*` is listed in `.gitignore` before any commit touches it.

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

5. **Symbol format** — internal; requires per-exchange translation to ccxt format. Translation rules live in a new Python module `fh_symbol_check/symbol_translation.py` as a registry of per-ccxt-id translators (default = identity).

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
- Mapping non-ccxt exchanges or feed handlers without a ccxt equivalent.
- Auto-detecting symbol-format translation rules — they are explicitly registered per exchange.
