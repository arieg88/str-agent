"""Load settings from config/settings.toml into typed objects."""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "settings.toml"


class UnknownCityError(ValueError):
    pass


class Paths(BaseModel):
    database: Path
    raw_dir: Path
    cache_dir: Path
    output_dir: Path


class CityConfig(BaseModel):
    display_name: str
    country_code: str
    base_url: str
    currency: str = "EUR"
    viewbox: tuple[float, float, float, float]

    def snapshot_url(self, snapshot: str) -> str:
        return self.base_url.format(snapshot=snapshot)


class CompsConfig(BaseModel):
    default_room_type: str = "Entire home/apt"
    radius_steps_m: list[int] = Field(default_factory=lambda: [500, 1000, 1500])
    min_comps: int = 10
    bedroom_tolerance: int = 1
    min_price: float = 10
    max_price: float = 2000


class OccupancyConfig(BaseModel):
    review_rate: float = Field(0.5, gt=0, le=1)
    average_stay_nights: float = Field(3.0, gt=0)
    max_occupancy: float = Field(0.70, gt=0, le=1)


class MarketConfig(BaseModel):
    multi_listing_threshold: int = 2


class GeocodingConfig(BaseModel):
    user_agent: str
    min_interval_seconds: float = 1.1


class Settings(BaseModel):
    paths: Paths
    cities: dict[str, CityConfig]
    comps: CompsConfig
    occupancy: OccupancyConfig
    market: MarketConfig
    geocoding: GeocodingConfig

    def city(self, name: str) -> CityConfig:
        try:
            return self.cities[name.lower()]
        except KeyError as exc:
            known = ", ".join(sorted(self.cities))
            raise UnknownCityError(f"Unknown city '{name}'. Configured cities: {known}") from exc

    def resolve(self, path: Path) -> Path:
        """Relative paths in the config are relative to the project root."""
        return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings(path: Path | None = None) -> Settings:
    path = path or Path(os.environ.get("STRAGENT_CONFIG", DEFAULT_CONFIG))
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return Settings.model_validate(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
