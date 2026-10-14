"""Load settings from config/settings.toml into typed objects."""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    min_radius_m: int = 100
    max_radius_m: int = 5000
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


class Price(BaseModel):
    """USD per million tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)


class LLMConfig(BaseModel):
    """The memo writer. Switching provider or model is a config change, not a code change."""

    provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    model: str = "claude-sonnet-5-5"
    base_url: str = Field(
        default="", description="openai_compatible only; empty = the vendor default"
    )
    api_key_env: str = Field(
        default="ANTHROPIC_API_KEY", description="Name of the env var with the key; empty = no key"
    )
    timeout_seconds: float = Field(default=60, gt=0, description="Cap on one HTTP attempt")
    max_retries: int = Field(
        default=1, ge=0, description="SDK retries on 429/5xx/connection errors"
    )
    deadline_seconds: float = Field(
        default=120,
        gt=0,
        description="Budget for one whole memo, retries and regeneration included",
    )
    min_attempt_seconds: float = Field(
        default=15, gt=0, description="Don't start a regeneration with less time than this left"
    )
    max_output_tokens: int = Field(default=8000, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        default="medium", description="anthropic only; None = the model's default"
    )
    prompt_version: str = "v1"
    prices: dict[str, Price] = Field(default_factory=dict)


class Settings(BaseModel):
    paths: Paths
    cities: dict[str, CityConfig]
    comps: CompsConfig
    occupancy: OccupancyConfig
    market: MarketConfig
    geocoding: GeocodingConfig
    llm: LLMConfig = Field(default_factory=LLMConfig)

    def city(self, name: str) -> CityConfig:
        try:
            return self.cities[name.lower()]
        except KeyError as exc:
            known = ", ".join(sorted(self.cities))
            raise UnknownCityError(f"Unknown city '{name}'. Configured cities: {known}") from exc

    def resolve(self, path: Path) -> Path:
        """Relative paths in the config are relative to the project root."""
        return path if path.is_absolute() else PROJECT_ROOT / path


def load_env() -> None:
    """Read .env at the project root into the environment. Variables already set win."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)


def load_settings(path: Path | None = None) -> Settings:
    path = path or Path(os.environ.get("STRAGENT_CONFIG", DEFAULT_CONFIG))
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return Settings.model_validate(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
