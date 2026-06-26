# Task List — Feed Handler Symbol Validity Checker

Tick items as they complete. New tasks may be appended as the project progresses.

## Phase 0 — Planning & sign-off

- [x] Read `CLAUDE.md`, existing helpers, and `.DBCreds.yaml`
- [x] Write `requirements.md`
- [x] Write `design.md`
- [x] Write `implementation.md`
- [x] Write `task.md`
- [x] Resolve open questions (all locked — see `requirements.md` §8):
  - [x] Q1: creds file = `.DBCreds.yaml`, section = `crypto_db`
  - [x] Q2: schema columns = `service_id, fh_name, hostname, exchange_name, cover_names`
  - [x] Q3: DB host = `10.50.12.8`, db = `crypto_db`
  - [x] Q4: symbol column = `cover_names`
  - [x] Q5: internal symbol format → per-exchange translators in `symbol_translation.py`
  - [x] Q6: no `enabled` flag — process every row
  - [x] Q7: helper refactors approved (option B for both)
  - [x] Q8: `exchange_name` mapping lives in code-tracked `exchange_mapping.yaml`
- [ ] **WAIT for user review of updated planning docs**

## Phase 1 — Repo hygiene (do FIRST, before any new code)

- [x] Add `.DBCreds.*`, `__pycache__/`, `*.pyc`, `.pytest_cache/` to `.gitignore`
- [x] Verify ignore rules by inspection (workspace is not a git repo yet, so `git check-ignore` is N/A; rules confirmed by reading `.gitignore` — `.DBCreds.*` matches the yaml file, `exchange_mapping.yaml` matches no rule)
- [x] Add `python=>=3.10,<3.14`, `pymysql`, `pyyaml` to `[dependencies]` and `ccxt` to `[pypi-dependencies]` (conda-forge has no current `ccxt`); lock with `pixi install`
- [x] Add `pytest`, `mypy`, `ruff` to `[feature.dev.dependencies]` and create `dev` environment
- [x] Smoke test: `pixi run --environment dev python -c "import pymysql, ccxt, yaml"` → OK (python 3.13.14, pymysql 2.2.8, ccxt 4.5.60, yaml 6.0.3)
- [ ] (Follow-up) `git init` so `.gitignore` actively protects creds before any commit

## Phase 2 — Helper refactors (Q7 approved — diffs reviewed first)

- [x] `check_delisted_symbol.py` (design §4.1 / impl §5.1): added `MarketLoadError`, `load_exchange_markets_safe`, pure `classify`; existing CLI behaviour preserved (verified via `--list-exchanges` and no-args help)
- [x] `mysql_select_query.py` (design §4.2 / impl §5.2): removed credential defaults; added `params` to `fetch_query_results`; `__main__` now reads `.DBCreds.yaml` (`crypto_db` section)
- [x] Smoke tests: `classify` covers LISTED/INACTIVE/DELISTED (incl. case-insensitivity + missing `active` key); `load_exchange_markets_safe` raises `MarketLoadError` for unknown exchanges; `MySQLQueryClient.__init__` rejects missing creds with `TypeError`
- [x] Confirmed via `rg`: no hard-coded credential strings remain in any `.py` file

## Phase 3 — Build the new package

- [x] `exchange_mapping.yaml` (seed: `HUOBI: htx`, `WOODEX: woo`) — code-tracked
- [x] `fh_symbol_check/__init__.py` (empty)
- [x] `models.py` — dataclasses + `SymbolStatus`
- [x] `creds.py` — YAML loader by section, masked-repr `DBCreds`, `CredsError`
- [x] `db.py` — parameterised `fetch_feed_handlers`, `FeedHandlerRow`, `DBError`
- [x] `exchange_map.py` — load + case-insensitive resolve, `ExchangeMapError`
- [x] `symbol_translation.py` — `TRANSLATORS` registry + `translate`; `_translate_woo`
- [x] `validator.py` — `build_tasks` (pure) + `classify_symbols` (per-exchange single market load, per-exchange error containment, optional concurrency)
- [x] `reporter.py` — text / json / csv renderers + summary
- [x] `logging_config.py` — `setup_logging`
- [x] `cli.py` — argparse + orchestration + exit codes
- [x] `find_expired_symbols.py` — thin entry point
- [x] Added `mypy.ini` (ignore_missing_imports for ccxt) and `types-PyYAML`/`types-PyMySQL` to dev deps
- [x] Lint clean (`pixi run -e dev ruff check fh_symbol_check find_expired_symbols.py`)
- [x] Type-check clean (`pixi run -e dev mypy fh_symbol_check find_expired_symbols.py tests` — 19 source files, 0 issues)

