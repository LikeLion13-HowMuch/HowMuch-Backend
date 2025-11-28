from functools import lru_cache
from typing import Optional
from pydantic import AnyUrl  
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: AnyUrl  # postgresql+asyncpg://app:app@localhost:5432/howmuch
    SCHED_ENABLE: bool = True

    # 매일 00:00 실행
    SCHED_CRON_DAANGN: str = "0 0 * * *"
    SCHED_CRON_JOONGNA: str = "0 0 * * *"
    SCHED_CRON_BUNJANG: str = "0 0 * * *"

    CATEGORY_IPHONE: int = 1

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings():
    return Settings()