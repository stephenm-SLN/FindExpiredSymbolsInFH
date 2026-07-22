# Task List — Feed Handler Symbol Validity Checker

Tick items as they complete. New tasks may be appended as the project progresses.

## Open items — quick index

All still-open work, grouped by area. Details (and their siblings, done and open) live in the phases below.

### Symbol translation / venue coverage
- [ ] Verify `DYDXV4 → dydx` is the correct ccxt id (Phase 7b — ccxt 4.5.60 ships only one `dydx`, presumed V4)
- [ ] Decide policy for ccxt-unsupported venues (accept `ERROR` rows or introduce a sentinel `UNSUPPORTED` status): `DRIFT`, `DRIFTDM`, `ENCLAVEDM`, `SOVERTEX`, `INJECTIVE`, `BLUEFIN`, `BLUEFINPRO`, `IDEXDM`, `AVAVERTEX`, `BERAVERTEX`, `MNTVERTEX`, `VERTEX`, `KALSHI`, `HUNDREDX`, `PYTH`, `PYTHPRO`, `BINANCEALPHA` (Phase 7b)
- [ ] Identify FH `ARCUS`, `M2`, `THLRIP`, `THLBMX` — no ccxt id known, no custom-venue endpoint identified (Phases 7b, 7d)
- [ ] Resume `POLYMARKETINT`: needs the FH-side abbreviation → real slug/condition-id mapping table AND a decision on the downstream endpoint to validate against (`gamma-api.polymarket.com` vs `api.prod.polymarketexchange.com`). Until then, its rows stay `ERROR` (Phase 7d)
- [ ] Verify assumptions with real FH samples for `WHITEBITDM` / `CRYPTOCOMDM` / `KRAKENDM` linear translators; the 14 `KRAKENDM` inverse markets are also unresolved without FH-side disambiguation (Phase 7f)
- [ ] Verify HL family (`HLXYZ`, `HLCASH`, `HLKM`, `HLFLX`) — mapped to `hyperliquid` but FH symbol format for each variant unverified; recommend `--exchange-name HLXYZ -v --show-listed` etc. (Phase 7b)

### End-to-end / handover
- [ ] Manually verify the `.vscode/launch.json` "TA-TKY-A-41" config breaks on breakpoints (Phase 5 — requires user)
- [ ] Live-run verification checklist items 1–9 in Phase 6 (tests + real deploy cover most of this already; formal tick still open)
- [ ] Final pass for type hints + docstrings on the new package (Phase 7)

### Packaging & deploy
- [ ] Publish wheel to the Nexus PyPI mirror so `pip install find-expired-symbols` works without a git remote (Phase 8, deferred)
- [ ] CI job to build + publish the wheel on tag (Phase 8, deferred)
- [ ] Ship a systemd `.service` + `.timer` example (Phase 8, deferred — user schedules externally)
- [ ] Automate the "bootstrap `/opt/pyhost` + install `/opt/find-expired-symbols` + set permissions" pass into a helper script or Ansible role (Phase 8b)

### Nice-to-have follow-ups (Phase 9)
- [ ] Persistent on-disk cache of `load_markets()` keyed by exchange + day
- [ ] ANSI colour in text output when stdout is a TTY
- [ ] Pre-commit hook that blocks commits containing the literal DB password
- [ ] `mysql_select_query.py`: context-manager pattern + connect/read timeouts
- [ ] Schema-drift guard at startup (`DESCRIBE crypto_db.fh_config`)
- [ ] CI workflow that runs `pytest`, `mypy`, `ruff`

