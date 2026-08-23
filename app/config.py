from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_name: str = 'V2Ray Monitor'
    database_url: str = 'sqlite+aiosqlite:///./data/monitor.db'
    bot_token: str = ''
    admin_ids: str = ''
    webapp_url: str = 'http://127.0.0.1:8000'
    encryption_key: str = ''
    xray_binary: str = '/usr/local/bin/xray'
    probe_timeout: float = 8.0
    sync_interval: int = 300
    max_subscription_bytes: int = 5_000_000
    max_nodes_per_subscription: int = 2000
    public_cache_seconds: int = 5

    @property
    def admins(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_ids.split(',') if x.strip().isdigit()}

@lru_cache
def get_settings() -> Settings:
    return Settings()
