from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file='.env',extra='ignore',case_sensitive=False)
    app_name:str='V2Ray Monitor'; database_url:str='sqlite+aiosqlite:///./data/monitor.db'; bot_token:str=''; admin_ids:str=''; webapp_url:str='http://127.0.0.1:8000'; encryption_key:str=''; xray_binary:str='/usr/local/bin/xray'
    probe_timeout:float=8.0; probe_interval:int=60; probe_concurrency:int=10; sync_interval:int=300; max_subscription_bytes:int=5_000_000; max_nodes_per_subscription:int=2000
    force_join_enabled:bool=False; force_join_channel:str=''; force_join_url:str=''; card_number:str=''; card_holder:str=''
    @field_validator('probe_timeout')
    @classmethod
    def valid_timeout(cls,v):
        if not 1<=v<=60: raise ValueError('PROBE_TIMEOUT must be between 1 and 60 seconds')
        return v
    @field_validator('probe_interval','sync_interval')
    @classmethod
    def valid_interval(cls,v):
        if v<10: raise ValueError('interval must be at least 10 seconds')
        return v
    @field_validator('probe_concurrency')
    @classmethod
    def valid_concurrency(cls,v):
        if not 1<=v<=100: raise ValueError('PROBE_CONCURRENCY must be between 1 and 100')
        return v
    @field_validator('max_subscription_bytes','max_nodes_per_subscription')
    @classmethod
    def valid_limits(cls,v):
        if v<1: raise ValueError('limits must be positive')
        return v
    @property
    def admins(self)->set[int]:
        out=set()
        for x in self.admin_ids.split(','):
            try:
                if x.strip():out.add(int(x.strip()))
            except ValueError:pass
        return out
@lru_cache
def get_settings():return Settings()