---

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
- [x] Extend `.gitignore` for dev/packaging noise: `.mypy_cache/`, `.ruff_cache/`, `build/`, `dist/`, `*.egg-info/` (added alongside the Phase-8 packaging work)
- [x] Verify ignore rules by inspection (rules confirmed by reading `.gitignore` — `.DBCreds.*` matches the yaml file, `fh_symbol_check/data/exchange_mapping.yaml` matches no rule and is tracked)
- [x] Add `python=>=3.10,<3.14`, `pymysql`, `pyyaml` to `[dependencies]` and `ccxt` to `[pypi-dependencies]` (conda-forge has no current `ccxt`); lock with `pixi install`
- [x] Add `pytest`, `mypy`, `ruff` to `[feature.dev.dependencies]` and create `dev` environment
- [x] Smoke test: `pixi run --environment dev python -c "import pymysql, ccxt, yaml"` → OK (python 3.13.14, pymysql 2.2.8, ccxt 4.5.60, yaml 6.0.3)
- [x] `git init` so `.gitignore` actively protects creds before any commit

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

## Phase 7h — `--symbol` lookup

- [x] New CLI flag `--symbol <STR>` to find every occurrence of an exact symbol across the scanned FH set
- [x] Match semantics: **case-sensitive, full-string equality**, checked against both `original_symbol` (FH/cover_names form) AND `ccxt_symbol` (translated venue form) — caller can pass either form
- [x] New `filter_by_symbol(tasks, errors, symbol)` in `validator.py`; applied after `build_tasks` but before `classify_symbols` so we skip wasted `load_markets` calls on tasks we'd throw away
- [x] Mutually-permissive: `--symbol` alone is enough (implies `--all`); composes with `--hostname` / `--exchange-name` / `--errors-only` / `--exchange-grouping`. The `--all` vs `--hostname/--exchange-name` exclusivity is preserved
- [x] Validation gate widened to "specify --hostname, --exchange-name, --symbol, or --all" (one of the four required)
- [x] Auto-enable `--show-listed` in text output when `--symbol` is set (the tool is being used as a search — every occurrence should be visible regardless of status)
- [x] INFO log line: `--symbol 'X': matched N/M task(s) and K/J unmappable-exchange error(s)`; WARNING line if 0 occurrences found
- [x] `_describe_filters` updated to include the `symbol=…` slug
- [x] 8 new pytest cases in `test_validator.py`: matches original side, matches ccxt side, case-sensitivity respected, exact-not-substring, unmappable errors filtered too, no-match returns empty, no mutation of inputs, same-symbol-on-multiple-FHs all survive
- [x] New `tests/test_cli.py` (11 cases) locking down argparse-level acceptance of `--symbol` in all permitted combinations + preservation of the pre-existing `--all`-exclusivity rules
- [x] README: Example 14 added with stdout / FH-form / ccxt-form / combinations, plus CLI reference row + updated "at least one of" line
- [x] Gates clean: ruff, mypy, 153 pytest

### Phase 7h.1 — extend `--symbol` to accept one-or-more values (OR semantics)

- [x] Argparse: switched `--symbol` to `nargs="+"` with `metavar="SYMBOL"` so `--help` renders `--symbol SYMBOL [SYMBOL ...]`. Argparse rejects `--symbol` with no values (nargs='+' contract). Space-separated invocation:
  ```
  pixi run python find_expired_symbols.py --symbol IP/USDT-PERP BTC/USDT-PERP ETH/USDT-PERP
  ```
- [x] `args.symbol` is now `list[str] | None` (was `str | None`). All existing bool-truthiness checks (`if args.symbol:`, `bool(args.symbol)`) continue to work.
- [x] `filter_by_symbol(tasks, errors, symbols: Iterable[str])`: match logic is `original_symbol in wanted or ccxt_symbol in wanted` where `wanted = set(symbols)`. **OR semantics** — a row is kept if any of the provided values matches on either side. Duplicate values in the input are deduplicated by the set (no double-counting). Empty iterable filters everything out.
- [x] `_describe_filters` renamed `symbol → symbols`; renders `symbols=['IP/USDT-PERP', 'BTC/USDT-PERP']` in the filter-summary INFO line.
- [x] Test coverage: existing 8 `filter_by_symbol` tests migrated to the list form; **4 new tests** added — multi-value OR semantics, mixed FH-form/ccxt-form in one call, duplicate deduplication, empty-iterable behaviour.
- [x] `tests/test_cli.py`: refreshed to expect `args.symbol == ["IP/USDT-PERP"]` (list of one) instead of the bare string; added tests for multiple space-separated values, greedy-stop at the next flag, and rejection of `--symbol` with no values.
- [x] Docs sync: README Example 14, CLI reference, `design.md` (§3.6 signature + arrow-diagram box + CLI §3.8), `implementation.md` (CLI shape + `filter_by_symbol` signature + verification steps), `requirements.md` (inputs, functional requirements, success criteria).
- [x] Gates clean: ruff, mypy, **165 pytest** (was 158; +7 new).

