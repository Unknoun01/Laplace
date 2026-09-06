"""Configuración del backend."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LAPLACE_", env_file=".env", extra="ignore")

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "laplace"
    clickhouse_password: str = "laplace"
    clickhouse_database: str = "laplace"
    clickhouse_secure: bool = False

    postgres_dsn: str = "postgresql://laplace:laplace@localhost:5433/laplace"
    #: Postgres sólo guarda metadatos y las tablas reservadas de Fases 3-4. Si no está
    #: disponible, la ingesta y la lectura de trazas siguen funcionando.
    postgres_enabled: bool = True

    cors_origins: str = "http://localhost:3000"

    #: Aplica las migraciones de ClickHouse y Postgres al arrancar. En un despliegue
    #: real esto lo haría un job aparte; con un solo proceso es más simple así.
    auto_migrate: bool = True

    log_level: str = "info"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
