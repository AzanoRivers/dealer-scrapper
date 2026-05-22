from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # === API ===
    PROJECT_NAME: str = "DealerScrapper"
    API_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_KEY: str = "test-key-12345678901234567890123456"

    # === CORS ===
    CORS_ORIGINS: list[str] = ["*"]

    # === LLM Provider (principal) ===
    LLM_PROVIDER: str = "zai"
    LLM_MODEL: str = "glm-5.1"
    LLM_API_KEY: str = ""
    LLM_MAX_TOKENS: int = 6000
    LLM_TEMPERATURE: float = 0.2

    # === LLM Fallback (emergencia — si el principal no responde en 2 min) ===
    LLM_FALLBACK_PROVIDER: str = "nvidia"
    LLM_FALLBACK_MODEL: str = "moonshotai/kimi-k2.6"
    LLM_FALLBACK_API_KEY: str = ""

    # === Crawler ===
    MAX_CONCURRENT_FETCHES: int = 3
    MAX_PAGES_PER_JOB: int = 50
    FETCH_TIMEOUT_SECONDS: int = 15
    FETCH_RETRIES: int = 3

    # === Timeouts y ciclo de vida ===
    LLM_WATCHDOG_SECONDS: int = 300
    JOB_MAX_DURATION_SECONDS: int = 1800
    RESULT_TTL_MINUTES: int = 15

    # === Storage ===
    JOB_BASE_DIR: str = "/tmp/dealerscrapper"

    # === Imagenes ===
    DOWNLOAD_IMAGES: bool = False
    MAX_IMAGE_SIZE_MB: int = 5

    # === Auditor ===
    AUDIT_COVERAGE_MIN_PERCENT: int = 30
    AUDIT_REFETCH_ENABLED: bool = True
    AUDIT_MAX_NEW_ROUTES: int = 20


settings = Settings()