## Phase 7i — OKEX fix (mixed spot + linear + inverse under one exchange_name)

- [x] Diagnosed: `OKEX → okx` was in `exchange_mapping.yaml` but had **no translator registered**. FH stores OKEX symbols in three shapes under a single `exchange_name`: spot `<BASE>/<QUOTE>` (no suffix), linear perps `<BASE>/USDT-PERP`, and inverse perps `<BASE>/USD-PERP`. Perps went to ccxt as-is (`ETH/USDT-PERP`, `BTC/USD-PERP`) → not valid market keys → every perp row was `DELISTED`. User reported: `ETH/USDT-PERP` and `BTC/USD-PERP` falsely `DELISTED`.
- [x] Live probe of `ccxt.okx().load_markets()`: 411 linear USDT-margined perps (`<BASE>/USDT:USDT`) + 15 inverse USD-margined perps (`<BASE>/USD:<BASE>`). Same mixed shape as BYBITDM/BITGETDM.
- [x] Registered `OKEX: _translate_perp_by_quote` (existing primitive — no new code needed). Spot symbols (no `-PERP` suffix) fall through untouched and match ccxt-okx spot keys directly, so the single translator covers all three shapes.
- [x] Verified live: `ETH/USDT-PERP → ETH/USDT:USDT` (present + active), `BTC/USD-PERP → BTC/USD:BTC` (present + active).
- [x] Extended `_translate_perp_by_quote` docstring to note the spot-passthrough behaviour makes it viable for venues that mix spot + perps under one `exchange_name`.
- [x] 1 new dispatch test (`test_translate_dispatches_for_okex`) covering both perp flavours and spot passthrough; added `OKEX` to the `expected_by_quote` set in the registry-contents assertion.
- [x] README "How symbol translation works" — added `OKEX` to the by-quote list and called out the shared spot+perp `exchange_name` pattern.
- [x] Gates clean: ruff, mypy, **166 pytest** (was 165; +1 new).

## Phase 7j — Repeater support (`crypto_db.repeater_feeds`)

- [x] User request: run the same delisted-symbol checks against the repeater layer using `SELECT hostname, app_name, exchange_name, instruments FROM crypto_db.repeater_feeds`.
- [x] Locked design decisions with the user before touching code:
  - `instruments` is a comma-separated CSV in the same shape as `fh_config.cover_names` — reuse the parser.
  - `repeater_feeds.exchange_name` uses the same value set as `fh_config.exchange_name` — reuse `exchange_mapping.yaml` and the same translators.
  - CLI shape: scan both by default; add `--source {fh, rp, both}` narrowing flag (default `both`).
  - Row identity: reuse the existing `fh_name` slot for the repeater `app_name`; add a `source` field to disambiguate — no rename churn.
  - Text detail: two labelled sections, `--- Feed handlers ---` then `--- Repeaters ---`.
  - `--exchange-grouping`: single unified table with an added `source` column (so `(exchange, source)` is the group key).
