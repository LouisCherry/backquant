from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from app.auth import generate_auth_token
from app.api.system_api import bundle_status as _bundle_status
from app.market_data.analyzer import ensure_bundle_analysis_task
from app.database import DatabaseConnection, get_db_connection

bp_login = Blueprint("bp_login", __name__)

_BUNDLE_REQUIRED_FILES = ("future_info.json", "instruments.pk", "trading_dates.npy")


def _error_response(http_status: int, code: str, message: str):
    return jsonify({"error": {"code": code, "message": message}}), http_status


def _as_admin_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _parse_login_form() -> tuple[str, str]:
    data = request.form.to_dict() if request.form else {}
    if not data:
        data = request.get_json(silent=True) or {}
    username = data.get("username") or data.get("mobile")
    password = data.get("password")
    username = username.strip() if isinstance(username, str) else ""
    password = password if isinstance(password, str) else ""
    return username, password


def _invalid_credentials():
    return _error_response(401, "UNAUTHORIZED", "invalid credentials")

def _bundle_is_ready(bundle_path: Path) -> bool:
    if not bundle_path.exists() or not bundle_path.is_dir():
        return False
    try:
        if not any(bundle_path.iterdir()):
            return False
    except OSError:
        return False
    for filename in _BUNDLE_REQUIRED_FILES:
        candidate = bundle_path / filename
        try:
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


def _verify_bcrypt_password(password: str, password_hash: str) -> tuple[bool, tuple | None]:
    try:
        import bcrypt
    except ImportError:
        return False, _error_response(500, "AUTH_CONFIG_ERROR", "bcrypt is not installed")

    try:
        ok = bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False, _error_response(500, "AUTH_CONFIG_ERROR", "bcrypt hash is invalid")
    return ok, None


def _auth_db_path() -> Path:
    raw = str(current_app.config.get("AUTH_DB_PATH", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    base_dir = Path(str(current_app.config.get("BACKTEST_BASE_DIR", "/tmp"))).expanduser()
    return base_dir / "auth.sqlite3"


def _init_auth_db(db: DatabaseConnection) -> None:
    """Initialize auth DB schema. Only creates tables for SQLite; MariaDB tables
    are created by db/init.sql at container startup."""
    if db.config.db_type == 'sqlite':
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )


def _hash_password(plain_password: str) -> tuple[str | None, tuple | None]:
    try:
        import bcrypt
    except ImportError:
        return None, _error_response(500, "AUTH_CONFIG_ERROR", "bcrypt is not installed")

    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return hashed, None


def _ensure_default_admin(db: DatabaseConnection) -> tuple[bool, tuple | None]:
    _init_auth_db(db)
    configured_username = str(current_app.config.get("LOCAL_AUTH_MOBILE", "") or "").strip()
    password_hash = str(current_app.config.get("LOCAL_AUTH_PASSWORD_HASH", "") or "").strip()
    plain_password = str(current_app.config.get("LOCAL_AUTH_PASSWORD", "") or "")
    is_admin = _as_admin_flag(current_app.config.get("LOCAL_AUTH_IS_ADMIN", True))

    if not configured_username or (not password_hash and not plain_password):
        return False, _error_response(500, "AUTH_CONFIG_ERROR", "default admin config is incomplete")

    row = db.fetchone("SELECT id FROM users WHERE username = ?", (configured_username,))
    if row:
        return True, None

    if not password_hash:
        password_hash, err_resp = _hash_password(plain_password)
        if err_resp:
            return False, err_resp

    created_at = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    db.execute(
        "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
        (configured_username, password_hash, 1 if is_admin else 0, created_at),
    )
    return True, None


def _db_login(username: str, password: str):
    try:
        import bcrypt
    except ImportError:
        return _error_response(500, "AUTH_CONFIG_ERROR", "bcrypt is not installed")

    with get_db_connection('auth') as db:
        ok, err_resp = _ensure_default_admin(db)
        if err_resp:
            return err_resp
        if not ok:
            return _error_response(500, "AUTH_CONFIG_ERROR", "failed to init auth database")
        row = db.fetchone(
            "SELECT id, username, password_hash, is_admin FROM users WHERE username = ?",
            (username,),
        )
        if not row:
            return _invalid_credentials()
        if not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
            return _invalid_credentials()
        user_id = row["id"]
        is_admin = bool(row["is_admin"])
        token = generate_auth_token(user_id=user_id, is_admin=is_admin)
        return jsonify({"token": token, "userid": user_id, "is_admin": is_admin}), 200


@bp_login.post("/api/login")
def api_login():
    username, password = _parse_login_form()
    if not username or not password:
        return _error_response(400, "INVALID_ARGUMENT", "username and password are required")
    
    # 先进行登录认证，获取token
    auth_response = _db_login(username, password)
    
    # 然后检查bundle状态
    bundle_path = Path(current_app.config.get("RQALPHA_BUNDLE_PATH") or "").expanduser()
    if not bundle_path or not _bundle_is_ready(bundle_path):
        status_response = _bundle_status()
        status_payload = status_response.get_json(silent=True) if status_response else None
        
        # 如果认证成功，返回token和bundle状态
        if auth_response[1] == 200:
            auth_data = auth_response[0].get_json(silent=True) or {}
            payload = {
                "token": auth_data.get("token"),
                "userid": auth_data.get("userid"),
                "is_admin": auth_data.get("is_admin"),
                "code": "BUNDLE_NOT_READY",
                "message": "bundle is downloading",
                "bundle_status": status_payload or {},
            }
            return jsonify(payload), 200
        else:
            # 认证失败，返回认证错误
            return auth_response
    
    try:
        ensure_bundle_analysis_task(bundle_path)
    except Exception:
        current_app.logger.exception("failed to auto-trigger bundle analysis after login")
    
    return auth_response
