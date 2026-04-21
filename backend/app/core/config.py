from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_secret_key: str = "dev-secret-change-me"

    database_url: str = "sqlite+aiosqlite:///./atlas.db"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: str = "mock"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-latest"

    # Third-party integrations (all optional; empty string => offline fallback)
    tavily_api_key: str = ""
    tavily_max_results: int = 10
    brave_api_key: str = ""
    brave_max_results: int = 10
    site_crawler_enabled: bool = True
    site_crawler_max_per_discovery: int = 8

    cors_origins: str = "http://localhost:3000"

    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
