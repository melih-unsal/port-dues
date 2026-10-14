from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    openai_api_key: str = ""
    # Model per role. Every role defaults to the cheapest model; the compiler is the one worth upgrading
    # first if rule quality suffers, since its output drives every answer.
    compiler_model: str = "gpt-6-luna"
    judge_model: str = "gpt-6-luna"
    fast_model: str = "gpt-6-luna"
    # USD per 1M tokens. Used for the cost shown in the app and registered with Langfuse so its
    # dashboards price the same calls the same way. cached_input defaults to the full input price
    # until a discounted rate is confirmed, so costs are upper bounds rather than guesses.
    model_prices: dict[str, dict[str, float]] = {
        "gpt-6-luna": {"input": 0.10, "cached_input": 0.10, "output": 0.50},
    }
    embedding_model: str = "text-embedding-3-small"
    effort_fast: Literal["minimal", "low", "medium", "high"] = "low"
    effort_compiler: Literal["minimal", "low", "medium", "high"] = "medium"
    embedding_dim: int = 1536

    database_url: str = "postgresql://portdues:portdues@localhost:5434/portdues"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "tariff_nodes"
    data_dir: Path = Path("data")

    # "auto" sends only pages with tables/figures to the vision model
    vision_pages: Literal["auto", "all", "none"] = "auto"
    llm_concurrency: int = 8
    agent_max_steps: int = 30  # model turns per compiler agent
    # independent compiles per charge; with 2+ the engine keeps the rule the samples agree on
    compile_samples: int = 2

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"
    # what the browser uses for trace links (differs from langfuse_host inside docker)
    langfuse_public_url: str = "http://localhost:3000"
    langfuse_project_id: str = "portdues"

    laya_enabled: bool = True
    laya_model: str = "convaiinnovations/laya"
    laya_device: str | None = None

    cors_origins: list[str] = ["http://localhost:5173"]

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # A project .env should win over whatever happens to be exported in the shell.
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return settings