## Phase 4 — Tests (pytest)

- [x] `tests/conftest.py` — ensure workspace root on sys.path
- [x] `tests/test_creds.py` (9 tests) — load by section, missing file, missing section, malformed, repr masks, password not in caplog, frozen dataclass
- [x] `tests/test_db.py` (5 tests) — stubbed `MySQLQueryClient`; verifies `%s` placeholder, bound param `%<hostname>%`, cover_names parsing, empty handling, error wrapping
- [x] `tests/test_exchange_map.py` (7 tests) — load uppercases keys, case-insensitive resolve, empty/missing/malformed handling
- [x] `tests/test_symbol_translation.py` (10 tests, parametrised) — `_translate_woo` cases, identity default, log-once for unregistered ccxt id
- [x] `tests/test_validator.py` (5 tests) — `build_tasks` mapped + unmapped; `classify_symbols` groups by exchange (one `load_markets` per ccxt_id); `MarketLoadError` containment per exchange
- [x] `tests/test_reporter.py` (8 tests) — summary counts, json valid, csv header, text shows/hides LISTED, text shows ccxt_symbol when different
- [x] All 54 tests pass: `pixi run -e dev pytest -q`

## Phase 4b — Filter modes: all FHs / by exchange

- [x] `db.fetch_feed_handlers`: optional `hostname_pattern` + new keyword `exchange_name`; dynamic WHERE; both `None` ⇒ full table scan
- [x] CLI: `--hostname` made optional; added `--exchange-name` (case-insensitive exact match) and `--all` (no filters); explicit mutual exclusion + "at least one of" validation
- [x] CLI logs an active-filter summary line at INFO
- [x] `tests/test_db.py`: added cases for exchange-only, hostname+exchange, and no-filters (3 new); existing hostname test tightened

## Phase 5 — Debug + dev experience

- [x] Created `.vscode/launch.json` with both configs from `implementation.md` §8
- [ ] Manually verify the "TA-TKY-A-41" launch config breaks on breakpoints (requires user)

## Phase 6 — End-to-end verification (success criteria from requirements §6)

- [ ] (1) Live run against `TA-TKY-A-41`; capture output
- [ ] (2) `--output json` is valid JSON
- [ ] (3) `--output csv` header matches spec
- [ ] (4) Non-existent hostname → empty report, exit 0
- [ ] (5) Bad creds → exit 2, no password leak
- [ ] (6) `rg` for known credential strings in new code → no matches
- [ ] (7) SQL is parameterised (manual review + grep)
- [ ] (8) `git check-ignore` confirms `.DBCreds.yaml` ignored
- [ ] (9) `git check-ignore exchange_mapping.yaml` is empty (i.e. tracked)

## Phase 7 — Polish & handover

- [x] `README.md` written: prereqs, setup, creds & mapping files, 12 BINANCE/BINANCEDM/BINANCEDMCOIN usage examples, sample text/JSON/CSV output, CLI reference, exit codes, symbol-translation explanation, "add a new exchange" walkthrough, dev/troubleshooting sections
- [x] Added `BINANCEDMCOIN: binancecoinm` to `exchange_mapping.yaml`
- [x] Added `_translate_perp_suffix_inverse` (FH `<BASE>/<QUOTE>-PERP` → ccxt `<BASE>/<QUOTE>:<BASE>`); covered by a 6-case parametrised test
- [ ] Final pass for type hints + docstrings on the new package
- [ ] Lint clean: `pixi run ruff check fh_symbol_check tests` (if ruff approved)

## Phase 7b — Mapping expansion + translator dispatch refactor

