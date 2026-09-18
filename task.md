# Task List — Feed Handler Symbol Validity Checker

Tick items as they complete. New tasks may be appended as the project progresses.

## Open items — quick index

All still-open work, grouped by area. Details (and their siblings, done and open) live in the phases below.

### Symbol translation / venue coverage
- [ ] Verify `DYDXV4 → dydx` is the correct ccxt id (Phase 7b — ccxt 4.5.60 ships only one `dydx`, presumed V4)
- [ ] Decide policy for ccxt-unsupported venues (accept `ERROR` rows or introduce a sentinel `UNSUPPORTED` status): `DRIFT`, `DRIFTDM`, `ENCLAVEDM`, `BLUEFIN`, `BLUEFINPRO`, `IDEXDM`, `KALSHI`, `HUNDREDX`, `PYTH`, `PYTHPRO`, `BINANCEALPHA` (Phase 7b). ~~`VERTEX`, `AVAVERTEX`, `BERAVERTEX`, `MNTVERTEX`, `SOVERTEX`~~ resolved via custom venue in Phase 7n. ~~`INJECTIVE`~~ resolved via custom venue in Phase 7p.
- [ ] Identify FH `M2`, `THLRIP`, `THLBMX` — no ccxt id known, no custom-venue endpoint identified (Phases 7b, 7d). ~~`ARCUS`~~ resolved via custom venue in Phase 7s.
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
- [x] Ship a systemd `.service` example (Phase 8d — API service ships `deploy/find-expired-symbols.service`, user unit); a `.timer` for the CLI is still deferred (user schedules externally)
- [ ] Automate the "create `$FES_DIR` + rsync manifest/wheel + first-run permissions + systemd unit install" pass into a helper script or Ansible role (Phase 8c/8d) — deferred; the manual flow works fine

### Nice-to-have follow-ups (Phase 9)
- [ ] Persistent on-disk cache of `load_markets()` keyed by exchange + day
- [ ] ANSI colour in text output when stdout is a TTY
- [ ] Pre-commit hook that blocks commits containing the literal DB password
- [ ] `mysql_select_query.py`: context-manager pattern + connect/read timeouts
- [ ] Schema-drift guard at startup (`DESCRIBE crypto_db.fh_config`)
- [ ] CI workflow that runs `pytest`, `mypy`, `ruff`
- [ ] API: persistent job storage, cancellation endpoint, WebSocket/SSE push, auth front, rate-limiting, downloadable exports (all from Phase 8d)

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
  - Outstanding (need user input): CBITL, M2, HLXYZ, THLRIP, THLBMX, HLCASH, HLKM, HLFLX
  - Custom-venue backed (no longer ERROR by default): NADO, POLYMARKETPERPS (Phase 7d), VERTEX, AVAVERTEX, BERAVERTEX, MNTVERTEX, SOVERTEX (Phase 7n), INJECTIVE (Phase 7p), RHLIGHTER (Phase 7q), ARCUS (Phase 7s), ONDOPERPS (Phase 7t).
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
- [ ] Identify M2 / THLRIP / THLBMX
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

## Phase 7k — SSL trust store via `truststore` (fixes UPBIT/KRAKEN/… all-ERROR on corp networks)

- [x] User report: every `UPBIT` symbol was landing as `ERROR`.
- [x] Diagnosed live: `ccxt.upbit().load_markets()` fails with `SSL: CERTIFICATE_VERIFY_FAILED — unable to get local issuer certificate`; `openssl s_client -connect api.upbit.com:443` shows the chain re-signed by `CN=Zscaler Intermediate Root CA (zscalerthree.net)` / `CN=Zscaler Root CA`. The pixi conda-forge trust store at `.pixi/envs/<env>/ssl/cert.pem` is the Mozilla bundle only and doesn't include the corp Zscaler root, so verify fails. ccxt bubbles it up as `NetworkError`; the validator (per its exchange-wide catch in `_classify_one_exchange`) marks every task in the group `ERROR` with `detail="load_markets failed: upbit GET https://api.upbit.com/v1/market/all"`. Same symptom on `KRAKEN` from the same machine; `BINANCE` is unaffected because Zscaler bypasses that host.
- [x] Fix (per user pick, option C): added `truststore>=0.10` as a **runtime** dependency (present on both conda-forge and PyPI, requires Python 3.10+ which matches our pin) and call `truststore.inject_into_ssl()` at the top of `fh_symbol_check.cli.run()`. Injection monkey-patches `ssl.create_default_context` to use the OS-native trust store (macOS Keychain / Linux system CA bundle / Windows cert store), so any corp root IT has already installed system-wide is picked up automatically.
  - `pyproject.toml`: `truststore>=0.10` added to `[project.dependencies]` (so the wheel declares the dep for downstream `pip install`s).
  - `pixi.toml`: `truststore = ">=0.10"` moved into `[dependencies]` (not `[feature.dev.dependencies]`) so both dev and default pixi envs get it.
  - `deploy/shared-workspace-pixi.toml`: `truststore = ">=0.10"` added to `[dependencies]` next to `cryptography` — the server workspace resolves it from conda-forge like the rest of the C-adjacent stack. Doc comment explains the corp-egress motivation.
  - `fh_symbol_check/cli.py`: new private `_install_system_trust_store()` helper called from `run()`. Best-effort: any exception (missing package, incompatible Python, injection error) is caught, logged as WARNING, and the tool falls back to the bundled bundle rather than crashing.
