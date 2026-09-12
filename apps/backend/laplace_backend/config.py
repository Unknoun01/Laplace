"""Configuración del backend."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LAPLACE_", env_file=".env", extra="ignore")

    #: `clickhouse` en la nube, `sqlite` en local. Es lo ÚNICO que cambia entre los
    #: dos modos: la ingesta, la API, las reglas y la interfaz son el mismo código.
    store: str = "clickhouse"
    #: Fichero del modo local. `laplace ui` lo pone en `~/.laplace/laplace.db`.
    sqlite_path: str = "laplace.db"

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

    # -- alertas a Slack (Fase 2, punto 3) -------------------------------------------
    #: Apagadas por defecto. Nada que mande mensajes fuera se enciende solo.
    alerts_enabled: bool = False
    #: El webhook vive en el entorno y no en el fichero de ajustes: es un secreto.
    alerts_slack_webhook: str = ""
    #: JSON con los ajustes por proyecto (umbral, silencio, webhook propio). Ver
    #: `AlertConfig` en `alerts.py` para el formato.
    alerts_config_path: str = ""
    #: Raíz pública de la interfaz, para el enlace a la ficha del problema. Sin esto
    #: la alerta se manda sin enlace: mejor eso que un enlace a ninguna parte.
    alerts_base_url: str = ""
    #: Cada cuánto se repasan los proyectos. No es la frecuencia con la que se avisa:
    #: de eso se encarga el periodo de calma por hallazgo (D-074).
    alerts_interval_seconds: int = 300
    #: Ventana de análisis de las alertas, y umbral por defecto en dólares YA GASTADOS.
    alerts_window_days: int = 7
    alerts_min_usd: float = 1.0
    #: Horas de silencio por hallazgo tras avisar de él.
    alerts_quiet_hours: float = 24.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
