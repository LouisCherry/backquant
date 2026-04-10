#coding: utf8

import os
from pathlib import Path

basedir = os.path.abspath(os.path.dirname(__file__))
project_root = Path(basedir).parent


def _abs_path_from_env(env_name: str, default: str) -> str:
    raw = os.environ.get(env_name, default)
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    return str((project_root / path).resolve())


def _int_from_env(env_name: str, default: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _list_from_env(env_name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


def _bool_from_env(env_name: str, default: bool) -> bool:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _str_from_env(env_name: str, default: str) -> str:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    return raw.strip()


class Config:
    FLASKY_POSTS_PER_PAGE = 20
    SECRET_KEY = _str_from_env("SECRET_KEY", "change-me")
    JWT_EXPIRES_HOURS = _int_from_env("JWT_EXPIRES_HOURS", 24)
    LOCAL_AUTH_MOBILE = _str_from_env("LOCAL_AUTH_MOBILE", "admin")
    LOCAL_AUTH_PASSWORD = os.environ.get("LOCAL_AUTH_PASSWORD", "")
    LOCAL_AUTH_PASSWORD_HASH = os.environ.get("LOCAL_AUTH_PASSWORD_HASH", "")
    LOCAL_AUTH_USER_ID = _int_from_env("LOCAL_AUTH_USER_ID", 1)
    LOCAL_AUTH_IS_ADMIN = _bool_from_env("LOCAL_AUTH_IS_ADMIN", True)
    AUTH_DB_PATH = _str_from_env("AUTH_DB_PATH", "")
    RQALPHA_BUNDLE_PATH = _abs_path_from_env(
        "RQALPHA_BUNDLE_PATH",
        "/home/app/.rqalpha/bundle",
    )
    # Optional explicit command used to launch rqalpha, for example:
    # "/home/app/backquant/backtest/.venv/bin/rqalpha" or "python -m rqalpha".
    RQALPHA_COMMAND = _str_from_env("RQALPHA_COMMAND", "")
    BACKTEST_BASE_DIR = _abs_path_from_env(
        "BACKTEST_BASE_DIR",
        "/home/app/rqalpha_platform_storage",
    )
    # Optional path to strategy rename metadata DB (default: <BACKTEST_BASE_DIR>/backtest_meta.sqlite3).
    BACKTEST_RENAME_DB_PATH = _str_from_env("BACKTEST_RENAME_DB_PATH", "")
    BACKTEST_TIMEOUT = _int_from_env("BACKTEST_TIMEOUT", 900)
    BACKTEST_COMPILE_TIMEOUT = _int_from_env("BACKTEST_COMPILE_TIMEOUT", 10)
    BACKTEST_KEEP_DAYS = _int_from_env("BACKTEST_KEEP_DAYS", 30)
    BACKTEST_IDEMPOTENCY_WINDOW_SECONDS = _int_from_env("BACKTEST_IDEMPOTENCY_WINDOW_SECONDS", 30)
    BACKTEST_ALLOWED_FREQUENCIES = _list_from_env("BACKTEST_ALLOWED_FREQUENCIES", ("1d", "1m", "tick"))
    # Market data database path (default: <BACKTEST_BASE_DIR>/market_data.sqlite3).
    MARKET_DATA_DB_PATH = _str_from_env("MARKET_DATA_DB_PATH", "")
    # Database configuration (SQLite or MariaDB)
    DB_TYPE = _str_from_env("DB_TYPE", "sqlite")
    DB_HOST = _str_from_env("DB_HOST", "localhost")
    DB_PORT = _int_from_env("DB_PORT", 3306)
    DB_NAME = _str_from_env("DB_NAME", "backquant")
    DB_USER = _str_from_env("DB_USER", "root")
    DB_PASSWORD = _str_from_env("DB_PASSWORD", "")
    # VnPy futures bar data table
    DB_TABLE = _str_from_env("DB_TABLE", "dbbardata")
    # ==================== 数据存储配置 ====================
    # 数据源类型：'parquet' (推荐) 或 'hdf5' (旧版)
    # 切换此项将影响回测引擎、数据同步和API接口的数据读取行为
    DATA_SOURCE = _str_from_env("DATA_SOURCE", "parquet")
    
    # 数据源路径配置
    # HDF5 数据目录
    DATA_PATH_HDF5 = _abs_path_from_env(
        "DATA_PATH_HDF5",
        "data/hdf5",
    )
    # Parquet 数据目录
    DATA_PATH_PARQUET = _abs_path_from_env(
        "PARQUET_ROOT_DIR",
        "data/parquet",
    )
    
    # 双重写入模式：迁移期间同时写入 Parquet 和 HDF5
    # 设置为 True 时，数据同步脚本会同时写入两种格式
    # 设置为 False 时，只写入 DATA_SOURCE 指定的格式
    DUAL_WRITE_ENABLED = _bool_from_env("DUAL_WRITE_ENABLED", True)
    
    # Default storage type for market data: 'parquet' or 'db'
    MARKET_DATA_STORAGE_TYPE = _str_from_env("MARKET_DATA_STORAGE_TYPE", "parquet")
    # Research workbench storage and notebook session settings.
    # RESEARCH_PUBLIC_BASE_URL is optional; when empty, request.host_url is used.
    RESEARCH_PUBLIC_BASE_URL = _str_from_env("RESEARCH_PUBLIC_BASE_URL", "")
    RESEARCH_NOTEBOOK_PROXY_BASE = _str_from_env("RESEARCH_NOTEBOOK_PROXY_BASE", "/jupyter")
    # Optional base URL for the Jupyter server API (defaults to <public_base_url><proxy_base>).
    RESEARCH_NOTEBOOK_API_BASE = _str_from_env("RESEARCH_NOTEBOOK_API_BASE", "")
    # Optional Jupyter API token (if Jupyter auth is enabled).
    RESEARCH_NOTEBOOK_API_TOKEN = _str_from_env("RESEARCH_NOTEBOOK_API_TOKEN", "")
    RESEARCH_NOTEBOOK_API_TIMEOUT_SECONDS = _int_from_env("RESEARCH_NOTEBOOK_API_TIMEOUT_SECONDS", 3)
    # Optional notebook filesystem root (default: project root).
    RESEARCH_NOTEBOOK_ROOT_DIR = _str_from_env("RESEARCH_NOTEBOOK_ROOT_DIR", "")
    RESEARCH_SESSION_TTL_SECONDS = _int_from_env("RESEARCH_SESSION_TTL_SECONDS", 2 * 60 * 60)

    @staticmethod
    def init_app(app):
        pass


class ProductionConfig(Config):
    DEBUG = False


class DevelopmentConfig(Config):
    DEBUG = True


CONFIG = {
    "product": ProductionConfig,
    "production": ProductionConfig,
    "development": DevelopmentConfig,
    "default": DevelopmentConfig,
}

CONFIG_ENV = os.environ.get("CONFIG_ENV", "development")
