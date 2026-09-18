"""Pydantic models for the API's JSON boundary.

Mirrors :mod:`fh_symbol_check.pipeline` and :mod:`fh_symbol_check.models` at
the HTTP boundary. Kept as thin as possible — the model classes exist to give
FastAPI its OpenAPI schema and to validate request bodies; conversion helpers
live at the bottom.

Filter validation replicates :meth:`fh_symbol_check.pipeline.ScanFilters.validate`
so the API returns 422 with the same wording the CLI shows on the terminal —
users don't have to learn two error dialects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..pipeline import ScanFilters, ScanProgress
from ..models import SymbolResult

SourceSelector = Literal["fh", "rp", "both"]
JobState = Literal["queued", "running", "done", "failed"]


class ScanRequest(BaseModel):
    """POST /scans body. Field names/semantics match the CLI's argparse."""

    model_config = ConfigDict(extra="forbid")

    hostname: str | None = Field(default=None, description="hostname LIKE %<value>%")
    exchange_name: str | None = Field(
        default=None, description="Case-insensitive exact match on fh_config.exchange_name"
    )
    all: bool = Field(
        default=False,
        alias="all",
        description="Scan every producer row; mutually exclusive with hostname/exchange_name.",
    )
    symbol: list[str] | None = Field(
        default=None,
        description=(
            "One or more symbols (OR semantics). Case-sensitive; matched against both "
            "the FH internal form and the translated venue form. Implies --all when used alone."
        ),
    )
    source: SourceSelector = Field(default="both")
    errors_only: bool = Field(default=False)
    exchange_grouping: bool = Field(default=False)
    show_listed: bool = Field(default=False)
    concurrency: int = Field(default=4, ge=1, le=32)

    @model_validator(mode="after")
    def _validate_filter_combination(self) -> "ScanRequest":
        # Same error wording as the CLI's argparse so both fronts speak the
        # same dialect; users don't have to relearn error strings when
        # moving from CLI to API.
        if self.all and (self.hostname or self.exchange_name):
            raise ValueError(
                "--all cannot be combined with --hostname or --exchange-name"
            )
        if not (
            self.all or self.hostname or self.exchange_name or self.symbol
        ):
            raise ValueError(
                "specify --hostname, --exchange-name, --symbol, or --all"
            )
        return self

    def to_filters(self) -> ScanFilters:
        return ScanFilters(
            hostname=self.hostname,
            exchange_name=self.exchange_name,
            all_producers=self.all,
            symbols=tuple(self.symbol) if self.symbol else (),
            source=self.source,
            concurrency=self.concurrency,
        )


class ProgressOut(BaseModel):
    """Serialised :class:`ScanProgress` snapshot."""

    phase: str
    total_producers: int
    completed_producers: int
    rows_scanned: int

    @classmethod
    def from_progress(cls, p: ScanProgress) -> "ProgressOut":
        return cls(
            phase=p.phase,
            total_producers=p.total_producers,
            completed_producers=p.completed_producers,
            rows_scanned=p.rows_scanned,
        )


class SymbolResultOut(BaseModel):
    """One row of scan output. Mirrors :class:`fh_symbol_check.models.SymbolResult`
    field-for-field so consumers can round-trip freely between the CLI's JSON
    output and the API's."""

    source: str
    service_id: int | None
    fh_name: str
    hostname: str
    exchange_name: str
    ccxt_id: str
    original_symbol: str
    ccxt_symbol: str
    status: str
    detail: str

    @classmethod
    def from_result(cls, r: SymbolResult) -> "SymbolResultOut":
        return cls(
            source=r.source,
            service_id=r.service_id,
            fh_name=r.fh_name,
            hostname=r.hostname,
            exchange_name=r.exchange_name,
            ccxt_id=r.ccxt_id,
            original_symbol=r.original_symbol,
            ccxt_symbol=r.ccxt_symbol,
            status=r.status,
            detail=r.detail,
        )


class ScanJobSummary(BaseModel):
    """One row in ``GET /scans``. Result rows are elided at the summary
    level — clients follow the ``id`` to ``GET /scans/{id}`` for detail."""

    id: str
    state: JobState
    filters: dict[str, Any]
    progress: ProgressOut
    created_at: datetime
    completed_at: datetime | None
    error: str | None


class ScanJobDetail(ScanJobSummary):
    """Full ``GET /scans/{id}`` payload. ``result`` is populated once
    ``state == "done"``."""

    result: list[SymbolResultOut] | None


class ScanJobList(BaseModel):
    """``GET /scans`` payload."""

    jobs: list[ScanJobSummary]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    active_scans: int
    recent_scans: int
