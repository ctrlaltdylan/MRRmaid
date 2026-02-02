"""Configuration management using pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Shopify Partner API settings
    shopify_partner_token: Optional[str] = Field(
        default=None,
        description="Shopify Partner API access token",
    )
    shopify_organization_id: Optional[str] = Field(
        default=None,
        description="Shopify Partner organization ID",
    )
    shopify_api_version: str = Field(
        default="2026-01",
        description="Shopify Partner API version",
    )

    # Stripe API settings
    stripe_api_key: Optional[str] = Field(
        default=None,
        description="Stripe API secret key",
    )

    # Database settings
    database_url: str = Field(
        default="sqlite:///mrrmaid.db",
        description="Database connection URL",
    )

    # Application settings
    data_dir: Path = Field(
        default=Path.home() / ".mrrmaid",
        description="Directory for storing application data",
    )

    @property
    def shopify_configured(self) -> bool:
        """Check if Shopify Partner API is configured."""
        return bool(self.shopify_partner_token and self.shopify_organization_id)

    @property
    def stripe_configured(self) -> bool:
        """Check if Stripe API is configured."""
        return bool(self.stripe_api_key)


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