- [x] `exchange_mapping.yaml` expanded to 40 entries; all values validated against the live `ccxt.exchanges` list
  - Newly mapped (29): APEX, ASTER, BACKPACK, BITGET, BITGETDM, BITHUMB, BITSTAMP, BITVAVO, BYBIT, BYBITDM, COINBASE, COINONE, CRYPTOCOM, CRYPTOCOMDM, DYDXV4, GRVT, HUOBICOINSWAP, HYPERLIQUID, KRAKEN, KRAKENDM, LIGHTER, MEXC, OKEX, PARADEX, PHEMEX, PHEMEXDMCOIN, PHEMEXDMT, UPBIT, WHITEBITDM
  - Outstanding (need user input): CBITL, NADO, ARCUS, M2, HLXYZ, THLRIP, THLBMX, HLCASH, HLKM, HLFLX
  - Known-unsupported by ccxt 4.5.60 (left unmapped → will surface as ERROR): DRIFT, DRIFTDM, ENCLAVEDM, SOVERTEX, INJECTIVE, BLUEFIN, BLUEFINPRO, IDEXDM, AVAVERTEX, BERAVERTEX, MNTVERTEX, VERTEX, POLYMARKETINT, POLYMARKETPERPS, KALSHI, HUNDREDX, PYTH, PYTHPRO, BINANCEALPHA
- [x] Refactored translator dispatch from `dict[ccxt_id, fn]` to `dict[exchange_name, fn]` so multiple FH names sharing a ccxt id can each have their own format (resolves 3-way collisions on `htx` and `phemex`)
  - Linear `-PERP` registered for: BINANCEDM, GATEIODM, HUOBIDM, KUCOINDM, PHEMEXDMT, WOO, WOODEX
  - Inverse `-PERP` registered for: BINANCEDMCOIN, HUOBICOINSWAP, PHEMEXDMCOIN
  - Identity (no translator) for spot exchanges (BINANCE, HUOBI, KUCOIN, GATEIO, PHEMEX, BYBIT, BITGET, CRYPTOCOM, …)
- [x] "no translator registered" log demoted from INFO to DEBUG (identity is now the norm for spot exchanges, not an anomaly)
- [x] `validator.build_tasks` updated to dispatch translation by `row.exchange_name`
- [x] `tests/test_symbol_translation.py` rewritten for new dispatch (linear cases, inverse cases, case-insensitivity, spot identity, registry contents, DEBUG-level log assertion)
- [x] `README.md` updated: shipped-mapping snapshot now shows all 40 entries; "How symbol translation works" rewritten around `exchange_name`-keyed dispatch
- [x] Gates clean: ruff, mypy, 63 pytest

### Known follow-ups (not done this iteration)

