import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str
    secret_key: str
    vault_key: str
    token_hours: int
    cors_origins: list
    hub_host: str
    hub_api_port: int
    hub_api_user: str
    hub_api_password: str
    hub_wg_interface: str
    hub_public_endpoint: str
    hub_public_port: int
    edge_api_user: str
    edge_api_password: str


def load_settings() -> Settings:
    secret = _env("SECRET_KEY")
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY ausente o demasiado corta (mínimo 32 caracteres).")
    return Settings(
        pg_host=_env("POSTGRES_HOST", "mikrotik-manager-db"),
        pg_port=int(_env("POSTGRES_PORT", "5432")),
        pg_db=_env("POSTGRES_DB"),
        pg_user=_env("POSTGRES_USER"),
        pg_password=_env("POSTGRES_PASSWORD"),
        secret_key=secret,
        vault_key=_env("VAULT_KEY"),
        token_hours=int(_env("TOKEN_HOURS", "12")),
        cors_origins=[o for o in _env("CORS_ORIGINS").split(",") if o],
        hub_host=_env("HUB_HOST"),
        hub_api_port=int(_env("HUB_API_PORT", "8728")),
        hub_api_user=_env("HUB_API_USER"),
        hub_api_password=_env("HUB_API_PASSWORD"),
        hub_wg_interface=_env("HUB_WG_INTERFACE"),
        hub_public_endpoint=_env("HUB_PUBLIC_ENDPOINT"),
        hub_public_port=int(_env("HUB_PUBLIC_PORT", "51820")),
        edge_api_user=_env("EDGE_API_USER"),
        edge_api_password=_env("EDGE_API_PASSWORD"),
    )


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