- [x] `models.py`: added `SourceKind = Literal["fh","repeater"]`. All three producer dataclasses (`FeedHandlerRow`, `ResolvedTask`, `SymbolResult`) now carry `source: SourceKind = "fh"` and `service_id: int | None = None`. Kwargs-only constructions everywhere → no breaking positional changes.
- [x] `db.py`: added `fetch_repeaters(creds, hostname_pattern=None, *, exchange_name=None)` alongside `fetch_feed_handlers`. Both share `_compose_sql` (same LIKE / UPPER filter contract) and `_run_query` (same DBError wrapping, message scopes the failing table). Repeater rows land in the same `FeedHandlerRow` type — `app_name` in the `fh_name` slot, `service_id=None`, `source="repeater"`.
- [x] `validator.py`: `build_tasks` propagates `row.source` verbatim to `ResolvedTask` and to the unknown-exchange ERROR rows. `_classify_one_exchange`, `_classify_custom_venue`, and `_error_result` propagate `t.source` into `SymbolResult`. Fully source-agnostic — the ccxt / custom-venue dispatch is untouched.
- [x] `reporter.py`:
  - Added `summary_by_repeater(results)` (source="repeater" only); `summary_by_fh` now filters to source="fh". Shared factoring via `_summary_by_producer`.
  - `summary_by_exchange` group key = `(exchange_name, source)`; `ExchangeSummary` gained a `source` field.
  - Text: two labelled detail sections (`--- Feed handlers ---` / `--- Repeaters ---`, elided when empty). Two summary tables — `Summary by feed handler` (header `fh_name`) and `Summary by repeater` (header `app_name`), also elided when empty (single-source runs render exactly one). Under `--exchange-grouping` a single unified table with an added `source` column; TOTAL row leaves the source cell blank.
  - CSV: `source` added as the first column of `_CSV_FIELDS`. JSON: `source` + nullable `service_id` flow through via `asdict`.
  - `keep_fhs_with_errors` identity = `(source, hostname, fh_name, exchange_name)` so a repeater ERROR doesn't drag in a same-named FH.
- [x] `cli.py`: added `--source {fh, rp, both}` (default `both`). `main()` now calls `fetch_feed_handlers` and/or `fetch_repeaters` per the flag, with per-table DBError messages and per-table "no rows matched" WARNINGs. Both row lists are concatenated before `build_tasks`. Log lines that talked about "feed handlers" were widened to "producers" where the source is ambiguous (`--errors-only`, `--symbol 0-occurrences`).
- [x] Tests: 20 new (186 total, was 166).
  - `test_db.py` — `fetch_repeaters` SQL statement, bound filters, `instruments` CSV parsing, source stamp, empty-instruments behaviour, DBError scoping.
  - `test_validator.py` — repeater-row build_tasks propagation, unknown-exchange ERROR propagation, classify_symbols end-to-end source propagation via mocked `load_exchange_markets_safe` / `classify`.
  - `test_reporter.py` — `summary_by_fh` / `summary_by_repeater` source filtering, `summary_by_exchange` (exchange, source) group key, two-labelled-detail-sections split with strict ordering, two-summary-tables with correct `fh_name` / `app_name` headers, single-source runs render only one table, `--exchange-grouping` has a `source` column with both fh and repeater rows for the same exchange, `keep_fhs_with_errors` disambiguation.
  - `test_cli.py` — `--source` accepts `fh`/`rp`/`both`, defaults to `both`, rejects unknown, composes with `--all` + `--symbol`.
  - Existing tests: 3 pre-existing repaired (CSV header + `ExchangeSummary` constructor + `--exchange-grouping` TOTAL row cells).
- [x] Gates clean: ruff, mypy, **186 pytest** (was 166; +20 new).
- [x] Docs sync: `requirements.md`, `design.md`, `implementation.md`, `README.md`, `task.md`.

## Phase 7g — `--exchange-grouping` summary

- [x] New CLI flag `--exchange-grouping` (text-only): replaces the per-FH summary table with a per-exchange one
- [x] `ExchangeSummary` dataclass (`exchange_name`, `feed_handlers`, `active`, `inactive`, `delisted`, `error`, `total_dead`, `total`); `feed_handlers` counts distinct `(hostname, fh_name)` pairs
- [x] `summary_by_exchange(results)` aggregator, sorted alphabetically for stable output
- [x] Per-symbol detail block is suppressed when `--exchange-grouping` is set AND `--output-file` is **not** set (clean stdout for fleet stats, full detail when writing to file for triage)
- [x] JSON/CSV unaffected (verified by tests asserting identical output with/without the flag)
- [x] Table-rendering core extracted into a shared `_render_psql_table` helper; both `_render_fh_summary_table` and the new `_render_exchange_summary_table` go through it (eliminates the duplicated box-drawing logic)
- [x] 11 new pytest cases (aggregation, sort order, suppress-details on/off, JSON/CSV unaffected, totals row arithmetic, replacement-not-augmentation, empty input)
- [x] README: Example 14 added (with both stdout and `--output-file` forms) + CLI reference table row
- [x] Gates clean: ruff, mypy, 134 pytest

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