- [ ] Verify `DYDXV4 → dydx` is correct (ccxt 4.5.60 only ships one `dydx`, presumed to be V4)
- [x] Determine FH symbol convention for BYBITDM / BITGETDM / CRYPTOCOMDM / KRAKENDM / WHITEBITDM and register translators — done in Phases 7e + 7f. Open: KRAKENDM inverse (14 markets) still unresolved; CRYPTOCOMDM/WHITEBITDM/KRAKENDM assumptions noted in 7f and need a real-run sanity check.
- [ ] Decide what to do with the unsupported-by-ccxt set (accept ERROR rows vs. add a sentinel `UNSUPPORTED` status)
- [ ] Identify ARCUS / M2 / THLRIP / THLBMX
- [x] Identified CBITL → `coinbaseinternational` (linear -PERP translator registered; assumes FH stores `<BASE>/USDC-PERP` style symbols — verify with `--exchange-name CBITL -v --show-listed`)
- [x] Identified NADO (https://docs.nado.xyz/) — DEX on Ink L2 by the Kraken team; no `ccxt 4.5.60` support, remains in the unsupported list
- [x] Identified HLXYZ / HLCASH / HLKM / HLFLX as Hyperliquid variants → all mapped to `hyperliquid` (no translator registered yet — FH symbol format for each variant not yet verified; recommend `--exchange-name HLXYZ -v --show-listed` etc. to confirm)
- [ ] THLRIP / THLBMX deferred (user request)

## Phase 7c — Custom (non-ccxt) venue checkers

- [x] New `fh_symbol_check/custom_venues/` package — registry of FH `exchange_name` → checker callables; `validator.build_tasks` routes registered venues to a `custom:<EXCHANGE_NAME>` sentinel `ccxt_id` *before* consulting `exchange_mapping.yaml`, and `_classify_one_exchange` dispatches custom sentinels through a new `_classify_custom_venue`
- [x] First implementation: **NADO** via `https://archive.prod.nado.xyz/v2/symbols`
  - Maps `trading_status == "live"` → LISTED, anything else in {post_only, reduce_only, soft_reduce_only, not_tradable} → INACTIVE, absent → DELISTED
  - Lookup is case-insensitive (FH symbol uppercased before key lookup; Nado keys uppercased on parse)
  - Sets `User-Agent: FindExpiredSymbolsInFH/1.0 (symbol-validation)` because Nado's WAF 403s the default `Python-urllib/X.Y` UA
  - Parser separated from network call (`_parse_symbols`) so tests can use fixture JSON with no network I/O
  - Fetch failures are contained: one ERROR row per task in the offending group, never blows up the whole run
- [x] 9 new parser/fetch tests in `tests/test_custom_venues_nado.py`; 4 new dispatch tests in `tests/test_validator.py`
- [x] Live smoke test against the real endpoint: 82 symbols (70 live, 12 inactive). `BTC-PERP`/`ETH-PERP` → LISTED as expected
- [x] Gates clean: ruff, mypy, 81 pytest

### Known follow-ups for NADO

- [x] Confirmed FH stores `<BASE>/USD-PERP` style (`BNB/USD-PERP`, `ETH/USD-PERP`, …) while Nado expects `<BASE>-PERP`
- [x] Added `_translate_perp_strip_quote` (`BTC/USD-PERP` → `BTC-PERP`); registered for `NADO`; covered by 8-case parametrised test + dispatch test + registry-contents check (9 new tests, 90 total)

## Phase 7f — Remaining `*DM` translators (BITGETDM / WHITEBITDM / CRYPTOCOMDM / KRAKENDM)

- [x] Probed each ccxt id to learn the venue shape:
  - `bitget` — 718 linear (USDT + USDC) + 25 inverse (USD) → same shape as Bybit → registered `_translate_perp_by_quote` for **BITGETDM**
  - `whitebit` — 302 linear, USDT-only; 0 inverse → registered `_translate_perp_suffix` for **WHITEBITDM**
  - `cryptocom` — 307 linear, USD-quoted+USD-settled (`BTC/USD:USD`); 0 inverse → registered `_translate_perp_suffix` for **CRYPTOCOMDM** (linear translator correctly produces `BTC/USD:USD`)
  - `krakenfutures` — 318 linear USD (`BTC/USD:USD`) + 14 inverse USD (`BTC/USD:BTC`) → registered `_translate_perp_suffix` for **KRAKENDM** as default; the 14 inverse markets remain unresolved without FH-side disambiguation
- [x] Tests: per-venue dispatch tests + linear-bucket update for the three linear-only DMs + `_translate_perp_by_quote` dispatch test for BITGETDM (123 total)
- [x] Live end-to-end check against real ccxt markets for all 4: all expected venue-supported quote types resolve to LISTED; non-existent symbols correctly DELISTED
- [x] README "How symbol translation works" updated to (a) list the new linear venues, (b) add BITGETDM to the by-quote category, (c) call out the cryptocom/krakenfutures USD-settled linear pattern (`BTC/USD:USD`), and (d) flag the open KRAKENDM-inverse limitation

### Assumptions made without FH-side confirmation (user skipped the questions)

- **WHITEBITDM**: FH stores `<BASE>/USDT-PERP`. If FH stores USDC or USD instead, those rows will be DELISTED (venue has no such markets).
- **CRYPTOCOMDM**: FH stores `<BASE>/USD-PERP`. If FH stores USDT/USDC, those will be DELISTED (venue has no such markets).
- **KRAKENDM**: FH stores linear `<BASE>/USD-PERP`; the 14 inverse markets are unaddressed.
- **BITGETDM**: FH stores any mix of `<BASE>/USDT-PERP`, `<BASE>/USDC-PERP`, `<BASE>/USD-PERP` — all three translate cleanly via the by-quote translator.

If real runs show DELISTED rows for any of these venues, paste 3 sample FH symbols and we'll adjust the translator.

## Phase 7e — BYBITDM fix

- [x] Diagnosed: BYBITDM had no translator registered → all FH `BTC/USDT-PERP`-style symbols went to ccxt as `BTC/USDT-PERP` (not a valid market key) → every row was DELISTED. ccxt's bybit `load_markets()` returns 3,294 markets including both linear (2,667) and inverse (25) contracts, so the venue side was fine.
- [x] Confirmed with user: BYBITDM contains a mix of `USDT`, `USDC`, and `USD` quotes → needs a translator that branches on the quote currency.
- [x] New primitive: `_translate_perp_by_quote` — `USD` → inverse (`<BASE>/<QUOTE>:<BASE>`), anything else → linear (`<BASE>/<QUOTE>:<QUOTE>`). Registered for `BYBITDM`.
- [x] 10 parametrised translator tests + 1 dispatch test + 1 registry-contents check (12 new tests, 122 total)
- [x] Live end-to-end check against real `ccxt.bybit().load_markets()` markets: all three quote types (`USDT`/`USDC`/`USD`) resolve to LISTED on real symbols; nonsense symbol correctly DELISTED.
- [x] README "How symbol translation works" section updated to document the new by-quote category and the custom-translator section for NADO + POLYMARKETPERPS.
- [ ] Same likely applies to `BITGETDM`, `CRYPTOCOMDM`, `KRAKENDM`, `WHITEBITDM` — verify quote currencies and register `_translate_perp_by_quote` if they also span USD+USDT.

## Phase 7d — Polymarket variants

- [x] **POLYMARKETPERPS** added as the second custom venue:
  - Endpoint: `GET https://api.perpetuals.polymarket.com/v1/info/instruments` (no auth)
  - Response: JSON list of 9 instruments (BTC-USD, ETH-USD, SOL-USD, GOLD-USD, SILVER-USD, WTIOIL-USD, SP500-USD, NAS100-USD, SPCX-USD); no `active`/`trading_status` flag, so the venue only produces LISTED/DELISTED — never INACTIVE
  - Symbol translation: `<BASE>/USDC-PERP` → `<BASE>-USD`; per-base remap `WTI → WTIOIL` (FH naming differs from venue)
  - 9 fetch/parse tests (`tests/test_custom_venues_polymarket_perps.py`) + 12 translator cases + dispatch test + registry-contents check (21 new tests, 111 total)
  - Live smoke test against real endpoint: all 9 venue instruments resolved cleanly; `XAU/USDC-PERP` → DELISTED (Polymarket calls that asset `GOLD`, not `XAU`)
- [x] Confirmed FH does **not** use the plain `POLYMARKET` exchange_name; only POLYMARKETPERPS and POLYMARKETINT are in use
- [~] **POLYMARKETINT — parked.** FH symbols are abbreviated, FH-internal identifiers (e.g. `DEMWINHOUSE2026/USDC`, `WINNINGCONFERENCE/USDC`, `NO2SEEDWIN/USDC`). They do not match any public Polymarket API:
  - `gamma-api.polymarket.com` uses long slugs (`will-democrats-win-…`)
  - `api.prod.polymarketexchange.com` (docs.polymarket.us) uses kebab-case structured ids (`ewc-ushse-…-2026-11-03`)
  - User indicated there is an FH-side local mapping table (`DEMWINHOUSE2026` → real polymarket slug/condition-id). Resuming this needs that table + a decision on which downstream endpoint to validate against. Until then, POLYMARKETINT rows will surface as ERROR.

## Phase 8 — Suggested follow-ups (not in this iteration)

Tracked here so they don't get lost:

- [ ] Persistent on-disk cache of `load_markets()` keyed by exchange + day
- [ ] ANSI colour in text output when stdout is a TTY
- [ ] Pre-commit hook that blocks commits containing the literal DB password
- [ ] `mysql_select_query.py`: context-manager pattern + connect/read timeouts
- [ ] Schema-drift guard at startup (`DESCRIBE crypto_db.fh_config`)
- [ ] CI workflow that runs `pytest`, `mypy`, `ruff`