- [x] Verified live: with injection active, `ccxt.upbit().load_markets()` = OK (807 markets), `ccxt.kraken().load_markets()` = OK (1430 markets), `ccxt.binance().load_markets()` still = OK (4552 markets).
- [x] 3 new pytest cases in `test_cli.py`:
  - `test_run_calls_truststore_inject_into_ssl_once` — `run()` invokes `truststore.inject_into_ssl` exactly once per invocation.
  - `test_run_survives_missing_truststore` — a raising `inject_into_ssl` is downgraded to a WARNING log; run() still returns cleanly.
  - `test_run_returns_operational_failure_on_unhandled_exception` — the truststore step does not swallow unrelated exceptions from `main()`; the existing catch-all → EXIT_OPERATIONAL_FAILURE contract is preserved.
- [x] Gates clean: ruff, mypy, **189 pytest** (was 186; +3 new).
- [x] Docs: README troubleshooting row (all-ERROR on one exchange), deploy.md troubleshooting row (with the concrete server-side CA bundle path + verification `awk` snippet), task.md this entry.

Notes for reviewers:
- The `truststore` package is maintained by the PSF/pip team; pip itself uses it since 24.2 for the same reason. It's a small pure-Python monkey-patch, not a C extension.
- The fix is entirely transparent — no CLI flag, no config file. The only observable difference in a corp-network run is that previously-erroring exchanges now load. Runs on machines whose OS trust store is already the Mozilla bundle (i.e. no corp SSL inspection) behave identically to before.
- If a downstream user really wants to disable it (e.g. to debug), the fallback path is auto-triggered on `ImportError` — so uninstalling truststore is a valid escape hatch.

## Phase 7l — Browser User-Agent for ccxt + ERROR-detail sanitisation

