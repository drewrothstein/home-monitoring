"""
REST API for Home Monitor service.

Provides endpoints for:
- Configuration queries (locations, sites)
- Fetch run status and history
- Data maintenance (pruning)

Run with: uvicorn home_monitor.api:app --reload
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from home_monitor.database import (
    aggregate_span_circuit_readings,
    get_fetch_run_summaries,
    get_locations,
)
from home_monitor.site_config import get_sites

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Home Monitor API",
    description="REST API for home monitoring service configuration and status",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# =============================================================================
# Pydantic Models
# =============================================================================


class LocationResponse(BaseModel):
    """Response model for a location."""

    id: int
    name: str
    latitude: float
    longitude: float
    timezone: Optional[str] = None
    capacity_kw: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class SitesResponse(BaseModel):
    """Response model for sites configuration."""

    sites: Dict[str, Any]


class IntegrationSummary(BaseModel):
    """Summary of an integration's fetch results."""

    calls: int = 0
    success: int = 0
    failed: int = 0


class FetchRunSummary(BaseModel):
    """Response model for a fetch run summary."""

    id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str
    total_data_points: int = 0
    integrations_summary: Optional[Dict[str, IntegrationSummary]] = None
    error_message: Optional[str] = None


class LastFetchResponse(BaseModel):
    """Response model for the last fetch endpoint."""

    last_run: Optional[FetchRunSummary] = None
    recent_runs: List[FetchRunSummary] = []


class PruneSpanCircuitsRequest(BaseModel):
    """Request model for pruning span circuit readings."""

    last_days: int = Field(
        default=30,
        ge=1,
        description="Only aggregate readings older than this many days",
    )
    bucket_minutes: int = Field(
        default=30,
        ge=5,
        le=1440,
        description="Time bucket size in minutes for aggregation (5-1440)",
    )


class PruneSpanCircuitsResponse(BaseModel):
    """Response model for span circuit pruning."""

    rows_deleted: int
    rows_inserted: int
    buckets_processed: int
    net_reduction: int
    message: str


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/", tags=["Health"])
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "home-monitor-api"}


@app.get("/config/locations", response_model=List[LocationResponse], tags=["Configuration"])
async def get_config_locations():
    """
    Get all locations from the database.

    Returns a list of all configured locations with their coordinates,
    timezone, and solar capacity information.
    """
    try:
        locations = get_locations()
        return locations
    except Exception as e:
        logger.error(f"Error fetching locations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch locations: {str(e)}")


@app.get("/config/sites", response_model=SitesResponse, tags=["Configuration"])
async def get_config_sites():
    """
    Get site configuration from sites.json.

    Returns the full site configuration including all enabled integrations
    and their settings for each site.
    """
    try:
        sites = get_sites()
        return {"sites": sites}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Sites configuration not found: {str(e)}")
    except Exception as e:
        logger.error(f"Error fetching sites config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch sites config: {str(e)}")


@app.get("/last", response_model=LastFetchResponse, tags=["Status"])
async def get_last_fetch():
    """
    Get information about the last fetcher run.

    Returns the most recent fetch run summary including:
    - Start and completion timestamps
    - Status (running, success, error)
    - Per-integration call counts and success/failure rates
    - Any error messages
    """
    try:
        summaries = get_fetch_run_summaries(limit=5)

        if not summaries:
            return {"last_run": None, "recent_runs": []}

        # Convert to response models
        runs = []
        for summary in summaries:
            run = FetchRunSummary(
                id=summary["id"],
                started_at=summary["started_at"],
                completed_at=summary.get("completed_at"),
                status=summary["status"],
                total_data_points=summary.get("total_data_points", 0),
                integrations_summary=summary.get("integrations_summary"),
                error_message=summary.get("error_message"),
            )
            runs.append(run)

        return {"last_run": runs[0] if runs else None, "recent_runs": runs}
    except Exception as e:
        logger.error(f"Error fetching last run info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch last run info: {str(e)}")


@app.post("/prune/span/circuits", response_model=PruneSpanCircuitsResponse, tags=["Maintenance"])
async def prune_span_circuits(request: PruneSpanCircuitsRequest):
    """
    Aggregate and prune old span circuit readings.

    This endpoint aggregates readings older than `last_days` days into
    `bucket_minutes` time buckets to reduce storage. For each bucket:
    - Averages instant_power_w
    - Sums import_energy_wh and export_energy_wh
    - Keeps the latest timestamp

    This preserves the accuracy of energy totals while reducing granularity
    for older data.

    **Warning**: This operation modifies data and cannot be undone.
    """
    try:
        logger.info(
            f"Starting span circuit pruning: last_days={request.last_days}, "
            f"bucket_minutes={request.bucket_minutes}"
        )

        result = aggregate_span_circuit_readings(
            last_days=request.last_days,
            bucket_minutes=request.bucket_minutes,
        )

        message = (
            f"Processed {result['buckets_processed']} time buckets. "
            f"Deleted {result['rows_deleted']} rows, inserted {result['rows_inserted']} aggregated rows. "
            f"Net reduction: {result['net_reduction']} rows."
        )

        logger.info(f"Span circuit pruning complete: {message}")

        return PruneSpanCircuitsResponse(
            rows_deleted=result["rows_deleted"],
            rows_inserted=result["rows_inserted"],
            buckets_processed=result["buckets_processed"],
            net_reduction=result["net_reduction"],
            message=message,
        )
    except Exception as e:
        logger.error(f"Error during span circuit pruning: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to prune span circuits: {str(e)}")


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
