"""FastAPI service + HTMX browser UI for find-expired-symbols.

Runs as a long-lived process (systemd user unit — see ``deploy.md``) and
exposes the same scanning capability as the CLI via ``POST /scans`` + polled
``GET /scans/{id}``. Both fronts share :func:`fh_symbol_check.pipeline.run_scan`.
"""
