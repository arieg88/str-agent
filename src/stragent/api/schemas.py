"""Request and response models specific to the HTTP API.

Results reuse the models in ``stragent.models`` (Analysis, Seasonality), so the CLI JSON and the
API JSON are the same contract.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from stragent.analytics.comps import CompQuality


class CompFilterParams(BaseModel):
    """Comp filters shared by every endpoint that picks comps."""

    bedrooms: int | None = Field(None, ge=0, le=20)
    room_type: str | None = Field(None, description="Default: Entire home/apt")
    min_reviews: int = Field(0, ge=0, description="Min reviews in the last 12 months")
    min_rating: float | None = Field(None, ge=0, le=5)
    max_min_nights: int | None = Field(None, ge=1)
    superhost_only: bool = False
    radius_m: int | None = Field(
        None, description="Search radius in metres; default: auto (500 m widened as needed)"
    )
    snapshot: str | None = Field(None, description="YYYY-MM-DD; default: latest loaded")
    city: str = "thessaloniki"

    def quality(self) -> CompQuality:
        return CompQuality(
            min_reviews_ltm=self.min_reviews,
            min_rating=self.min_rating,
            max_minimum_nights=self.max_min_nights,
            superhost_only=self.superhost_only,
        )


class AnalyzeRequest(CompFilterParams):
    address: str | None = Field(None, description="Street address in the city")
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    show: int = Field(15, ge=1, le=500, description="How many comps to return")

    @model_validator(mode="after")
    def _location_given(self) -> AnalyzeRequest:
        if not self.address and (self.lat is None or self.lon is None):
            raise ValueError("Give either 'address' or both 'lat' and 'lon'.")
        return self

    def query(self) -> str:
        if self.lat is not None and self.lon is not None:
            return f"{self.lat}, {self.lon}"
        assert self.address is not None
        return self.address


class CompsRequest(AnalyzeRequest):
    """GET /api/comps query string."""

    show: int = Field(500, ge=1, le=1000, description="How many comps to return")


class SeasonalityRequest(AnalyzeRequest):
    """GET /api/seasonality query string."""

    months: int = Field(24, ge=3, le=60, description="Months of review history")


class ErrorResponse(BaseModel):
    error: str = Field(description="Machine-readable error code")
    detail: str
    request_id: str | None = None


class SnapshotInfo(BaseModel):
    city: str
    snapshot_date: str
    listings: int
    calendar_rows: int
    reviews: int
    loaded_at: str
    source: str


class Health(BaseModel):
    status: str
    database: bool
    database_path: str
    snapshots: list[SnapshotInfo]


class NeighbourhoodSummary(BaseModel):
    name: str
    group: str | None
    listings: int
    entire_home_share: float | None
    median_price_entire_home: float | None
    median_reviews_ltm: float | None
    multi_listing_host_share: float | None


class NeighbourhoodDetail(NeighbourhoodSummary):
    snapshot_date: str
    geometry: dict[str, Any] = Field(description="GeoJSON geometry")
