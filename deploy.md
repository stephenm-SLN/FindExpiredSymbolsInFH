# Deploying `find-expired-symbols` on a Linux server

Simple rsync-based deploy. Build the wheel on your dev box, rsync it +
`pixi.toml` + `pixi.lock` to a directory of your choice on the server,
then install the wheel into the pixi env with
`pixi run -e find-expired-symbols pip install --no-deps --force-reinstall …`.

The same five steps run for a fresh install and for every subsequent
update. The single wheel ships **two** console scripts:

- `find-expired-symbols` — the CLI (unchanged; documented in `README.md`).
- `find-expired-symbols-service` — the FastAPI service (new).

Pick one, both, or neither on any given server. If you install the
service, it runs under a systemd user unit; the CLI has no such
requirement — it's just a program on `$PATH` inside the pixi env.

The install directory is user-chosen: `/opt/find-expired-symbols/`,
`/home/<user>/api/find-expired-symbols/`, `/srv/find-expired-symbols/`,
whatever fits. Everything lives under that one tree.

---

## Contents

- [Prerequisites](#prerequisites)
- [The deploy flow (new install and updates)](#the-deploy-flow-new-install-and-updates)
- [First-time-only extras](#first-time-only-extras)
    - [DB credentials](#db-credentials)
    - [API service (optional): systemd user unit](#api-service-optional-systemd-user-unit)
- [Verification / smoke test](#verification--smoke-test)
    - [CLI smoke test](#cli-smoke-test)
    - [API smoke test](#api-smoke-test)
- [Scheduler wiring (CLI)](#scheduler-wiring-cli)
- [Uninstall / rollback](#uninstall--rollback)
- [Multi-user permissions (optional)](#multi-user-permissions-optional)
- [Troubleshooting cheatsheet](#troubleshooting-cheatsheet)
- [Directory map after install](#directory-map-after-install)

---

## Prerequisites

**Dev machine:**

- Repo cloned locally, changes committed. The wheel version comes from
  `[project.version]` in `pyproject.toml` and the bundled
  `exchange_mapping.yaml` reflects your working tree, so commit before
  building.
- `pixi` installed (macOS: `curl -fsSL https://pixi.sh/install.sh | bash`).

**Linux server:**

- `pixi` installed and on the invoking user's `$PATH`. Nothing else is
  needed — pixi provides the Python interpreter, `pip`, and every
  runtime dep declared in `pixi.toml`.
- Outbound HTTPS to `conda-forge` and `pypi.org` (once, at first
  `pixi run` — the env is cached after that) and to the exchange REST
  APIs at runtime.
- TCP reachability to the FH MySQL host.
- **For the API service only:** systemd with user services enabled
  (any modern distro). You do not need `root`; the unit lives under
  `~/.config/systemd/user/` and is enabled with `systemctl --user`.

**Not required:** system Python, system `pip`, `venv`, `ensurepip`,
`sudo` (except once for `loginctl enable-linger` on the service track),
or SSH deploy-keys to GitHub. The install is user-scoped in a
user-writable directory.

---

## The deploy flow (new install and updates)

Set your target once (adjust to taste — the install directory is
whatever you want, as long as your user can write there):

```bash
FES_HOST=your-server.example.com
FES_USER=stephen.m
FES_DIR=/home/$FES_USER/api/find-expired-symbols   # or /opt/find-expired-symbols, /srv/..., etc.
FES_VER=0.1.0                                      # matches [project.version] in pyproject.toml
```

On the **server**, once, create the install directory:

```bash
ssh "$FES_USER@$FES_HOST" "mkdir -p $FES_DIR"
```

Then, from the dev repo root, run the five steps:

```bash
# 1. Build the wheel locally
pixi run -e dev python -m build --wheel

# 2. Rsync wheel + pixi manifest + lockfile to the install directory
rsync "dist/find_expired_symbols-$FES_VER-py3-none-any.whl" pixi.toml pixi.lock \
    "$FES_USER@$FES_HOST:$FES_DIR/"

# 3. SSH to the server
ssh "$FES_USER@$FES_HOST"

# 4. Change to the install directory
cd "$FES_DIR"      # substitute your literal path if $FES_DIR isn't set in your remote shell

# 5. Install the wheel into the pixi env
pixi run -e find-expired-symbols pip install --no-deps --force-reinstall \
    find_expired_symbols-0.1.0-py3-none-any.whl
```

That's it. The exact same five steps run for a fresh install and for
every upgrade — pixi transparently creates + solves the env on the
first `pixi run`, and `--force-reinstall` swaps the wheel out cleanly
on updates.

**If the API service is running, follow up with one extra command
after step 5** to pick up the new code:

```bash
systemctl --user restart find-expired-symbols
```

The CLI needs no equivalent — schedulers pick up the new wheel on
their next invocation. See [API service](#api-service-optional-systemd-user-unit)
for the first-time unit setup.

**Why these flags on the pip install:**

- `--no-deps` — runtime deps (ccxt, pymysql, pyyaml, truststore,
  cryptography, fastapi, uvicorn, jinja2, python-multipart, ...) come
  from `pixi.toml`. Pip only installs the wheel itself into the env's
  `site-packages`. This avoids pip re-resolving deps from PyPI and
  running into the manylinux mismatch that bit us on the `cryptography`
  transitive.
- `--force-reinstall` — swaps out the previously-installed
  `find-expired-symbols` package cleanly on updates. Idempotent on
  fresh installs (no-op if nothing was installed).

**When the wheel adds a new dependency:**

The wheel's `pyproject.toml` `[project.dependencies]` is the source of
truth for runtime requirements, but with `--no-deps` we bypass pip's
resolver, so `pixi.toml` needs to know too. If a new dep lands (or a
bound bumps in a way that matters), also update `[dependencies]` /
`[pypi-dependencies]` in `pixi.toml` before building. The rsync in
step 2 ships that same file, so pixi will re-solve automatically on
the next `pixi run` on the server.

---

## First-time-only extras

### DB credentials

Exactly one secret file to place by hand:

```bash
# On the server, in $FES_DIR
cat > .DBCreds.yaml <<'EOF'
crypto_db:
  host: <FH MySQL hostname or IP>
  database: crypto_db
  user: <username>
  password: <password>
EOF
chmod 600 .DBCreds.yaml
```

Both console scripts auto-pick it up via the pixi task aliases:

```toml
# from pixi.toml (excerpt)
[feature.find-expired-symbols.tasks]
find-expired-symbols         = "find-expired-symbols         --creds-file $PIXI_PROJECT_ROOT/.DBCreds.yaml"
find-expired-symbols-service = "find-expired-symbols-service --creds-file $PIXI_PROJECT_ROOT/.DBCreds.yaml"
```

`$PIXI_PROJECT_ROOT` is expanded by pixi at task run-time to the
directory containing `pixi.toml`, so the same manifest works whether
the install lives at `/opt/find-expired-symbols/`, under `/home/…/`,
or anywhere else. Users never have to pass `--creds-file` on the
command line, and there's nothing to edit after copying the manifest
into place.

`.DBCreds.yaml` is a secret — keep it out of dotfiles, config-
management systems, and shell history. For multi-user access, see
the [Multi-user permissions](#multi-user-permissions-optional)
appendix; the default `chmod 600` is right for single-user installs.

### API service (optional): systemd user unit

Skip this section if you only need the CLI.

The service is a long-running HTTP process (FastAPI + uvicorn) with an
HTMX-driven browser UI at `/` and a JSON API at `/scans`, `/scans/{id}`,
`/health`. Since it holds no persistent state, restarting it is safe;
in-flight scans get killed and any completed-but-not-yet-fetched jobs
are dropped.

Copy the sample unit shipped in the repo (`deploy/find-expired-symbols.service`)
to the user systemd config directory and edit the `WorkingDirectory=`
to your `$FES_DIR`:

```bash
mkdir -p ~/.config/systemd/user
cp $FES_DIR/deploy/find-expired-symbols.service ~/.config/systemd/user/   # or scp from your dev box
# The unit ships as `deploy/find-expired-symbols.service` in the wheel's
# source tree — for a wheel-only install, grab it from the repo directly
# and edit before copying.

# Edit WorkingDirectory= AND ExecStart= if $FES_DIR is not
# ~/api/find-expired-symbols. ExecStart must be the env binary
#   $FES_DIR/.pixi/envs/find-expired-symbols/bin/find-expired-symbols-service
# not `pixi run` — systemd cannot use PATH, and pixi is often at
# /usr/local/bin/pixi (203/EXEC if the unit still points at ~/.pixi/bin/pixi).
$EDITOR ~/.config/systemd/user/find-expired-symbols.service
```

Then load, enable, and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now find-expired-symbols
```

Verify:

```bash
systemctl --user status find-expired-symbols
journalctl --user -u find-expired-symbols -f    # tail service logs
```

Finally, allow the service to survive user logout / reboots:

```bash
sudo loginctl enable-linger $USER
```

Without linger, the user's systemd instance is torn down when your SSH
session closes, and the service dies with it. `loginctl enable-linger`
tells systemd to keep the user instance up regardless. This is the
single `sudo`-requiring command in the whole deploy.

**Bind address.** The unit binds `127.0.0.1:8000` by default — no
external exposure. If the box is behind a corp perimeter and you want
teammates to hit the UI directly, edit `ExecStart=` in the unit to
pass `--bind 0.0.0.0` and reload:

```bash
$EDITOR ~/.config/systemd/user/find-expired-symbols.service
systemctl --user daemon-reload
systemctl --user restart find-expired-symbols
```

**Reverse proxy (recommended for team access).** The service does no
auth. Front it with nginx / traefik / caddy if you want TLS or basic
auth; keep the service on `127.0.0.1:8000` and forward from `:443` to
`localhost:8000`. Beyond the scope of this doc — no code changes
needed.

---

## Verification / smoke test

### CLI smoke test

Before wiring the scheduler up, prove the install actually connects
and produces a report:

```bash
cd "$FES_DIR"

# 1) --help works — verifies the wheel is installed and the entry point resolves
pixi run -e find-expired-symbols find-expired-symbols --help | head -3

# 2) Narrow live run — one host, one exchange, fast failures
pixi run -e find-expired-symbols find-expired-symbols \
    --hostname <known-fh-host> \
    --exchange-name BINANCE \
    --show-listed \
    -v
echo "exit=$?"
```

Success criteria in order:

1. `--help` prints the argparse block.
2. No `creds error: …` from the CLI — `.DBCreds.yaml` is readable and
   parses.
3. Verbose log line `connected to <host>:3306` — MySQL reachable and
   credentials accepted.
4. `fetched N rows from crypto_db.fh_config` — the FH row query
   succeeded.
5. A "Summary by feed handler" table renders on stdout.
6. Exit code is `0` or `1`. `2` = operational failure — rerun with
   `--log-level DEBUG` and read the last error line before exit.

Then try the `--symbol` case:

```bash
pixi run -e find-expired-symbols find-expired-symbols --symbol TON/USDT-PERP
```

If it prints matching rows (or a clean "no occurrences found"
message), the CLI install is production-ready.

### API smoke test

If you installed the service, verify from the server:

```bash
# 1) Health check — should return {"status": "ok", "version": "..."}
curl -sSf http://127.0.0.1:8000/health | python -m json.tool

# 2) Submit a narrow scan
curl -sSf -X POST http://127.0.0.1:8000/scans \
    -H "Content-Type: application/json" \
    -d '{"exchange_name":"BINANCE","show_listed":true}' \
    | python -m json.tool
# note the "id" field in the response — that's your job id

# 3) Poll for status until state == "done"
JOB_ID=<paste id from step 2>
curl -sSf http://127.0.0.1:8000/scans/$JOB_ID | python -m json.tool
```

Then hit the browser UI. If the service is bound to `127.0.0.1`, use
SSH port-forwarding from your workstation:

```bash
# on your workstation
ssh -L 8000:127.0.0.1:8000 $FES_USER@$FES_HOST
```

Open <http://127.0.0.1:8000/> in your browser. You should see the
filter form, be able to submit a scan, watch the progress bar tick,
and see the result table on completion.

For the full API reference, browse to <http://127.0.0.1:8000/docs>
— FastAPI serves an interactive OpenAPI console.

---

## Scheduler wiring (CLI)

The CLI's scheduler contract is unchanged by the addition of the API
service. Absolute path + CLI flags + exit code; the scheduler doesn't
need to know pixi (or the API) exists.

The console script lives at:

```
$FES_DIR/.pixi/envs/find-expired-symbols/bin/find-expired-symbols
```

Sample cron / Airflow / Jenkins invocation:

```bash
set -eu

FES_DIR=/home/stephen.m/api/find-expired-symbols   # your install path
FES="$FES_DIR/.pixi/envs/find-expired-symbols/bin/find-expired-symbols"
CREDS="$FES_DIR/.DBCreds.yaml"
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

Exit-code contract (locked by
`tests/test_packaging.py::test_exit_codes_exported`):

| Code  | Meaning                                                       | Suggested alert routing            |
| ----- | ------------------------------------------------------------- | ---------------------------------- |
| `0`   | Ran cleanly, nothing invalid                                  | Green — no alert                   |
| `1`   | Ran cleanly, at least one non-`LISTED` symbol reported        | Warning — route to on-call triage  |
| `2`   | Operational failure (bad creds, DB down, unmapped exchange…)  | Page immediately                   |
| `130` | Interrupted (SIGINT)                                          | Info only — usually a manual kill  |

Airflow: `BashOperator` with the block above; `retries=0` on exit 2
(usually needs human intervention). Jenkins: freestyle job; if you
want exit 1 to be a non-failing warning, wrap with
`sh -c '<cmd>; ec=$?; [ $ec -le 1 ] || exit $ec'`.

**Alternative: schedule against the API instead of the CLI.** If you
prefer keeping every symbol run in one place with a shared view,
schedulers can `POST /scans` and follow the ``Location`` header:

```bash
# One-liner: submit + poll to done, extract exit-analogue from result
API=http://find-expired-symbols.internal
JOB=$(curl -sSf -X POST $API/scans \
        -H "Content-Type: application/json" \
        -d '{"all":true,"errors_only":true}' \
        | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

while true; do
    STATE=$(curl -sSf $API/scans/$JOB | python -c "import sys,json;print(json.load(sys.stdin)['state'])")
    [ "$STATE" = "done" ] && break
    [ "$STATE" = "failed" ] && exit 2
    sleep 5
done

curl -sSf $API/scans/$JOB > /var/log/find-expired-symbols/$(date -u +%Y-%m-%dT%H%MZ).json
```

The API route is nicer for humans (browser UI, shareable job URLs);
the CLI route is nicer for schedulers (no polling loop, direct exit
codes).

Log rotation for the JSON output:

```
# /etc/logrotate.d/find-expired-symbols
/var/log/find-expired-symbols/*.json {
    rotate 14
    daily
    compress
    missingok
    notifempty
}
```

---

## Uninstall / rollback

Because everything lives under `$FES_DIR`, uninstall is a single
command:

```bash
# If the service is installed, stop and disable first:
systemctl --user disable --now find-expired-symbols 2>/dev/null || true
rm -f ~/.config/systemd/user/find-expired-symbols.service
systemctl --user daemon-reload

# Then remove the install tree
rm -rf "$FES_DIR"
rm -rf /var/log/find-expired-symbols   # optional, if you also want scheduler output gone
```

**Rollback to a previous wheel:** rsync the older wheel over the
current one and rerun step 5.

```bash
rsync "dist/find_expired_symbols-<older-ver>-py3-none-any.whl" \
    "$FES_USER@$FES_HOST:$FES_DIR/"
ssh "$FES_USER@$FES_HOST"
cd "$FES_DIR"
pixi run -e find-expired-symbols pip install --no-deps --force-reinstall \
    find_expired_symbols-<older-ver>-py3-none-any.whl
# If the service was running, restart it to pick up the older code:
systemctl --user restart find-expired-symbols
```

`--force-reinstall` doesn't care about other wheels sitting alongside
in the directory — it only reinstalls the specific one you name. If
you want to keep the install directory tidy, `rm` the wheels you no
longer need.

---

## Multi-user permissions (optional)

For a single-user install (one operator on the box), the defaults
above (`chmod 600 .DBCreds.yaml`, user-owned install dir) are correct
and you can skip this section.

For a shared install where multiple users invoke the tool via
`pixi run -e find-expired-symbols …`, or where a scheduled job runs
as a different user than the one that installed the tool, add group
perms:

```bash
FES_GROUP=users        # or a dedicated group: `sudo groupadd fes-users && sudo usermod -aG fes-users <member>`
FES_DIR=/opt/find-expired-symbols

# Install tree — world-readable, world-executable, not writable
sudo chown -R "$USER:$FES_GROUP" "$FES_DIR"
sudo find "$FES_DIR" -type d -exec chmod 755 {} \;
sudo find "$FES_DIR" -type f -exec chmod 644 {} \;
# Env binaries need +x — restore after the blanket 644
sudo find "$FES_DIR/.pixi/envs/find-expired-symbols/bin" -type f \
    -exec chmod 755 {} \;

# DB creds — lock down again after the blanket 644
sudo chown "root:$FES_GROUP" "$FES_DIR/.DBCreds.yaml"
sudo chmod 640                 "$FES_DIR/.DBCreds.yaml"
```

Any user in `$FES_GROUP` can then `cd "$FES_DIR" && pixi run -e find-expired-symbols find-expired-symbols …`
or invoke the env binary by absolute path. The scheduler user must
also be in `$FES_GROUP`.

> **Order matters:** create `.DBCreds.yaml` **before** running the
> permissions block. Otherwise the blanket 644 leaves it world-
> readable until you rerun the last two lines.

**Service track note.** The systemd user unit runs under the account
that ran `systemctl --user enable`. It reads `.DBCreds.yaml` as that
user, so it needs `640 root:$FES_GROUP` (i.e. the service user must be
in `$FES_GROUP`).

---

## Troubleshooting cheatsheet

Real-world symptoms and fixes:

| Symptom                                                                                             | Cause                                                                                             | Fix                                                                                                                    |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `pixi run -e find-expired-symbols pip: command not found`                                           | `pip` isn't in the deploy env — usually means an older `pixi.toml` was rsync'd (before pip landed under `[dependencies]`). | Re-rsync `pixi.toml` from the dev repo (it declares `pip = "*"` under `[dependencies]`). Rerun `pixi run` — pixi re-solves automatically. |
| `pixi install` fails with `Wheel: cryptography-<ver>-cp3xx-abi3-manylinux_2_28_x86_64.whl doesn't match this systems virtual capabilities for tags: cp3xx-cp3xx-manylinux_2_26_x86_64 ...` | Recent `cryptography` PyPI wheels require manylinux_2_28 (glibc 2.28+); the conda-forge Python pixi installs advertises manylinux_2_26. `ccxt` pulls `cryptography` in transitively. | The shipped `pixi.toml` declares `cryptography = "*"` under `[dependencies]` so it's fetched from conda-forge (same glibc as the interpreter). If you're seeing this, you're on an old `pixi.toml` — re-rsync from the dev repo and `pixi install`. Do **not** move `ccxt` to `[dependencies]`; there's no conda-forge build (`No candidates were found for ccxt >=4`). |
| `pixi install` fails with `No candidates were found for ccxt >=4`                                    | `ccxt` was placed under `[dependencies]` (conda-forge) but has no conda-forge build.              | Move `ccxt = ">=4"` back to `[pypi-dependencies]`. The shipped `pixi.toml` already has it there.                        |
| `pixi run -e find-expired-symbols find-expired-symbols` fails with `creds error: file not found`     | `.DBCreds.yaml` isn't in `$FES_DIR/`, or isn't readable by the invoking user.                     | `ls -l "$FES_DIR/.DBCreds.yaml"`. Recreate per [DB credentials](#db-credentials), and — for shared installs — apply the multi-user permissions block. |
| Every symbol on some exchanges (typically **UPBIT**, **KRAKEN**, others) is `ERROR` with `detail: load_markets failed: <ex> GET https://…` — but `BINANCE` and other exchanges work fine on the same run | An SSL-inspection proxy (Zscaler / Palo Alto / Netskope) in the corp egress path is re-signing HTTPS for the affected hosts with an internal root CA. Python's Mozilla-only bundle (shipped by conda-forge `ca-certificates`) doesn't include it, so `ccxt.<ex>().load_markets()` raises `SSL: CERTIFICATE_VERIFY_FAILED`. Hosts on the proxy's SSL-bypass allowlist (e.g. Binance) are unaffected. | The tool calls `truststore.inject_into_ssl()` at startup so Python uses the OS-native trust store instead. On the server that means `/etc/ssl/certs/ca-certificates.crt` (Debian/Ubuntu) or `/etc/pki/tls/certs/ca-bundle.crt` (RHEL/CentOS). Confirm the corp root is present (`awk '/CN =/' /etc/ssl/certs/ca-certificates.crt \| grep -i zscaler` or similar). If it's missing, add it via your standard IT channel (e.g. `update-ca-trust`) — do **not** disable cert verification. Re-run and Upbit/Kraken should load. |
| Report contains blobs of HTML in the ERROR `detail` column (typical fragments: `<html>`, `<div class="pg red">`, `Your organization has selected Zscaler …`) | Sequel to the truststore fix: SSL now succeeds end-to-end, so the corp proxy inspects the request and returns an HTML block page (non-browser User-Agent policy on Cryptocurrency-categorised hosts). ccxt tries to JSON-parse the HTML, fails, and stuffs it into the `NetworkError` message which lands in the report. | The tool sends a Chrome 126 desktop User-Agent on all ccxt calls (matches what the exchanges' own web UIs send, so their WAFs accept it and Zscaler's non-browser policy doesn't fire), and collapses ERROR `detail` to a single line capped at 500 chars as a safety net. If HTML still appears, the corp policy is blocking on something other than UA — grab the full underlying error from the WARNING log line (`ccxt load_markets failed for <ex>: …`) and coordinate with IT to allowlist the tool's egress or add the exchange host to the proxy's SSL-bypass list. |
| Every symbol for an exchange (e.g. **HUOBI**, **HUOBIDM**, **HUOBICOINSWAP**) is `ERROR` with `detail: load_markets failed: blocked by corporate proxy (Zscaler wac_block.html); <ccxt_id> GET https://<host>/… 403 Forbidden — contact IT to allowlist this exchange host` | Zscaler's URL-category policy (Web Access Control, template `wac_block.html`) is denying outbound traffic to that host with **HTTP 403** — regardless of User-Agent. HTX/Huobi is the canonical example (US-sanctioned exchange, blocked by default on many enterprise tenants). | Corporate policy block — not fixable in code. Ask IT to allowlist the affected hosts for the machine/user running the tool (for HTX/Huobi: `api.huobi.pro`, `api.htx.com`, `api-aws.huobi.pro`, `*.hbdm.com`). If IT won't allowlist, accept the `ERROR` rows as "cannot verify from this network" — they'll show up consistently under `--errors-only`. The raw 14KB HTML is emitted at DEBUG (rerun with `-v` / `--log-level DEBUG`). |
| Every symbol on a **custom venue** (**NADO** / **POLYMARKETPERPS** / **INJECTIVE** / **RHLIGHTER** / **ARCUS** / **ONDOPERPS**) is `ERROR` with `detail: custom venue fetch failed: likely blocked by corporate network policy (TLS handshake closed before certificate exchange); … — contact IT to allowlist this host` | A firewall or proxy in the egress path is killing the connection **mid-handshake**, keyed on the TLS SNI hostname. TCP connects, the ClientHello goes out, and the peer returns **zero bytes** having presented no certificate. This is one layer earlier than the `wac_block.html` case above — nothing ever decrypted the request, so there's no block page to serve and no 403. A genuine venue outage produces the same signature, hence "likely". | Confirm with `openssl s_client -connect <host>:443 -servername <host> </dev/null`: `SSL handshake has read 0 bytes` plus `no peer certificate available` means a policy drop, not a certificate problem (ignore the `Verify return code: 0 (ok)` line — that's the default when nothing was ever verified). Then prove it's destination-specific by hitting a working custom venue from the same box: `curl -o /dev/null -w '%{http_code}\n' https://archive.prod.nado.xyz/v2/symbols` → `200`. Corporate policy block, not fixable in code — ask IT for a firewall / URL-filtering **allow** rule for the destination. This is **not** an SSL-inspection bypass request; no inspection is occurring on these connections, so a bypass rule won't help. **Not Vertex** — Vertex's archive hosts are gone (see next row); leftover DNS on those names produced this exact TLS signature and was misdiagnosed as a proxy block in Phase 7o. |
| Every **VERTEX** / **AVAVERTEX** / **BERAVERTEX** / **MNTVERTEX** / **SOVERTEX** symbol is `DELISTED` with `detail` mentioning July 2025 / Ink Foundation | Vertex Protocol shut down all EVM edges in July 2025 after merging with the Ink Foundation. The archive API no longer exists. The checker does not call the former hosts. | Expected. Remove those symbols from FH / repeater config. No IT ticket. |
| `pixi run -e find-expired-symbols …` fails for another user with a permission error on `.pixi/`     | Install tree isn't group/other readable, or missing +x on env binaries.                            | Rerun the [Multi-user permissions](#multi-user-permissions-optional) block. Verify with `namei -l "$FES_DIR/.pixi/envs/find-expired-symbols/bin/find-expired-symbols"` as the failing user. |
| `pixi run` wants to re-solve / re-download and fails on write to `.pixi/`                            | Someone changed `pixi.toml` or `pixi.lock` after the initial install and the invoking user can't write to `.pixi/`. | As the install owner: `pixi install -e find-expired-symbols` once to freshen the env. Other users can pass `--frozen` (`pixi run --frozen -e find-expired-symbols find-expired-symbols …`) to skip the up-to-date check. |
| Many `ERROR` rows across many exchanges, each row's `detail` starts with `unknown exchange_name=`   | The `exchange_name` values from the DB have no entry in the bundled `fh_symbol_check/data/exchange_mapping.yaml`. | Bump the wheel version on the dev box after adding entries, rerun the deploy flow. The mapping ships inside the wheel — no separate YAML file to sync. |
| **API only:** `curl http://127.0.0.1:8000/health` returns "connection refused" | The service isn't running, or is bound to a different port.                                        | `systemctl --user status find-expired-symbols`. If it's `inactive (dead)`, `systemctl --user start find-expired-symbols` and check `journalctl --user -u find-expired-symbols -n 100`. |
| **API only:** service dies within seconds of `systemctl --user start`, journal shows `creds error:` | Same as the CLI version — `.DBCreds.yaml` missing or unreadable by the service user.               | `sudo -u <service-user> cat $FES_DIR/.DBCreds.yaml` should succeed. If not, adjust perms per [Multi-user permissions](#multi-user-permissions-optional) or move the file to a location the service user owns and edit the pixi task. |
| **API only:** service stops when you log out                                                       | Linger wasn't enabled — user systemd instance is torn down on session close.                       | `sudo loginctl enable-linger $USER`. This is the single sudo-requiring step in the deploy. |
| **API only:** `systemctl --user restart find-expired-symbols` succeeds but the UI still shows old behaviour | Browser cached the old HTML / static assets.                                                       | Hard refresh (Ctrl-Shift-R / Cmd-Shift-R). The service itself has definitely restarted per `journalctl` — it's just your browser holding onto CSS/JS. |
| **API only:** POST /scans returns `422` with `"specify --hostname, --exchange-name, --symbol, or --all"` | Empty / missing filters. The API mirrors the CLI's argparse validation deliberately.               | Include at least one of the required filters in the JSON body: `{"all":true}` for a full scan, or `{"exchange_name":"HUOBI"}` etc. |

For anything not on this list, rerun the failing invocation with
`--log-level DEBUG` (CLI) or `journalctl --user -u find-expired-symbols
-n 200` (API) and look at the last error line before the process exited.

---

## Directory map after install

```
$FES_DIR/                                          # your chosen install directory
├── pixi.toml                                      # from the dev repo (rsync'd)
├── pixi.lock                                      # from the dev repo (rsync'd)
├── find_expired_symbols-<ver>-py3-none-any.whl    # from the dev-box build (rsync'd)
├── .DBCreds.yaml                                  # operator-provided, 600 (or 640 root:$FES_GROUP for shared installs)
└── .pixi/envs/find-expired-symbols/               # pixi-managed
    ├── bin/
    │   ├── find-expired-symbols                   # CLI console script — scheduler calls this by absolute path
    │   ├── find-expired-symbols-service           # API console script — systemd user unit calls this
    │   ├── uvicorn                                # (bundled via `uvicorn-standard`)
    │   ├── python
    │   └── pip
    └── lib/python3.11/site-packages/
        ├── fh_symbol_check/
        │   ├── cli.py, validator.py, reporter.py, ...
        │   ├── pipeline.py                        # shared orchestration (CLI + API)
        │   ├── api/
        │   │   ├── main.py, server.py, routes.py, views.py, jobs.py, workers.py, models.py
        │   │   ├── templates/*.html               # HTMX + Jinja UI
        │   │   └── static/{htmx.min.js, app.css}  # bundled static assets
        │   ├── data/exchange_mapping.yaml         # bundled config
        │   └── custom_venues/
        ├── check_delisted_symbol.py               # top-level helper
        ├── mysql_select_query.py                  # top-level helper
        ├── ccxt/                                  # runtime dep (PyPI)
        ├── pymysql/                               # runtime dep (conda-forge)
        ├── yaml/                                  # runtime dep (conda-forge, package name pyyaml)
        ├── cryptography/                          # runtime dep (conda-forge; sidesteps manylinux_2_28)
        ├── truststore/                            # runtime dep (conda-forge; OS trust store adapter)
        ├── fastapi/                               # runtime dep (conda-forge)
        ├── uvicorn/                               # runtime dep (conda-forge, package name uvicorn-standard)
        ├── jinja2/                                # runtime dep (conda-forge)
        └── multipart/                             # runtime dep (conda-forge, package name python-multipart)

~/.config/systemd/user/                            # only present if the API service is installed
└── find-expired-symbols.service                   # operator-installed from $FES_DIR/deploy/

/var/log/find-expired-symbols/                     # CLI scheduler output (optional; picked by your job's --output-file)
└── <timestamp>.json
```

The whole deploy is self-contained under `$FES_DIR/` and (for the API
track) `~/.config/systemd/user/find-expired-symbols.service`. Blowing
away `$FES_DIR` + disabling the systemd unit cleanly removes every
trace of the install (other than the scheduler output directory, which
is separate on purpose).


# current deploy process
# 1. pixi run -e dev python -m build --wheel
# 2. rsync dist/find_expired_symbols-0.1.0-py3-none-any.whl pixi.toml pixi.lock archy@SGP-DEVOPS-MBS-02:/home/archy/api/find-expired-symbols
# 3. ssh archy@SGP-DEVOPS-MBS-02
# 4. cd /home/archy/api/find-expired-symbols
# 5. pixi run -e find-expired-symbols pip install --no-deps --force-reinstall find_expired_symbols-0.1.0-py3-none-any.whl
# 6. systemctl --user restart find-expired-symbols