- [x] Follow-up to 7k: after truststore let HTTPS through end-to-end, Zscaler began **inspecting** requests and replying with an HTML block page for hosts categorised as Cryptocurrency (Upbit, Kraken, OKX). The trigger was the User-Agent — ccxt's underlying `requests` client defaults to `python-requests/<ver>`, which Zscaler classifies as non-browser. The block-page HTML then landed verbatim in the report's `detail` column for every ERROR row (ccxt wraps the HTML into `NetworkError`/`ExchangeError` messages).
- [x] Fix 1 — modern browser UA: `check_delisted_symbol.load_exchange_markets_safe` now sets `exchange.headers["User-Agent"]` to a Chrome 126 desktop UA before calling `load_markets()`. Hard-coded string (no auto-refresh), extensive comment explains the corp-proxy motivation. Exchange WAFs accept it since it matches their own web trading UI, and it's a no-op on networks without SSL inspection.
- [x] Fix 2 — defense-in-depth `_sanitize_error_detail(text)` in `fh_symbol_check.validator`: collapses runs of whitespace (incl. newlines) to single spaces and caps at `_MAX_ERROR_DETAIL_LEN = 500` chars with a trailing `…`. Applied inside `_error_result` so every ERROR-row `detail` (from any of the three error paths: unknown exchange, `MarketLoadError`, unexpected `Exception`) is guaranteed clean. LISTED/INACTIVE/DELISTED details are untouched (they're already short/controlled).
- [x] 8 new pytest cases:
  - New `tests/test_check_delisted_symbol.py`: `load_exchange_markets_safe` sets browser UA (starts with `Mozilla/5.0`, contains `Chrome/`); handles `headers=None`; rejects unknown exchange; wraps ccxt errors as `MarketLoadError`.
  - Additions to `tests/test_validator.py`: `_sanitize_error_detail` collapses whitespace, caps length with `…`, is idempotent on short input; `_error_result` end-to-end proof with a giant HTML blob → single-line ≤ 500-char detail.
- [x] Verified live from the same corp-proxy env: `upbit` = 807 markets, `kraken` = 1430, `binance` = 4552, `okx` = 4172 — all four exchanges now load with the browser UA.
- [x] Gates clean: ruff, mypy, **197 pytest** (was 189; +8 new).

## Phase 7m — Zscaler URL-category block detection (HUOBI / HTX)

- [x] User report: every HUOBI / HUOBIDM symbol was ERROR again. Different failure mode from Upbit (7l): Zscaler's **Web Access Control** (template `wac_block.html`, HTTP **403** — not 200 + browser-check) denies outbound traffic to `api.huobi.pro` / `api.htx.com` by URL category, regardless of User-Agent. HTX/Huobi is US-sanctioned and blocked by default on many enterprise Zscaler tenants.
- [x] Nothing to fix in transport — the corp proxy is blocking the host outright. But the ~14 KB HTML block body was landing (sanitised to 500 chars) in every ERROR row's `detail`, making reports and logs unreadable.
- [x] Added `_rewrite_proxy_block(ccxt_id, err_text) -> str | None` in `fh_symbol_check.validator`. Extensible tuple `_PROXY_BLOCK_SENTINELS` maps a sentinel substring to a vendor label; the first hit wins. When a sentinel matches, the raw message is replaced with:
  ```
  blocked by corporate proxy (Zscaler wac_block.html); <ccxt HTTP summary line> — contact IT to allowlist this exchange host
  ```
  The head token is preserved from ccxt's own error prefix (`<ccxt_id> <METHOD> <URL> <status> <reason>`) so operators still see host + status code without any HTML.
- [x] Wired into the `MarketLoadError` catch in `_classify_one_exchange`: WARNING log line uses the hint (cleaner scheduled-run logs), full raw text is kept at DEBUG level (`logger.debug("full underlying error for %s: %s", ccxt_id, raw)`) for when the hint isn't enough. Non-matching errors flow through unchanged so we don't hide unrelated failures.
- [x] Also updated `check_delisted_symbol.load_exchange_markets_safe` — no code change needed there; sentinel detection lives at the report boundary in the validator so a single place owns the transformation and the raw error is unmutated for anyone else consuming `MarketLoadError`.
- [x] 4 new pytest cases in `tests/test_validator.py`:
  - `_rewrite_proxy_block` recognises Zscaler `wac_block.html` and includes host + status in the hint.
  - `_rewrite_proxy_block` returns `None` for unrelated network errors (rate-limit, connection reset) — so we don't accidentally rewrite real failures.
  - Falls back to `ccxt_id` as the head token when ccxt didn't prepend a summary line.
  - End-to-end via `classify_symbols`: mocked loader raises `MarketLoadError` with a 14-KB `wac_block.html` payload → resulting SymbolResult.detail contains the clean hint, no HTML tags, ≤ 500 chars.
- [x] Verified live from the same corp-proxy env: HUOBI symbols now report `[ERROR] load_markets failed: blocked by corporate proxy (Zscaler wac_block.html); htx GET https://api.huobi.pro/v2/reference/currencies 403 Forbidden — contact IT to allowlist this exchange host`.
- [x] Docs synced: README + deploy.md troubleshooting rows for the 403 / `wac_block.html` case; implementation.md deps table entry updated; task.md this entry.
- [x] Gates clean: ruff, mypy, **201 pytest** (was 197; +4 new).

Follow-up for reviewers: `_PROXY_BLOCK_SENTINELS` is a one-liner tuple. Add new vendors when they surface (Palo Alto `pan-block`, Netskope `netskope-block`, Cisco Umbrella `umbrella-block-page`, etc.) with the same idempotent `(sentinel, vendor_label)` shape — no other code changes needed.

## Phase 7n — Vertex Protocol custom venue (VERTEX + 4 edges)

- [x] User report: every `VERTEX` symbol was ERROR. Root cause was correct-by-design — VERTEX (and its multi-chain edges `AVAVERTEX` / `BERAVERTEX` / `MNTVERTEX` / `SOVERTEX`) is a decentralised perpetuals+spot exchange that ccxt 4.5.60 doesn't support, so `build_tasks` was emitting the "unknown exchange_name; add it to `exchange_mapping.yaml`" ERROR for every row. Same story as INJECTIVE / BLUEFIN / KALSHI / PYTH etc. on the "cross this bridge when we come to it" list.
- [x] User chose (via `AskQuestion`): scope = all five edges; symbol format = `BTC-PERP` (bare base + `-PERP`, same as Nado); network stance = build anyway even though the corp Zscaler blocks Vertex data endpoints at the TLS layer from the dev machine (connection reset by peer during TLS handshake — different mechanism from HUOBI's 403 + `wac_block.html` but same category: corp-blocked crypto host).
- [x] Vertex API research: Nado is a Vertex fork, so `archive.<edge>.vertexprotocol.com/v2/symbols` returns the same `{SYMBOL: {trading_status, ...}}` shape. Edge hostnames confirmed from Vertex's own Python SDK source (`vertex_protocol.utils.backend.VertexBackendURL`):
  - `VERTEX`     → `archive.prod.vertexprotocol.com`
  - `AVAVERTEX`  → `archive.avax-prod.vertexprotocol.com`
  - `BERAVERTEX` → `archive.bera-prod.vertexprotocol.com`
  - `MNTVERTEX`  → `archive.mantle-prod.vertexprotocol.com`
  - `SOVERTEX`   → `archive.sonic-prod.vertexprotocol.com` (decoded `SO` prefix as Sonic — flag in code comment for if it turns out to mean a different chain)
- [x] Implementation: new module `fh_symbol_check/custom_venues/vertex.py`.
  - Private `_EDGES: dict[str, str]` maps FH exchange_name → archive base URL (single source of truth; easy to add Base / Sei / Blast / Abstract / XRPL sidechain when they show up in FH).
  - Private `_fetch(edge)` core: builds `{base}/v2/symbols`, GETs with the same descriptive `FindExpiredSymbolsInFH/1.0 (symbol-validation)` UA + `Accept: application/json` we use for Nado, wraps `URLError` / `TimeoutError` / JSON errors in `VertexFetchError(f"…{edge}…")` so the error message names which edge failed.
  - Private `_parse_symbols(edge, payload)`: same status semantics as Nado (`trading_status == "live"` → True, anything else including missing → False). Uppercases keys; skips non-str keys and non-dict entries silently to survive future schema additions.
  - Five thin public wrappers `fetch_vertex()` / `fetch_avavertex()` / `fetch_beravertex()` / `fetch_mntvertex()` / `fetch_sovertex()` so the registry in `custom_venues/__init__.py` can point at a distinct callable per venue.
- [x] Registered all five in `CUSTOM_VENUES`. FH rows with those exchange_names now flow through `_classify_custom_venue` (via the existing `custom:` sentinel routing in the validator), so the checker slots into the summary tables and CSV/JSON schemas exactly like Nado / Polymarket Perps — no changes needed in `validator.py`, `reporter.py`, or `cli.py`.
- [x] 16 new pytest cases in `tests/test_custom_venues_vertex.py`:
  - Parser: live → True, non-live (`not_tradable`, `reduce_only`, missing status) → False, uppercases keys, ignores garbage entries, top-level-must-be-dict raises `VertexFetchError`, error message names the offending edge (so operators know which of the five broke).
  - Registry: all five edges present in `_EDGES` with distinct archive hostnames (guards against copy-paste bugs); each edge maps to the exact expected subdomain; all five wrappers wired into `CUSTOM_VENUES`.
  - `_fetch` URL composition: each of the five wrappers hits exactly its own `/v2/symbols` URL (single parametric test asserts the list of seen URLs).
  - Happy path returns parsed liveness map; invalid JSON raises `VertexFetchError` naming the edge; URL/network errors raise `VertexFetchError` naming the edge; request sends browser-ish UA + JSON Accept header; `_fetch("VRTX")` (typo) raises `KeyError` loudly.
- [x] Verified live from the corp-proxy env: VERTEX / AVAVERTEX rows previously ERROR with `unknown exchange_name='VERTEX'; add it to exchange_mapping.yaml`; now ERROR with `custom venue fetch failed: failed to fetch VERTEX symbols: <urlopen error [Errno 54] Connection reset by peer>`. Short, single-line, sanitiser doesn't even trigger. On any network where IT allowlists the Vertex archive hostnames (or on the deploy server if its egress differs), the checker will actually classify LISTED / INACTIVE / DELISTED.
- [x] Gates clean: ruff, mypy, **217 pytest** (was 201; +16 new).

Follow-ups / open questions for reviewers:

1. **SOVERTEX = Sonic?** Moot — all edges are gone (Phase 7r).
2. **Zscaler unblock request.** Superseded by Phase 7r. The TLS EOF on `archive.*.vertexprotocol.com` was leftover DNS to a shut-down service, not a proxy allowlist miss. The `_rewrite_proxy_block` TLS-teardown hint remains useful for *other* custom venues.
3. **Symbol format is a guess.** Moot — every Vertex-family symbol is now `DELISTED` because the venue shut down, regardless of ticker form.

## Phase 7o — TLS-teardown block detection (custom venues; VERTEX on the deploy server)

- [x] User report: after deploying to `SGP-DEVOPS-MBS-02`, every Vertex-family symbol was ERROR with `custom venue fetch failed: failed to fetch AVAVERTEX symbols: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1032)>`.
- [x] Diagnosed live on the server and confirmed it is **not** a code, trust-store, or User-Agent problem:
  - `openssl s_client -connect archive.avax-prod.vertexprotocol.com:443 -servername …` → TCP `CONNECTED`, then `SSL handshake has read 0 bytes and written 357 bytes`, `no peer certificate available`. The connection is killed after the SNI is read and before any TLS response, so nothing decrypted the request. Rules out `truststore` (which only addresses `CERTIFICATE_VERIFY_FAILED`) and the UA (never sent — no HTTP request happens).
  - No interception certificate is presented, so this is **not** the `wac_block.html` mechanism from Phase 7m and **not** an SSL-inspection bypass issue. Earlier speculation that the fix was a bypass-list entry was wrong; the ask is a firewall / URL-filtering **allow** rule.
  - DNS resolves to two real public IPs (`216.150.16.129`, `216.150.1.129`) and TCP connects, so traffic isn't being redirected to a proxy — an inline device is killing it mid-path.
  - `curl https://archive.prod.nado.xyz/v2/symbols` → **200** from the same box, same code path, same UA, same minute. Nado is a Vertex fork serving the identical `/v2/symbols` API, so crypto-API egress in general works; the denial is specific to `vertexprotocol.com` (likely URL category, with `nado.xyz` uncategorised).
  - All five edges fail identically, so the `*.vertexprotocol.com` allowlist IT added earlier is not in effect for any of them.
- [x] Escalated to IT with the above as evidence. Ticket asks for an allow rule on all five `archive.*.vertexprotocol.com` hostnames and explicitly notes it is not a bypass request. **Superseded by Phase 7r** — the hosts are gone (Vertex shutdown), not blocked.
- [x] Code: extended `_rewrite_proxy_block` in `validator.py` with a second signature class, `_TLS_TEARDOWN_SENTINELS` (`UNEXPECTED_EOF_WHILE_READING`, `EOF occurred in violation of protocol`). Unlike the block-page branch there's no HTML to strip, so the original text is preserved and prefixed with `likely blocked by corporate network policy (TLS handshake closed before certificate exchange)` + `— contact IT to allowlist this host`. Hedged with "likely" because an upstream outage produces the same signature. Block-page sentinels are still checked first (more specific — names the vendor).
- [x] Code: wired `_rewrite_proxy_block` into `_classify_custom_venue`, mirroring the WARNING-hint / DEBUG-raw split already used in `_classify_one_exchange`. Previously only the ccxt path got block detection.
- [x] Code: custom-venue fetch errors now embed the failing URL (`failed to fetch AVAVERTEX symbols from https://…/v2/symbols: …`) in `vertex.py`, `nado.py`, and `polymarket_perps.py`, so the hint names the exact host to put in the IT ticket. Rendered detail is 357 chars — single line, well under the 500-char `_sanitize_error_detail` cap.
- [x] Corrected the now-stale comment in `vertex.py` that described the deploy server's egress as unknown.
- [x] 5 new pytest cases in `tests/test_validator.py`: TLS sentinel recognised and hostname preserved; bare openssl prose wording (`EOF occurred in violation of protocol` without the SSL constant) recognised; block-page sentinel wins when both signatures appear; end-to-end via `classify_symbols` that a `VertexFetchError` carrying the verbatim server error yields ERROR rows with the hint, the hostname, and ≤ 500 chars; ordinary failures (`502 Bad Gateway`) keep their raw detail and get no hint.
- [x] Gates clean: ruff, mypy, **300 pytest** (was 295; +5 new).

Note: this is a diagnosis-and-reporting change only. Vertex rows stay `ERROR` on this network until IT allowlists the hosts — the tool now just explains why instead of printing an `_ssl.c` traceback.

## Phase 7p — Injective custom venue (INJECTIVE)

- [x] User report: `unknown exchange_name='INJECTIVE'; add it to exchange_mapping.yaml`. Root cause was correct-by-design — Injective is a DEX, ccxt 4.5.60 has no `injective` / `helix` id, so a YAML mapping cannot help. Same class as Vertex (Phase 7n).
- [x] User chose (via `AskQuestion`): translator assumes FH `BASE/QUOTE-PERP` + spot `BASE/QUOTE`; status policy = Active LISTED, Paused/Expired INACTIVE, Demolished treated as absent (DELISTED).
- [x] Probed live LCD: `https://sentry.lcd.injective.network/injective/exchange/v1beta1/{spot,derivative}/markets` returns 129 spot + 286 derivative Active markets. Tickers are `INJ/USDT` (spot) and `BTC/USDC PERP` (space, not hyphen; all live perps currently USDC-quoted). Status filter works: Paused 36 spot / 110 deriv, Expired 0 spot / 6 dated futures (`WTIV5/USDT-22SEP25`), Demolished 19 spot / 2 deriv. Empty `?status=` still returns Active-only, so the checker issues 6 GETs.
- [x] Implementation: `fh_symbol_check/custom_venues/injective.py` + `_translate_injective` (`BTC/USDC-PERP` → `BTC/USDC PERP`). Registered in `CUSTOM_VENUES`. Fetch errors embed the failing URL. Tickers are stripped (LCD has at least one trailing-space ticker `APP/INJ `).
- [x] Tests: `tests/test_custom_venues_injective.py` (parser statuses, ticker strip, 6-URL composition, Demolished omitted from fetch, Active-wins merge, headers, JSON/network errors) + translator cases in `tests/test_symbol_translation.py`.
- [x] Docs synced: README status + translator + shipped-venues table; design.md registry/translator/checker tables; implementation.md files table; this entry. Phase 7b open list no longer names INJECTIVE as unmapped.
- [x] Gates clean: ruff, mypy, **325 pytest** (was 300).
- [x] Follow-up: first live `--exchange-name INJECTIVE -v --show-listed` will confirm whether FH really stores `BTC/USDC-PERP` (if known-good pairs come back DELISTED, the quote or hyphen convention is wrong). Dated expired futures (`WTIV5/USDT-22SEP25`) only match if FH stores that exact ticker.

## Phase 7q — Robinhood Lighter custom venue (RHLIGHTER)

- [x] User report: `unknown exchange_name='RHLIGHTER'; add it to exchange_mapping.yaml`. Not a YAML fix: `RHLIGHTER` is Robinhood Chain Lighter (`https://api.rh.lighter.xyz`), a separate deployment from the existing `LIGHTER` → ccxt `lighter` mapping (`mainnet.zklighter.elliot.ai`). CoinGecko and the two live books confirm they do **not** share markets — RH has 57 perps + 27 spot (USDG), mainnet has 235 perps + 11 spot (USDC); overlap is 43 perps, 14 RH-only, 192 mainnet-only. Mapping `RHLIGHTER: lighter` would classify against the wrong universe.
- [x] User chose (via `AskQuestion`): translator assumes FH `BASE/QUOTE-PERP` or `BASE-PERP` → venue bare base (`BTC`); spot `META/USDG` is identity.
- [x] Implementation: `fh_symbol_check/custom_venues/rhlighter.py` hits `GET /api/v1/orderBookDetails`. `status == "active"` → LISTED, `inactive` → INACTIVE, absent → DELISTED. `_translate_rhlighter` registered. Fetch errors embed the URL. **Did not** add a YAML entry.
- [x] Tests: `tests/test_custom_venues_rhlighter.py` + translator cases in `tests/test_symbol_translation.py`.
- [x] Docs synced: README / design.md / implementation.md / this entry.
- [x] Gates clean: ruff, mypy, **347 pytest** (was 325).
- [x] Follow-up: first live `--exchange-name RHLIGHTER -v --show-listed` will confirm whether FH really stores `BTC/USDG-PERP` / `BTC-PERP`. If known-good pairs come back DELISTED, the FH form is different.

## Phase 7r — Vertex Protocol shut down (July 2025 / Ink merger)

- [x] User report: `archive.prod.vertexprotocol.com` is gone. Vertex Protocol shut down; DNS outlived the service. Confirmed independently: July 2025 Ink Foundation merger, all EVM edges ceased trading (4-phase shutdown 8–17 July 2025), back-end deprecated by mid-August 2025.
- [x] This reinterprets the Phase 7o TLS EOF. Leftover DNS still resolves and TCP connects; the peer returns 0 bytes because nothing is serving TLS, not because a proxy filtered the SNI. Nado returning 200 was a red herring (Nado is a live fork on a different hostname).
- [x] Implementation: `_fetch` no longer calls the archive hosts. It raises `VenueGone` naming the edge and former host. Validator treats `VenueGone` as `DELISTED` (counts as dead — operators should remove the config) with that message as `detail`, not `ERROR`. Historical `_parse_symbols` kept.
- [x] Tests: five wrappers raise `VenueGone`; `urlopen` is not called; unknown edge still `KeyError`; end-to-end `classify_symbols` on VERTEX rows is DELISTED with the shutdown text.
- [x] Docs: README / design.md / implementation.md / deploy.md troubleshooting (Vertex split out of the TLS-proxy row) / Phase 7n follow-ups marked moot / this entry.
- [x] Gates clean: ruff, mypy, **349 pytest** (was 347).

## Phase 7s — ARCUS custom venue (dYdX Labs DEX)

- [x] User report: unknown `exchange_name='ARCUS'`. ccxt 4.5.60 has no `arcus` id — not a YAML mapping. Custom venue.
- [x] Live probe: `GET https://api.arcus.xyz/v1/markets` returns 64 mainnet perps; tickers are `BTC-USD`; `status` is `ONLINE` / `OFFLINE`. OFFLINE markets are visibility-only → INACTIVE.
- [x] Translator `_translate_arcus`: FH `BTC/USD-PERP` or `BTC/USD` → venue `BTC-USD`.
- [x] Checker `arcus.py` keyed on `marketDisplayName` (uppercased). ONLINE → live, OFFLINE → inactive. Fetch errors include the URL. **Did not** add a YAML entry — ccxt has no `arcus` id.
- [x] Tests: parser, fetch URL/headers, invalid JSON / network error, `CUSTOM_VENUES` registration, translator cases.
- [x] Docs: README / design.md / implementation.md / deploy.md / this entry. ARCUS struck from the identify-open list.
- [x] Gates clean: ruff, mypy, **370 pytest** (was 349).

## Phase 7t — ONDOPERPS custom venue

- [x] User report: unknown `exchange_name='ONDOPERPS'`. ccxt 4.5.60 has no `ondo` / `ondoperps` id — not a YAML mapping. Custom venue.
- [x] Live probe: `GET https://api.ondoperps.xyz/v1/markets` returns 81 perps; tickers are `NVDA-USD.P`. No status field — present → LISTED, absent → DELISTED.
- [x] Translator `_translate_ondoperps`: FH `NVDA/USD-PERP` / `US100/USD-PERP` → venue `NVDA-USD.P` / `US100-USD.P`.
- [x] Checker `ondoperps.py` keyed on `market` (uppercased). `success: false` raises. Fetch errors include the URL. **Did not** add a YAML entry.
- [x] Tests: parser, success=false, fetch URL/headers, invalid JSON / network error, `CUSTOM_VENUES` registration, translator cases.
- [x] Docs: README / design.md / implementation.md / deploy.md / this entry.
- [x] Gates clean: ruff, mypy, **392 pytest** (was 370).

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

## Phase 8c — rsync-based deploy flow (supersedes Phase 8b)

User asked to swap the "shared pixi workspace + wheel-in-`[pypi-dependencies]`" model out for a simpler rsync-based flow modelled on their existing `irq_service` deploy. Install directory is now user-chosen (`/opt/find-expired-symbols/`, `/home/<user>/api/find-expired-symbols/`, etc.), the wheel is installed on top of the pixi env with `pip install --no-deps --force-reinstall`, and the same five steps serve both fresh installs and updates — no separate upgrade path.

- [x] `pixi.toml` — single source of truth for both dev and deploy:
  - Added `cryptography = "*"` under `[dependencies]` (conda-forge) so the manylinux_2_28 mismatch that used to bite on the server can't recur; sourced globally instead of only in the deploy manifest.
  - Added `pip = "*"` under `[dependencies]` so `pixi run -e find-expired-symbols pip install …` works — pixi conda envs otherwise ship without pip.
  - Added a new `find-expired-symbols` feature carrying only the task alias (`find-expired-symbols --creds-file $PIXI_PROJECT_ROOT/.DBCreds.yaml`), plus the matching entry in `[environments]`. Users invoke with `pixi run -e find-expired-symbols …`; the task alias auto-prefills `--creds-file` and is fully relocatable via `$PIXI_PROJECT_ROOT`.
  - Verified: `pixi info` reports the three envs (`default`, `dev`, `find-expired-symbols`) with the correct feature/dep sets for both `osx-arm64` and `linux-64`.
- [x] Deleted `deploy/shared-workspace-pixi.toml` and the (now-empty) `deploy/` folder — the root `pixi.toml` + `pixi.lock` are what operators rsync to the server, so a separate template is redundant.
- [x] `deploy.md` — rewritten around the 5-step flow (build wheel → rsync wheel + pixi.toml + pixi.lock → ssh → cd → `pixi run -e find-expired-symbols pip install --no-deps --force-reinstall <wheel>`). Same steps for fresh install and update. Bootstrap Python step (`/opt/pyhost`) removed — pixi provisions the interpreter directly. Kept the multi-user permissions story as an optional appendix. Kept + refreshed the troubleshooting cheatsheet (SSL trust store, User-Agent, Zscaler URL-category block, creds file location).
- [x] `README.md` "Deploying on a Linux server" section rewritten to show the 5-step flow inline and point at `deploy.md` for the full runbook.
- [x] `design.md` §11 pointer updated (project layout no longer lists `deploy/`; deploy story now reflects rsync-based install into a user-chosen directory and the role of `cryptography` + `pip` in `[dependencies]`).
- [x] `implementation.md` — dropped the `deploy/shared-workspace-pixi.toml` row; refreshed the `deploy.md` row to describe the new runbook; added `cryptography` and `pip` rows to the Libraries table with their conda-forge rationale; refreshed the deploy-story pointer.
- [x] `task.md` — this phase; also updated the packaging & deploy open-items index (`Automate …` bullet now names the new phase and directory shape).

Deferred out of Phase 8c:

- [ ] Automate the "create `$FES_DIR` + rsync + first-run permissions" pass into a helper script or Ansible role. The manual runbook is small enough that automation isn't urgent.

## Phase 8d — REST API + HTMX browser UI service

User asked to add an on-demand API service with a browser UI on top of the existing CLI. Questionnaire pinned: drives both humans + machines, on-demand only (no scheduler), async-poll (POST + poll GET), HTMX front-end, in-memory jobs with 1h TTL, no auth (rely on perimeter), keep the CLI, systemd **user** unit for supervision.

Executed as five checkpoints, each gated on `pytest` / `ruff` / `mypy` before moving on.

**Checkpoint 1 — pipeline refactor (241/241 tests, gates clean):**
- [x] `fh_symbol_check/pipeline.py` — new module. `ScanFilters` (frozen dataclass mirroring argparse fields; `.validate()` reproduces argparse error strings), `ScanProgress` (immutable per-phase snapshot), `describe_filters`, `run_scan(filters, creds, exchange_map, *, on_progress=None) -> list[SymbolResult]`.
- [x] `fh_symbol_check/validator.py` — surgical: `classify_symbols(..., on_group_done=None)` optional callback fires once per completed ccxt group; exceptions swallowed. Zero behaviour change without the kwarg.
- [x] `fh_symbol_check/cli.py` — DB fetch → build_tasks → classify block replaced with a single `run_scan(filters, creds, exchange_map)` call. Argparse, logging, rendering, exit codes unchanged.
- [x] `tests/test_pipeline.py` — 24 new tests covering: filter validation error strings, `describe_filters`, DB routing per `source`, DB-error propagation, `--symbol` OR-semantics wiring, progress-callback phase order, monotonic completion counter, exception swallow, `classify_symbols` `on_group_done` firing exactly once per ccxt_id.

**Checkpoint 2 — API JSON skeleton (283/283 tests, gates clean):**
- [x] `pyproject.toml` — added `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart` to `[project.dependencies]`; added `find-expired-symbols-service = "fh_symbol_check.api.main:run"` entry point; extended `[tool.setuptools.package-data]` to include `api/templates/*.html` and `api/static/*`.
- [x] `pixi.toml` — added `fastapi`, `uvicorn-standard`, `jinja2`, `python-multipart` under `[dependencies]` (conda-forge). Added `find-expired-symbols-service` task alias pre-filling `--creds-file $PIXI_PROJECT_ROOT/.DBCreds.yaml`.
- [x] `fh_symbol_check/api/{__init__,main,server,routes,models,jobs,workers,views}.py` — API modules per `implementation.md` §1 additions. `views.py` shipped as a stub (empty `APIRouter`) at this checkpoint; filled in Checkpoint 3.
- [x] `tests/test_api_models.py` (11 tests), `tests/test_api_jobs.py` (12 tests), `tests/test_api_routes.py` (12 tests) — cover Pydantic validation dialect parity with argparse, `JobStore` state machine + TTL + thread safety, and the full JSON API surface (health, list, poll-to-done, DB-error path, 404).
- [x] `tests/test_packaging.py::test_service_run_is_a_second_console_script_entry_point` — locks the new console script name.

**Checkpoint 3 — HTMX + Jinja UI (295/295 tests, gates clean):**
- [x] `fh_symbol_check/api/templates/{base,home,scan_card,result_table,form_error}.html` — Jinja templates. `base` shells the HTMX + CSS. `home` renders the filter form (posts to `/`) + a `#scans-list` HTMX target. `scan_card` is state-aware and self-polls via `hx-get + hx-trigger="every 1s"` while queued/running; polling stops naturally when the done/failed swap drops the trigger. `result_table` includes a client-side status-chip filter (~15 lines of vanilla JS + a CSS attribute selector). `form_error` renders CLI-wording validation errors inline.
- [x] `fh_symbol_check/api/static/htmx.min.js` — vendored HTMX 1.9.12 (MIT, 48KB).
- [x] `fh_symbol_check/api/static/app.css` — ~200 lines, CSS variables for light + dark theme, status pills, progress bar, responsive form grid, sticky table header.
- [x] `fh_symbol_check/api/views.py` — real implementation: `GET /`, `POST /`, `GET /scans/{id}/partial`. Uses `Jinja2Templates.TemplateResponse(request, name, context)` (Starlette 1.6 signature). Form → Pydantic validation → `form_error.html` on failure, `scan_card.html` on success. Empty body when polling a swept job so HTMX stops the loop.
- [x] `fh_symbol_check/api/server.py` — mount `/static` conditionally; wire `views.views` router; register template environment on `app.state.templates` with a `filter_desc` Jinja filter.
- [x] `tests/test_api_templates.py` — 12 tests: form renders, static assets serve, POST returns scan card with polling trigger while running, done card has result rows and no trigger, form_error partial carries CLI wording, unknown-id partial returns empty body.

**Checkpoint 4 — systemd user unit + `deploy.md` rewrite:**
- [x] `deploy/find-expired-symbols.service` — user unit template. `Type=exec`, `ExecStart=%h/.pixi/bin/pixi run -e find-expired-symbols find-expired-symbols-service --bind 127.0.0.1 --port 8000`, `Restart=on-failure`, `RestartSec=5s`, `WantedBy=default.target`. Comments cover first-time install + update flow.
- [x] `deploy.md` — reworked. Kept the 5-step rsync flow; added an "API service (optional): systemd user unit" subsection under "First-time-only extras" (copy unit → `systemctl --user daemon-reload` → `systemctl --user enable --now` → `sudo loginctl enable-linger`); added an "API smoke test" alongside the CLI one; added an "Alternative: schedule against the API instead of the CLI" subsection under "Scheduler wiring"; added API-specific rows to the troubleshooting cheatsheet (connection refused, service dies on start, service stops on logout, browser cache, POST 422 wording); updated the directory-map appendix to include the API subpackage + systemd unit location. Uninstall / rollback flow updated to include `systemctl --user disable --now` and `rm -f ~/.config/systemd/user/find-expired-symbols.service`.

**Checkpoint 5 — documentation sync (this checkpoint):**
- [x] `README.md` — top-of-doc paragraph mentions the API service option; new "Running the API service" section (local dev + endpoint reference + persistence & lifecycle + service CLI reference); "Deploying on a Linux server" section notes the two console scripts + systemd wiring pointer; project layout tree extended for `fh_symbol_check/pipeline.py`, `fh_symbol_check/api/*`, `deploy/find-expired-symbols.service`.
- [x] `design.md` — Section 1 diagram + prose extended to show `pipeline.run_scan` as the shared core with two front-ends (CLI + API). Section 2 module tree extended for `pipeline.py`, `api/*`, `deploy/`. Two new subsections: §3.9 `pipeline.py` (contract, phases, callback semantics) and §3.10 `api/` subpackage (module-by-module walkthrough + template list + static assets). Old §3.9 `custom_venues/` renumbered to §3.11; internal cross-refs updated. New §12 "Service architecture (API + HTMX UI)" — non-goals for v1, request-lifecycle diagram, concurrency model, HTMX interaction pattern, testing surface.
- [x] `implementation.md` — §1 Files table extended with all new modules + templates + static assets + systemd unit + new test files. §6 Libraries table extended with `fastapi`, `uvicorn[standard]` / `uvicorn-standard`, `jinja2`, `python-multipart`. §9 Verification steps added for the API entry point, JSON surface, scan lifecycle, HTMX UI, packaging (templates + static bundled). §11 `pyproject.toml` snippet updated with new deps + entry point + package-data globs. New §12 "API service (implementation notes)" — framework choice, concurrency stacks, progress plumbing, TTL sweep, package data, Starlette version pin, systemd user unit rationale.
- [x] `task.md` (this file) — new Phase 8d block; quick-index updated; Phase 9 "Suggested follow-ups" list refreshed with API-service-specific items.

Deferred out of Phase 8d (moved to Phase 9 nice-to-haves):

- [ ] Persistent job storage (DB / SQLite) so the JobStore survives service restart. Currently everything is dropped on restart or after 1h TTL.
- [ ] Cancellation endpoint (`DELETE /scans/{id}` that signals the worker). No safe interruption point inside `classify_symbols` today; would need a cooperative cancellation token to plumb through.
- [ ] WebSocket / SSE for progress push instead of 1s polling. Not urgent — scans complete in seconds and polling is dead simple through corp proxies.
- [ ] Authentication (basic / SSO / mTLS at the reverse proxy). Out of scope for v1; the service is intended for behind-the-perimeter use.
- [ ] Rate-limiting / abuse protection at the API level. Same reasoning.
- [ ] Downloadable JSON / CSV export links on the done card. The `GET /scans/{id}` JSON endpoint already returns the full payload; wrapping it in a `<a download="…">` link is trivial and can wait for a user request.

## Phase 9 — Suggested follow-ups (not in this iteration)

Tracked here so they don't get lost:

- [ ] Persistent on-disk cache of `load_markets()` keyed by exchange + day
- [ ] ANSI colour in text output when stdout is a TTY
- [ ] Pre-commit hook that blocks commits containing the literal DB password
- [ ] `mysql_select_query.py`: context-manager pattern + connect/read timeouts
- [ ] Schema-drift guard at startup (`DESCRIBE crypto_db.fh_config`)
- [ ] CI workflow that runs `pytest`, `mypy`, `ruff`
- [ ] API: persistent job storage (DB / SQLite) so JobStore survives restart (from Phase 8d)
- [ ] API: cancellation endpoint (`DELETE /scans/{id}`) with cooperative interruption inside `classify_symbols` (from Phase 8d)
- [ ] API: WebSocket / SSE progress push (from Phase 8d — 1s polling is fine today)
- [ ] API: authentication front (basic / SSO / mTLS at the reverse proxy) (from Phase 8d)
- [ ] API: rate-limiting / abuse protection (from Phase 8d)
- [ ] API: downloadable JSON / CSV export links from the done scan card (from Phase 8d)
