# Deploying `find-expired-symbols` on a shared Linux server

Concrete, tested runbook for deploying the tool as a **shared pixi
workspace** — one install directory, one env, usable both by
interactive users (via `pixi run find-expired-symbols …`) and by
scheduled jobs (via the env's console-script binary called by absolute
path). Pixi is used on both sides:

- on the dev box, to build the wheel
- on the server, to provide a Python 3.11 interpreter and to hold the
  shared workspace users invoke

The scheduler contract at the end is unchanged from any other pip-
installed tool: absolute path + CLI flags + exit code.

---

## Contents

- [Prerequisites](#prerequisites)
- [Part 1 — Build the wheel on the dev box](#part-1--build-the-wheel-on-the-dev-box)
- [Part 2 — Ship the wheel to the server](#part-2--ship-the-wheel-to-the-server)
- [Part 3 — Bootstrap a usable Python via pixi](#part-3--bootstrap-a-usable-python-via-pixi)
- [Part 4 — Create the shared install workspace](#part-4--create-the-shared-install-workspace)
- [Part 5 — Configure DB credentials](#part-5--configure-db-credentials)
- [Part 6 — Permissions for multi-user access](#part-6--permissions-for-multi-user-access)
- [Part 7 — Give users a shorter command (optional)](#part-7--give-users-a-shorter-command-optional)
- [Part 8 — Smoke test](#part-8--smoke-test)
- [Part 9 — Wire the scheduler](#part-9--wire-the-scheduler)
- [Part 10 — Upgrading](#part-10--upgrading)
- [Part 11 — Uninstall / rollback](#part-11--uninstall--rollback)
- [Troubleshooting cheatsheet](#troubleshooting-cheatsheet)
- [Directory map after a successful install](#directory-map-after-a-successful-install)

---

## Prerequisites

**Dev machine:**

- The repo cloned locally.
- `pixi` installed (macOS: `curl -fsSL https://pixi.sh/install.sh | bash`).
- Commit + push your local changes first — the wheel version comes
  from `pyproject.toml` and the bundled `exchange_mapping.yaml`
  reflects whatever is in your working tree, so make sure that's the
  state you want to release.

**Linux server:**

- `pixi` installed (any recent version).
- Outbound HTTPS (443) to `conda-forge` (for pixi to fetch Python) and
  to the exchange REST APIs the tool will call at runtime.
- TCP reachability to the FH MySQL host.
- **Not required:** a working system Python, system `pip`, deploy-key
  SSH access to GitHub, or root — everything below is user-scoped
  except two `sudo mkdir`s under `/opt` and setting group ownership on
  `/etc/find-expired-symbols`.

---

## Part 1 — Build the wheel on the dev box

`build` is declared under `[feature.dev.pypi-dependencies]` in
`pixi.toml`, so a normal dev-env install has everything it needs:

```bash
cd /path/to/FindExpiredSymbolsInFH

# One-time (or after new deps land)
pixi install -e dev

# Optional but recommended: run the gates first
pixi run -e dev ruff check .
pixi run -e dev mypy fh_symbol_check tests
pixi run -e dev pytest -q

# Build the wheel
rm -rf dist/
pixi run -e dev python -m build --wheel
```

Output: `dist/find_expired_symbols-<version>-py3-none-any.whl`. The
version is read from `[project.version]` in `pyproject.toml`; bump it
there when you cut a release.

**Sanity check the wheel actually contains what you expect:**

```bash
pixi run -e dev python -m zipfile -l dist/find_expired_symbols-*.whl | head -30
```

You should see:

- `fh_symbol_check/*.py` (the package)
- `fh_symbol_check/custom_venues/*.py`
- `fh_symbol_check/data/exchange_mapping.yaml` ← **this must be there**;
  it's what makes the console script's `--exchange-map` default work
  out of the box on the server
- `check_delisted_symbol.py`, `mysql_select_query.py` (top-level helpers)
- `find_expired_symbols-<version>.dist-info/entry_points.txt` (declares
  the `find-expired-symbols` console script)

---

## Part 2 — Ship the wheel to the server

Any file-transfer method works. `scp` is the simplest:

```bash
scp dist/find_expired_symbols-*.whl \
    deploy/shared-workspace-pixi.toml \
    <user>@<server>:/home/<user>/tmp/
```

Ship the shared-workspace `pixi.toml` (from `deploy/` in this repo)
alongside the wheel — you'll move both into place on the server in
Part 4.

---

## Part 3 — Bootstrap a usable Python via pixi

**On the server**, create a durable pixi env whose only job is to
provide a Python 3.11+ interpreter. This is the workaround for hosts
whose system Python is missing `venv`/`ensurepip` or is < 3.10.

```bash
sudo mkdir -p /opt/pyhost
sudo chown "$USER" /opt/pyhost
cd /opt/pyhost

pixi init                              # creates /opt/pyhost/pixi.toml
pixi add "python>=3.11,<3.14"          # solves + installs the env

# Verify — this must succeed
/opt/pyhost/.pixi/envs/default/bin/python --version              # → 3.11.x or 3.12.x
/opt/pyhost/.pixi/envs/default/bin/python -c 'import venv, ensurepip; print("ok")'
```

If either verification line fails, do **not** proceed — post the error
output and diagnose. `pixi add` should always give you a fully-featured
conda-forge Python that has the whole stdlib.

**Important:** `/opt/pyhost` is a separate concern from the install in
Part 4. It exists purely to guarantee a working conda-forge Python is
available on the box. Don't delete it while the tool is deployed.

---

## Part 4 — Create the shared install workspace

The install itself is a pixi workspace at `/opt/find-expired-symbols/`.
It contains three things: the wheel, a `pixi.toml` that references it
and declares the shared task, and (after `pixi install`) the resolved
env in `.pixi/envs/default/`.

```bash
sudo mkdir -p /opt/find-expired-symbols
sudo chown "$USER" /opt/find-expired-symbols
cd /opt/find-expired-symbols

# Drop the wheel + the shared pixi.toml in place (both shipped in Part 2)
cp /home/<user>/tmp/find_expired_symbols-*.whl .
cp /home/<user>/tmp/shared-workspace-pixi.toml ./pixi.toml

# If the wheel version is not 0.1.0, update the `path = ...` line in
# ./pixi.toml so it matches the actual filename.

# Resolve + install the env (uses /opt/pyhost's Python indirectly via
# conda-forge; pixi handles that itself)
pixi install
```

**Checkpoint (must succeed before continuing):**

```bash
pixi run find-expired-symbols --help | head -3
```

Should print the `usage: find-expired-symbols ...` argparse block. If
it prints anything else — including a traceback — stop and read the
[Troubleshooting cheatsheet](#troubleshooting-cheatsheet).

The console script itself now lives at:

```
/opt/find-expired-symbols/.pixi/envs/default/bin/find-expired-symbols
```

That's the absolute path the scheduler will use in Part 9.

---

## Part 5 — Configure DB credentials

The wheel bundles `exchange_mapping.yaml` (code-tracked config), so
the **only** file the operator has to provide by hand is
`DBCreds.yaml`. It's a secret — keep it out of your dotfiles, config-
management systems, and shell history.

```bash
sudo install -d -m 750 /etc/find-expired-symbols
```

Create `/etc/find-expired-symbols/DBCreds.yaml` via your normal
secret-injection process. Content shape:

```yaml
crypto_db:
  host: <FH MySQL hostname or IP>
  database: crypto_db
  user: <username>
  password: <password>
```

Then set restrictive permissions (see the next section for the group
choice):

```bash
sudo chmod 640 /etc/find-expired-symbols/DBCreds.yaml
ls -l /etc/find-expired-symbols/DBCreds.yaml
```

---

## Part 6 — Permissions for multi-user access

Goal: any user on the box can `pixi run find-expired-symbols …`, but
nobody outside the intended group can read the DB password.

Pick a Unix group whose membership matches "who's allowed to use the
tool". On most dev/devops boxes `users` is fine because everyone with
a shell is already in it; on stricter setups, create a dedicated
group (`sudo groupadd fes-users` and add members with
`sudo usermod -aG fes-users <user>`).

```bash
FES_GROUP=users        # or fes-users, whatever you chose

# Install tree — world-readable, world-executable, not writable
sudo chown -R "$USER:$FES_GROUP" /opt/find-expired-symbols
sudo find /opt/find-expired-symbols -type d -exec chmod 755 {} \;
sudo find /opt/find-expired-symbols -type f -exec chmod 644 {} \;
# Binaries in the env need +x — restore it after the blanket 644
sudo find /opt/find-expired-symbols/.pixi/envs/default/bin -type f \
    -exec chmod 755 {} \;

# DB creds — group-readable to $FES_GROUP only
sudo chown "root:$FES_GROUP" /etc/find-expired-symbols
sudo chown "root:$FES_GROUP" /etc/find-expired-symbols/DBCreds.yaml
sudo chmod 750 /etc/find-expired-symbols
sudo chmod 640 /etc/find-expired-symbols/DBCreds.yaml

# Sanity check as a non-owner user (if you can `su`)
su -c 'cat /etc/find-expired-symbols/DBCreds.yaml >/dev/null && echo ok' <other-user>
```

**How this maps to the two invocation styles:**

- Interactive users need read/execute on `/opt/find-expired-symbols/`
  (they only *use* it — they never write). `pixi run` in a fully-
  installed workspace is a read-only operation.
- The scheduler (whatever user it runs as) needs read/execute on
  `/opt/find-expired-symbols/.pixi/envs/default/bin/` and read on
  `/etc/find-expired-symbols/DBCreds.yaml`. Make sure the scheduler
  user is in `$FES_GROUP`.

---

## Part 7 — Give users a shorter command (optional)

`pixi run find-expired-symbols` requires either being `cd`'d into
`/opt/find-expired-symbols` or passing `--manifest-path`. To let users
invoke from anywhere, drop a wrapper at `/usr/local/bin`:

```bash
sudo tee /usr/local/bin/find-expired-symbols >/dev/null <<'EOF'
#!/usr/bin/env bash
exec pixi run --manifest-path /opt/find-expired-symbols find-expired-symbols "$@"
EOF
sudo chmod 755 /usr/local/bin/find-expired-symbols
```

Then users can do:

```bash
find-expired-symbols --symbol TON/USDT-PERP
```

from any directory. If you prefer a shell alias per user rather than a
global wrapper, drop this in `/etc/profile.d/fes.sh`:

```bash
alias find-expired-symbols='pixi run --manifest-path /opt/find-expired-symbols find-expired-symbols'
```

(Note: the alias only fires in interactive shells; the wrapper works
in every shell + in non-interactive scheduler contexts.)

---

## Part 8 — Smoke test

Before wiring the scheduler up, prove the install can actually connect
and produce a report as **a non-admin user** (this is what proves the
multi-user setup works, not just your own account).

Log in as a regular member of `$FES_GROUP` and run:

```bash
cd /opt/find-expired-symbols

# Narrow — one host, one exchange, so failures are fast
pixi run find-expired-symbols \
    --hostname <a-known-fh-host> \
    --exchange-name BINANCE \
    --show-listed \
    -v
echo "exit=$?"
```

Success criteria in order:

1. No `creds error: ...` from the CLI → the shared DBCreds file is
   readable by this user.
2. Verbose log line `connected to <host>:3306` → MySQL reachable +
   credentials accepted.
3. Log line `fetched N rows from crypto_db.fh_config` → the FH row
   query succeeded.
4. A "Summary by feed handler" table renders at the bottom of stdout.
5. Exit code is `0` or `1`. `2` = operational failure — rerun with
   `--log-level DEBUG` and read the last error line.

Then try the `--symbol` case the user actually wants:

```bash
pixi run find-expired-symbols --symbol TON/USDT-PERP
```

If it prints one or more matching rows (or a clean "no occurrences
found" message), the install is production-ready.

---

## Part 9 — Wire the scheduler

Nothing here is deploy-md-specific — this is just how the installed
tool is invoked from Airflow / Jenkins / cron / systemd timers. The
runtime contract is `absolute path + CLI flags + exit code`, and the
scheduler doesn't need to know about pixi.

```bash
set -eu

FES=/opt/find-expired-symbols/.pixi/envs/default/bin/find-expired-symbols
CREDS=/etc/find-expired-symbols/DBCreds.yaml
OUTDIR=/var/log/find-expired-symbols
mkdir -p "$OUTDIR"

"$FES" \
    --creds-file "$CREDS" \
    --all \
    --errors-only \
    --output json \
    --output-file "$OUTDIR/$(date -u +%Y-%m-%dT%H%MZ).json" \
    --log-level INFO
```

Exit-code contract (locked in by `tests/test_packaging.py::test_exit_codes_exported`):

| Code | Meaning                                                       | Suggested alert routing            |
| ---- | ------------------------------------------------------------- | ---------------------------------- |
| `0`  | Ran cleanly, nothing invalid                                  | Green — no alert                   |
| `1`  | Ran cleanly, at least one non-`LISTED` symbol reported        | Warning — route to on-call triage  |
| `2`  | Operational failure (bad creds, DB down, unmapped exchange…)  | Page immediately                   |
| `130`| Interrupted (SIGINT)                                          | Info only — usually a manual kill  |

Airflow: `BashOperator` with the block above; `retries=0` on exit 2
(it usually needs human intervention).

Jenkins: freestyle job; if you want exit 1 to be a non-failing
warning, wrap with `sh -c '<cmd>; ec=$?; [ $ec -le 1 ] || exit $ec'`.

Cron: `0 */6 * * * /opt/find-expired-symbols/.pixi/envs/default/bin/find-expired-symbols --creds-file /etc/find-expired-symbols/DBCreds.yaml --all --output json --output-file /var/log/find-expired-symbols/$(date -u +\%FT\%H).json 2>&1 | logger -t find-expired-symbols`

**Log rotation** — the JSON files accumulate. Drop a `logrotate`
snippet at `/etc/logrotate.d/find-expired-symbols`:

```
/var/log/find-expired-symbols/*.json {
    rotate 14
    daily
    compress
    missingok
    notifempty
}
```

---

## Part 10 — Upgrading

**New tool version (most common):**

On the dev box, build the new wheel:

```bash
pixi run -e dev python -m build --wheel
scp dist/find_expired_symbols-*.whl <user>@<server>:/home/<user>/tmp/
```

On the server, swap the wheel in-place and re-solve:

```bash
cd /opt/find-expired-symbols

# Remove the old wheel and drop the new one alongside pixi.toml
sudo rm ./find_expired_symbols-*.whl
sudo cp /home/<user>/tmp/find_expired_symbols-*.whl .

# If the wheel version changed, update the `path = ...` line in pixi.toml

sudo -u <owner-of-install-dir> pixi install

# Verify
pixi run find-expired-symbols --help | head -1
```

Because `exchange_mapping.yaml` ships inside the wheel, the mapping
is refreshed automatically — you never sync YAML files by hand.

**New Python version** (rare — required if you bump the `>=3.11,<3.14`
constraint in the workspace `pixi.toml`): edit `pixi.toml`, then
`pixi install`. Pixi handles the interpreter swap; the wheel is
reinstalled against the new Python automatically.

---

## Part 11 — Uninstall / rollback

Because everything the tool touches lives in three well-known
locations, uninstall is trivial:

```bash
# The install itself
sudo rm -rf /opt/find-expired-symbols

# The wrapper (if you added Part 7)
sudo rm -f /usr/local/bin/find-expired-symbols

# The credential file
sudo rm -rf /etc/find-expired-symbols

# Log output (if you want it gone)
sudo rm -rf /var/log/find-expired-symbols

# Optionally, the pixi-provided Python — only if nothing else on the
# box uses /opt/pyhost
sudo rm -rf /opt/pyhost
```

To roll back to a previous wheel, keep the previous
`find_expired_symbols-<old-version>-py3-none-any.whl`, drop it into
`/opt/find-expired-symbols/`, point `pixi.toml`'s `path = …` back at
it, and `pixi install`.

---

## Troubleshooting cheatsheet

Symptoms we hit on the real deploy and how to fix them:

| Symptom                                                                                             | Cause                                                                                             | Fix                                                                                                                    |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `pixi run -e dev python -m pip install --quiet build` → `No module named pip`                       | Pixi envs don't ship `pip` by default (deliberately — deps should be declared in `pixi.toml`).    | `build` is already declared under `[feature.dev.pypi-dependencies]`; run `pixi install -e dev` and use `python -m build`. |
| Fresh venv install → `Package 'find-expired-symbols' requires a different Python: 3.7.16 not in '<3.14,>=3.10'` | The system Python was too old.                                                                    | This runbook doesn't use the system Python at all — the shared workspace pulls its interpreter from conda-forge. If you see this, you're following an older recipe. |
| `python -m venv <path>` → `No module named venv`                                                    | System Python was shipped without the `venv` stdlib module.                                        | Same as above — this runbook sidesteps `python -m venv` entirely. Pixi's conda-forge Python has the full stdlib.       |
| `pixi run find-expired-symbols` fails for another user with a permission error on `.pixi/`          | The install tree isn't group/other readable, or missing +x on binaries.                            | Rerun the Part 6 permissions block. Verify with `namei -l /opt/find-expired-symbols/.pixi/envs/default/bin/find-expired-symbols` as the failing user. |
| `pixi run find-expired-symbols` for another user fails with `creds error: file not found`           | `DBCreds.yaml` isn't readable by the user's group.                                                | Check `id <user>` includes `$FES_GROUP` and that the file is `640 root:<$FES_GROUP>`.                                   |
| `pixi run` wants to re-solve / re-download on someone else's account and fails on write to `.pixi/` | Something changed the lockfile after the initial `pixi install`.                                  | As the install owner: run `pixi install` once to freshen the env. Users can also pass `--frozen` (`pixi run --frozen find-expired-symbols …`) to skip the up-to-date check. |
| Everything worked yesterday; today the console script fails with `interpreter not found`           | `/opt/pyhost` was moved or deleted; nothing directly, but the shared workspace's cached conda env still trusts the world it was built in. | Restore `/opt/pyhost` (rerun Part 3), then `pixi install` in `/opt/find-expired-symbols`.                              |

For anything not on this list, rerun the failing invocation with
`--log-level DEBUG` and look at the last error line before the process
exited.

---

## Directory map after a successful install

```
/opt/pyhost/                                          # pixi env that provides a bootstrap Python
├── pixi.toml
├── pixi.lock
└── .pixi/envs/default/bin/python

/opt/find-expired-symbols/                            # the shared install workspace
├── pixi.toml                                         # workspace + tasks + wheel path
├── pixi.lock                                         # regenerated by `pixi install`
├── find_expired_symbols-<version>-py3-none-any.whl   # the wheel
└── .pixi/envs/default/
    ├── bin/
    │   ├── find-expired-symbols                      # ← scheduler calls this by absolute path
    │   ├── python
    │   └── pip
    └── lib/python3.11/site-packages/
        ├── fh_symbol_check/
        │   ├── cli.py, validator.py, reporter.py, ...
        │   ├── data/exchange_mapping.yaml            # bundled config
        │   └── custom_venues/
        ├── check_delisted_symbol.py                  # top-level helper
        ├── mysql_select_query.py                     # top-level helper
        ├── ccxt/                                     # runtime dep
        ├── pymysql/                                  # runtime dep
        └── yaml/                                     # runtime dep (pyyaml)

/usr/local/bin/find-expired-symbols                   # optional wrapper (Part 7)

/etc/find-expired-symbols/
└── DBCreds.yaml                                      # 640 root:$FES_GROUP, operator-provided

/var/log/find-expired-symbols/
└── <timestamp>.json                                  # scheduler-produced output
```

Four independent concerns (bootstrap Python, install workspace,
credentials, output). Blowing away any one of them affects only that
concern.