## Phase 8 — Packaging as a pip-installable console script

Ship the tool as a real Python package so operators can `pip install` it
into a venv on a Linux server and get a `find-expired-symbols` command
on `$PATH`. External schedulers (Airflow, Jenkins, cron, systemd timers)
invoke it like any other shell command.

- [x] Moved `exchange_mapping.yaml` → `fh_symbol_check/data/exchange_mapping.yaml` (ships inside the wheel as package data; kept out of `pip install` boilerplate on the server).
- [x] Added `DEFAULT_EXCHANGE_MAP` in `fh_symbol_check/cli.py`; resolves via `importlib.resources.files("fh_symbol_check")` so both source-tree and installed-wheel invocations pick up the same file.
- [x] Added `--exchange-map` default = `DEFAULT_EXCHANGE_MAP` (operator can still override with an explicit path). Also removed the hard-coded `prog="find_expired_symbols.py"` so argparse infers program name from `sys.argv[0]` — installed users see `find-expired-symbols`, source-tree users still see `find_expired_symbols.py`.
- [x] Added `run() -> int` in `fh_symbol_check/cli.py` that wraps `main()` with KeyboardInterrupt (→ exit `130`) and generic exception (→ exit `2`, `logger.exception`) handling. Both invocation styles go through it.
- [x] Simplified `find_expired_symbols.py` to a 3-line wrapper around `fh_symbol_check.cli.run`.
- [x] Created `pyproject.toml`:
  - `[build-system]` setuptools + wheel
  - `[project]` metadata (name `find-expired-symbols`, `requires-python = ">=3.10,<3.14"`, runtime deps `pymysql`/`ccxt`/`pyyaml`)
  - `[project.scripts]` → `find-expired-symbols = fh_symbol_check.cli:run`
  - `[tool.setuptools] py-modules = ["check_delisted_symbol", "mysql_select_query"]` (top-level helpers ship alongside the package)
  - `[tool.setuptools.packages.find]` including `fh_symbol_check*`, excluding `tests*`
  - `[tool.setuptools.package-data] fh_symbol_check = ["data/*.yaml"]`
- [x] Added `linux-64` to `pixi.toml` `platforms` so the pixi env resolves on the target Linux host too.
- [x] `tests/test_packaging.py` — 5 smoke checks:
  - `run` is importable and is the exact callable pyproject points at
  - `--help` succeeds and mentions every filter flag operators depend on
  - `DEFAULT_EXCHANGE_MAP` exists on disk, lives inside `fh_symbol_check`, and is at `data/exchange_mapping.yaml`
  - the bundled mapping loads as a `{str: str}` YAML dict
  - exit-code constants are frozen at `0/1/2/130`
- [x] End-to-end smoke on real wheel:
  - `python -m build --wheel` produces `find_expired_symbols-0.1.0-py3-none-any.whl` (includes `fh_symbol_check/data/exchange_mapping.yaml`)
  - `pip install <wheel>` into a fresh venv installs the `find-expired-symbols` console script
  - `find-expired-symbols --help` works from any cwd
  - `DEFAULT_EXCHANGE_MAP` points at `<venv>/lib/python3.13/site-packages/fh_symbol_check/data/exchange_mapping.yaml` (not the source tree)
  - `>=3.10` guard correctly rejects Python 3.9
- [x] README "Deploying on a Linux server" section — venv install (from checkout or from `git+ssh://…`), where to place `DBCreds.yaml`, external-scheduler invocation recipe, exit-code reference, upgrade path.
- [x] Full gate re-run: 163 tests passing (158 pre-packaging + 5 new), `ruff check .` clean, `mypy fh_symbol_check tests` clean.

Deferred (documented, out of scope for v1):

