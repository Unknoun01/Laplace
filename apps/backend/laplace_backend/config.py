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
    #: Segundos que se recuerda el Diagnóstico. Sin fijar: 60 en la nube y 0 en local
    #: (D-142).
    overview_cache_s: int | None = None

    #: 127.0.0.1 y no `localhost`: compose publica las bases sólo en IPv4 de loopback, y
    #: `localhost` prueba antes `::1`, que no contesta, y tarda segundos en caer a IPv4.
    clickhouse_host: str = "127.0.0.1"
    clickhouse_port: int = 8123
    clickhouse_user: str = "laplace"
    clickhouse_password: str = "laplace"
    clickhouse_database: str = "laplace"
    clickhouse_secure: bool = False

    postgres_dsn: str = "postgresql://laplace:laplace@127.0.0.1:5433/laplace"
    #: Postgres sólo guarda metadatos y las tablas reservadas de Fases 3-4. Si no está
    #: disponible, la ingesta y la lectura de trazas siguen funcionando.
    postgres_enabled: bool = True

    # -- autenticación ----------------------------------------------------------------
    #: `auto` decide por el modo: en la nube se exige credencial, en local no. Es una
    #: exención **escrita**, no el efecto de que nadie compruebe nada: el modo local no
    #: tiene cuentas por diseño (D-010) y se dice por el log al arrancar. `true` y
    #: `false` fuerzan, y forzar `false` en la nube deja un aviso en cada arranque.
    auth_required: str = "auto"
    #: Código para crear la primera cuenta (D-127). Vacío: se genera uno al arrancar y se
    #: escribe en el log. Fijarlo sirve para despliegues automatizados.
    setup_token: str = ""

    cors_origins: str = "http://localhost:3000"

    #: Tamaño máximo de un cuerpo de petición, en MiB, tal como llega por el cable. Un
    #: lote OTLP normal son unos pocos megas; sin tope, una sola petición podía llenar
    #: la memoria del proceso que comparten todos los proyectos. Descomprimido se admite
    #: hasta `OTLP_EXPANSION` veces esto (ver `ingest/otlp.py`).
    max_body_mb: int = 32
    #: Proxies de los que se cree `X-Forwarded-For` y `X-Forwarded-Proto`, separados por
    #: comas: IPs, rangos CIDR o nombres de host. Vacío: no se cree a nadie y cuenta la IP
    #: de la conexión. Sin esto, cualquiera ponía la cabecera y se saltaba el freno de
    #: intentos por IP. Next no vale como proxy de confianza por sí solo: reenvía la
    #: cabecera del navegador sin tocarla. En compose va Caddy delante (deploy/Caddyfile).
    trusted_proxies: str = ""

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

    # -- alertas por correo (D-123) ----------------------------------------------------
    #: El servidor de correo es de la instalación, no de un proyecto: el destinatario se
    #: pone en la interfaz, pero la credencial SMTP es un secreto y vive en el entorno.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # -- retención (D-123) -------------------------------------------------------------
    #: Días que se guardan las trazas. 0 = para siempre, que es lo que había; se decide
    #: a propósito y no por omisión, porque borrar datos no se enciende solo.
    retention_days: int = 0

    # -- evaluaciones: LLM-as-judge (Fase 5) -----------------------------------------
    #: Apagado por defecto. El veredicto de máquina es opcional: anotar a mano funciona
    #: sin configurar nada, y correr un juez cuesta dinero de verdad.
    evals_judge_enabled: bool = False
    #: `anthropic` u `openai`. Decide la forma de la petición, no el proveedor de nadie.
    evals_judge_system: str = "anthropic"
    evals_judge_model: str = ""
    evals_judge_api_key: str = ""
    #: Para apuntar a una pasarela propia. Vacío = el endpoint oficial del proveedor.
    evals_judge_base_url: str = ""
    #: Tope de trazas que se pueden juzgar de una vez. Es un freno de mano: un juez
    #: suelto sobre diez mil trazas es una factura sorpresa, y este producto existe
    #: justamente para que no haya facturas sorpresa.
    evals_judge_max_batch: int = 200

    @property
    def auth_enforced(self) -> bool:
        """Si esta instalación exige credencial.

        El valor por defecto lo decide el almacén y no una variable suelta: `sqlite` es
        el modo local —un proceso en el portátil de quien escribe el agente, sin
        cuentas—, y `clickhouse` es un despliegue con datos de más de uno.
        """
        elegido = self.auth_required.strip().lower()
        if elegido in ("true", "1", "yes", "si", "sí"):
            return True
        if elegido in ("false", "0", "no"):
            return False
        return self.store != "sqlite"

    @property
    def cache_diagnostico_s(self) -> int:
        if self.overview_cache_s is not None:
            return max(0, self.overview_cache_s)
        return 60 if self.store == "clickhouse" else 0

    @property
    def max_body_bytes(self) -> int:
        return max(1, self.max_body_mb) * 1024 * 1024

    @property
    def trusted_proxy_list(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.trusted_proxies.split(",") if p.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
