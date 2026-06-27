"""Data models for report generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from home_monitor.report.periods import PeriodBounds, PeriodType


@dataclass
class SiteReportSummary:
    id: Optional[int]
    location_id: int
    location_name: str
    period: PeriodBounds
    metrics: Dict[str, Any]
    status: str = "computed"
    error_message: Optional[str] = None


@dataclass
class ReportBatch:
    period_type: PeriodType
    batch_period: PeriodBounds
    site_summaries: List[SiteReportSummary] = field(default_factory=list)
    generated_at: Optional[datetime] = None
    delivery_id: Optional[int] = None
    summary_ids: List[int] = field(default_factory=list)

    @property
    def subject(self) -> str:
        from home_monitor.report.periods import period_type_title

        return (
            f"Home Monitor {period_type_title(self.period_type)} Report"
            f" — {self.batch_period.label}"
        )