- [ ] Publish wheel to the Nexus PyPI mirror (`https://nexus.selini.tech/repository/pypi-hosted/simple`) so `pip install find-expired-symbols` works without a git remote — user picked "cross that bridge later" for the update flow.
- [ ] CI job to build + publish the wheel on tag.
- [ ] Ship a systemd `.service` + `.timer` example — user is scheduling externally (Airflow/Jenkins/etc.), so this isn't in v1.

## Phase 8b — Multi-user shared pixi deploy (Linux server)

Real-world server deploy uncovered several host-Python and packaging
gaps that the plain `python -m venv + pip install <wheel>` recipe
couldn't handle. Reworked into a shared pixi-workspace model so one
install serves both interactive users and the scheduler, and neither
side depends on the system Python.

- [x] `deploy/shared-workspace-pixi.toml` — template pixi manifest checked into the repo. Declares `python>=3.11,<3.14` + `cryptography = "*"` under `[dependencies]` (conda-forge), the local wheel under `[pypi-dependencies]`, and a `[tasks] find-expired-symbols` entry that prefills `--creds-file /opt/find-expired-symbols/.DBCreds.yaml`.
- [x] Diagnosed and worked around the `cryptography` `manylinux_2_28` vs. conda-forge Python `manylinux_2_26` mismatch by pulling `cryptography` from conda-forge instead of PyPI; ccxt (transitively pulling `cryptography`) accepts the conda-forge build via pixi's conda↔pypi name mapping.
- [x] Confirmed ccxt itself has no conda-forge build and must stay on PyPI (via the wheel's own metadata) — documented in-line in the template + troubleshooting cheatsheet.
- [x] `deploy.md` — 11-part runbook: prerequisites → build wheel → ship → bootstrap Python via `/opt/pyhost` pixi env → shared install workspace at `/opt/find-expired-symbols/` → creds → permissions → optional `/usr/local/bin` wrapper → smoke test as a non-admin user → scheduler wiring (Airflow / Jenkins / cron + logrotate) → upgrading → uninstall/rollback → troubleshooting cheatsheet → directory map.
- [x] Consolidated `.DBCreds.yaml` inside the install workspace (`/opt/find-expired-symbols/.DBCreds.yaml`) instead of `/etc/find-expired-symbols/` — matches the dev-repo convention (creds file next to `find_expired_symbols.py`), keeps the whole deploy under one tree, and simplifies uninstall to a single `rm -rf`.
- [x] Documented the multi-user permissions model: install tree `755`/`644`, binaries in `.pixi/envs/default/bin/` restored to `755`, creds file locked to `640 root:$FES_GROUP` after the blanket chmod so it isn't world-readable.
- [x] Scheduler contract locked: absolute-path invocation → `.pixi/envs/default/bin/find-expired-symbols`; exit codes `0/1/2/130` frozen by `tests/test_packaging.py::test_exit_codes_exported`.
- [x] `README.md` — "Deploying on a Linux server" section rewritten to point at `deploy.md` for the runbook and show the pixi-based pattern inline.
- [x] Live end-to-end deploy verified on the target host (build wheel on dev box → scp → `pixi install` on the server → `pixi run find-expired-symbols --help` → real DB smoke test).

Deferred out of Phase 8b:

- [ ] Provide a small helper script or Ansible role that reproduces the "bootstrap `/opt/pyhost` + install `/opt/find-expired-symbols` + set permissions" pass in one command; the manual runbook works and rerolling into automation isn't urgent.

## Phase 9 — Suggested follow-ups (not in this iteration)

Tracked here so they don't get lost:

- [ ] Persistent on-disk cache of `load_markets()` keyed by exchange + day
- [ ] ANSI colour in text output when stdout is a TTY
- [ ] Pre-commit hook that blocks commits containing the literal DB password
- [ ] `mysql_select_query.py`: context-manager pattern + connect/read timeouts
- [ ] Schema-drift guard at startup (`DESCRIBE crypto_db.fh_config`)
- [ ] CI workflow that runs `pytest`, `mypy`, `ruff`
