from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit, urlunsplit

import requests
from flask import Blueprint, current_app, g, jsonify, request

from app.auth import ExpiredSignatureError, InvalidTokenError, decode_auth_token

bp_research = Blueprint("bp_research", __name__, url_prefix="/api/research")

_ALLOWED_RESEARCH_STATUS = {"DRAFT", "ACTIVE", "ARCHIVED"}
_ALLOWED_SESSION_STATUS = {"RUNNING", "STOPPED"}
_DEFAULT_KERNEL = "python3"
_DEFAULT_ITEM_STATUS = "DRAFT"
_DEFAULT_NOTEBOOK_PLACEHOLDERS = {
    "research/notebooks/workbench.ipynb",
    "workbench.ipynb",
}

_store_lock = threading.RLock()
_session_locks: dict[str, threading.RLock] = {}


def _error(message: str, status: int):
    return jsonify({"message": message}), status


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _research_auth_required(func):
    @wraps(func)
    def _wrapped(*args, **kwargs):
        token = (request.headers.get("Authorization") or "").strip()
        if not token:
            return _error("token is missing", 401)
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
            if not token:
                return _error("token is missing", 401)

        try:
            payload = decode_auth_token(token)
        except ExpiredSignatureError:
            return _error("token has expired", 401)
        except InvalidTokenError:
            return _error("invalid token", 401)

        user_id = payload.get("user_id")
        if user_id in (None, ""):
            return _error("invalid token", 401)
        g.user_id = user_id
        g.is_admin = _as_bool(payload.get("is_admin", False))
        return func(*args, **kwargs)

    return _wrapped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _research_storage_dir() -> Path:
    base_dir = Path(str(current_app.config.get("BACKTEST_BASE_DIR", "/tmp")))
    storage_dir = base_dir / "research"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _items_path() -> Path:
    return _research_storage_dir() / "items.json"


def _sessions_path() -> Path:
    return _research_storage_dir() / "sessions.json"


def _write_json_atomic(path: Path, payload: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _read_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _listify_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tags must be an array of strings")
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("tags must be an array of strings")
        text = item.strip()
        if text:
            tags.append(text)
    return tags


def _validate_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("id must be a string")
    text = value.strip()
    if not text:
        raise ValueError("id is required")
    if len(text) > 128:
        raise ValueError("id length must be <= 128")
    invalid = {"/", "\\", " ", "\t", "\n", "\r"}
    if any(ch in invalid for ch in text):
        raise ValueError("id contains invalid characters")
    return text


def _validate_notebook_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("notebook_path is required")
    notebook_path = value.strip()
    if notebook_path.startswith("/") or ".." in notebook_path.split("/"):
        raise ValueError("notebook_path must be a relative safe path")
    if not notebook_path.lower().endswith(".ipynb"):
        raise ValueError("notebook_path must end with .ipynb")
    return notebook_path


def _default_notebook_dir() -> str:
    configured = str(current_app.config.get("RESEARCH_NOTEBOOK_DEFAULT_DIR", "") or "").strip()
    if configured:
        return configured
    root_dir = str(current_app.config.get("RESEARCH_NOTEBOOK_ROOT_DIR", "") or "").strip()
    if root_dir:
        return ""
    return "research/notebooks"


def _default_notebook_path(research_id: str) -> str:
    base_dir = _default_notebook_dir()
    filename = f"{research_id}.ipynb"
    if not base_dir or base_dir == ".":
        return filename
    return f"{base_dir.rstrip('/')}/{filename}"


def _is_placeholder_notebook_path(value: str) -> bool:
    return value in _DEFAULT_NOTEBOOK_PLACEHOLDERS


def _root_dir_is_legacy_notebooks_dir() -> bool:
    configured = str(current_app.config.get("RESEARCH_NOTEBOOK_ROOT_DIR", "") or "").strip()
    if not configured:
        return False
    try:
        root = Path(configured).expanduser().resolve()
    except OSError:
        return False
    legacy_parts = ("research", "notebooks")
    if len(root.parts) < len(legacy_parts):
        return False
    return root.parts[-len(legacy_parts) :] == legacy_parts


def _strip_legacy_dir_prefix(notebook_path: str) -> str:
    if not _root_dir_is_legacy_notebooks_dir():
        return notebook_path
    legacy_prefix = "research/notebooks/"
    if notebook_path.startswith(legacy_prefix):
        return notebook_path[len(legacy_prefix) :]
    return notebook_path


def _select_notebook_path(research_id: str, requested: Any, existing: Any | None = None) -> str:
    candidate: str | None = None
    for raw in (requested, existing):
        if isinstance(raw, str):
            text = raw.strip()
            if text and not _is_placeholder_notebook_path(text):
                candidate = text
                break
    if candidate is None:
        candidate = _default_notebook_path(research_id)
    else:
        candidate = _strip_legacy_dir_prefix(candidate)
    return _validate_notebook_path(candidate)


def _validate_item_payload(data: dict, *, require_id: bool, require_notebook_path: bool = True) -> dict:
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")

    normalized: dict[str, Any] = {}

    if require_id:
        normalized["id"] = _validate_id(data.get("id"))
    elif "id" in data and data.get("id") not in (None, ""):
        normalized["id"] = _validate_id(data.get("id"))

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required")
    normalized["title"] = title.strip()

    description = data.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    normalized["description"] = description.strip()

    if "notebook_path" in data:
        notebook_path = data.get("notebook_path")
        if notebook_path is None or (isinstance(notebook_path, str) and not notebook_path.strip()):
            if require_notebook_path:
                raise ValueError("notebook_path is required")
        else:
            normalized["notebook_path"] = _validate_notebook_path(notebook_path)
    elif require_notebook_path:
        raise ValueError("notebook_path is required")

    kernel = data.get("kernel", _DEFAULT_KERNEL)
    if not isinstance(kernel, str) or not kernel.strip():
        raise ValueError("kernel must be a non-empty string")
    normalized["kernel"] = kernel.strip()

    status = data.get("status", _DEFAULT_ITEM_STATUS)
    if not isinstance(status, str) or not status.strip():
        raise ValueError("status must be a non-empty string")
    status = status.strip().upper()
    if status not in _ALLOWED_RESEARCH_STATUS:
        allowed = ", ".join(sorted(_ALLOWED_RESEARCH_STATUS))
        raise ValueError(f"status must be one of: {allowed}")
    normalized["status"] = status

    normalized["tags"] = _listify_tags(data.get("tags", []))

    return normalized


def _normalize_item_record(payload: dict) -> dict:
    now_iso = _iso(_now())
    return {
        "id": str(payload.get("id") or ""),
        "title": str(payload.get("title") or ""),
        "description": str(payload.get("description") or ""),
        "notebook_path": str(payload.get("notebook_path") or ""),
        "kernel": str(payload.get("kernel") or _DEFAULT_KERNEL),
        "status": str(payload.get("status") or _DEFAULT_ITEM_STATUS),
        "tags": _listify_tags(payload.get("tags", [])) if isinstance(payload.get("tags", []), list) else [],
        "created_at": str(payload.get("created_at") or now_iso),
        "updated_at": str(payload.get("updated_at") or now_iso),
    }


def _load_items() -> dict[str, dict]:
    raw = _read_json_dict(_items_path())
    items: dict[str, dict] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        item = _normalize_item_record(value)
        if not item["id"]:
            item["id"] = key
        items[item["id"]] = item
    return items


def _save_items(items: dict[str, dict]) -> None:
    serializable = {item_id: _normalize_item_record(item) for item_id, item in items.items()}
    _write_json_atomic(_items_path(), serializable)


def _load_sessions() -> dict[str, dict]:
    raw = _read_json_dict(_sessions_path())
    sessions: dict[str, dict] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        status = str(value.get("status") or "RUNNING").upper()
        if status not in _ALLOWED_SESSION_STATUS:
            status = "RUNNING"
        sessions[key] = {
            "session_id": str(value.get("session_id") or ""),
            "research_id": key,
            "notebook_path": str(value.get("notebook_path") or ""),
            "kernel": str(value.get("kernel") or _DEFAULT_KERNEL),
            "kernel_id": str(value.get("kernel_id") or ""),
            "kernel_status": str(value.get("kernel_status") or ""),
            "session_token": str(value.get("session_token") or ""),
            "notebook_url": str(value.get("notebook_url") or ""),
            "embed_url": str(value.get("embed_url") or ""),
            "status": status,
            "started_at": str(value.get("started_at") or _iso(_now())),
            "last_active_at": str(value.get("last_active_at") or _iso(_now())),
            "expires_at": str(value.get("expires_at") or _iso(_now() + timedelta(hours=2))),
        }
    return sessions


def _save_sessions(sessions: dict[str, dict]) -> None:
    _write_json_atomic(_sessions_path(), sessions)


def _session_status_for_item(session: dict | None) -> tuple[str | None, bool]:
    if session is None:
        return None, False
    status = str(session.get("status") or "RUNNING")
    if status == "RUNNING":
        return status, False
    return status, False


def _session_lock_for(research_id: str) -> threading.RLock:
    with _store_lock:
        lock = _session_locks.get(research_id)
        if lock is None:
            lock = threading.RLock()
            _session_locks[research_id] = lock
        return lock


def _session_ttl_seconds() -> int:
    raw = current_app.config.get("RESEARCH_SESSION_TTL_SECONDS", 2 * 60 * 60)
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        ttl = 2 * 60 * 60
    return max(60, ttl)


def _host_base_url() -> str:
    configured = str(current_app.config.get("RESEARCH_PUBLIC_BASE_URL", "") or "").strip()
    if configured:
        return configured.rstrip("/")
    return request.host_url.rstrip("/")


def _proxy_base_path() -> str:
    raw = str(current_app.config.get("RESEARCH_NOTEBOOK_PROXY_BASE", "/jupyter") or "/jupyter").strip()
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw.rstrip("/")


def _notebook_api_base_url() -> str:
    configured = str(current_app.config.get("RESEARCH_NOTEBOOK_API_BASE", "") or "").strip()
    if configured:
        if configured.startswith("/"):
            return f"{_host_base_url()}{configured}".rstrip("/")
        return configured.rstrip("/")
    if current_app.testing:
        return ""
    return f"{_host_base_url()}{_proxy_base_path()}"


def _notebook_api_timeout_seconds() -> int:
    raw = current_app.config.get("RESEARCH_NOTEBOOK_API_TIMEOUT_SECONDS", 3)
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        timeout = 3
    return max(1, timeout)


def _notebook_api_token() -> str:
    return str(current_app.config.get("RESEARCH_NOTEBOOK_API_TOKEN", "") or "").strip()


def _notebook_api_auth_token() -> str | None:
    token = _notebook_api_token()
    return token or None


def _resolve_notebook_api_token_for_session(session: dict | None) -> str | None:
    # Only use the configured Jupyter API token; session tokens are for UI links.
    return _notebook_api_auth_token()


def _notebook_api_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: dict | None = None,
) -> requests.Response | None:
    base = _notebook_api_base_url()
    if not base:
        return None
    url = f"{base.rstrip('/')}{path}"
    headers: dict[str, str] = {}
    auth_token = (token or _notebook_api_token()).strip()
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"
        # Some proxies strip Authorization headers; include token in query as fallback.
        if "token=" not in url:
            parts = urlsplit(url)
            query = parse_qs(parts.query, keep_blank_values=True)
            query.setdefault("token", [auth_token])
            url = urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    parts.path,
                    urlencode(query, doseq=True),
                    parts.fragment,
                )
            )

    method_upper = method.strip().upper()
    timeout = _notebook_api_timeout_seconds()

    if auth_token:
        try:
            return requests.request(method, url, headers=headers, timeout=timeout, json=json_body)
        except requests.RequestException:
            return None

    # No token: for non-GET requests Jupyter may enforce XSRF checks.
    if method_upper in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            with requests.Session() as session:
                # Touch the base URL to obtain the _xsrf cookie.
                base_url = f"{base.rstrip('/')}/"
                session.get(base_url, timeout=timeout)
                xsrf = session.cookies.get("_xsrf")
                if xsrf:
                    headers["X-XSRFToken"] = xsrf
                    # Some Jupyter deployments also expect _xsrf in the request args.
                    parts = urlsplit(url)
                    query = parse_qs(parts.query, keep_blank_values=True)
                    query.setdefault("_xsrf", [xsrf])
                    url = urlunsplit(
                        (
                            parts.scheme,
                            parts.netloc,
                            parts.path,
                            urlencode(query, doseq=True),
                            parts.fragment,
                        )
                    )
                parts = urlsplit(base_url)
                origin = f"{parts.scheme}://{parts.netloc}"
                headers.setdefault("Origin", origin)
                headers.setdefault("Referer", base_url)
                return session.request(method, url, headers=headers, timeout=timeout, json=json_body)
        except requests.RequestException:
            return None

    try:
        return requests.request(method, url, headers=headers, timeout=timeout, json=json_body)
    except requests.RequestException:
        return None


def _wait_for_notebook_server_ready(*, timeout_seconds: int = 20) -> bool:
    """Wait until Jupyter API is ready to serve requests."""
    base = _notebook_api_base_url()
    if not base:
        return True

    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        response = _notebook_api_request("GET", "/api")
        if response is not None and response.status_code == 200:
            return True
        time.sleep(0.5)
    return False


def _candidate_notebook_paths(notebook_path: str) -> set[str]:
    base = notebook_path.lstrip("/")
    candidates = {base}
    default_dir = _default_notebook_dir().strip().rstrip("/")
    if default_dir and base.startswith(f"{default_dir}/"):
        candidates.add(base[len(default_dir) + 1 :])
    if base.startswith("research/notebooks/"):
        candidates.add(base[len("research/notebooks/") :])
    return {path for path in candidates if path}


def _notebook_path_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    path = parts.path or ""
    markers = ("/lab/tree/", "/lab/notebooks/", "/notebooks/")
    for marker in markers:
        if marker in path:
            tail = path.split(marker, 1)[1]
            return unquote(tail).lstrip("/")
    return ""


def _find_jupyter_session_for_notebook(*, notebook_path: str, token: str | None = None) -> dict | None:
    sessions = _find_jupyter_sessions_for_notebook(notebook_path=notebook_path, token=token)
    if sessions:
        return sessions[0]
    return None


def _find_jupyter_sessions_for_notebook(*, notebook_path: str, token: str | None = None) -> list[dict]:
    response = _notebook_api_request("GET", "/api/sessions", token=token)
    if response is None or response.status_code != 200:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    candidates = _candidate_notebook_paths(notebook_path)
    candidate_names = {Path(path).name for path in candidates if path}
    matches: list[dict] = []
    for session in payload:
        if not isinstance(session, dict):
            continue
        notebook = session.get("notebook")
        if isinstance(notebook, dict):
            path = str(notebook.get("path") or "").lstrip("/")
        else:
            path = ""
        if path in candidates or (path and Path(path).name in candidate_names):
            matches.append(session)
    return matches


def _kernel_state_to_status(state: str) -> str:
    normalized = state.strip().lower()
    if normalized in {"dead"}:
        return "STOPPED"
    if normalized:
        return "RUNNING"
    return "STOPPED"


def _sync_session_with_jupyter(session: dict) -> bool:
    auth_token = _resolve_notebook_api_token_for_session(session)
    response = _notebook_api_request("GET", "/api/sessions", token=auth_token)
    if response is None or response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    if not isinstance(payload, list):
        return False

    candidates = _candidate_notebook_paths(str(session.get("notebook_path") or ""))
    jupyter_session: dict | None = None
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        notebook = entry.get("notebook")
        if isinstance(notebook, dict):
            path = str(notebook.get("path") or "").lstrip("/")
        else:
            path = ""
        if path in candidates:
            jupyter_session = entry
            break

    changed = False
    if jupyter_session is None:
        if session.get("kernel_id"):
            session["kernel_id"] = ""
            changed = True
        if session.get("kernel_status"):
            session["kernel_status"] = ""
            changed = True
        if session.get("status") != "STOPPED":
            session["status"] = "STOPPED"
            changed = True
        return changed

    kernel = jupyter_session.get("kernel") if isinstance(jupyter_session, dict) else None
    kernel_id = ""
    if isinstance(kernel, dict):
        kernel_id = str(kernel.get("id") or "").strip()
    if kernel_id and session.get("kernel_id") != kernel_id:
        session["kernel_id"] = kernel_id
        changed = True

    kernel_state = ""
    if kernel_id:
        kernel_response = _notebook_api_request("GET", f"/api/kernels/{kernel_id}", token=auth_token)
        if kernel_response is not None and kernel_response.status_code == 200:
            try:
                kernel_payload = kernel_response.json()
            except ValueError:
                kernel_payload = {}
            if isinstance(kernel_payload, dict):
                kernel_state = str(
                    kernel_payload.get("execution_state")
                    or kernel_payload.get("state")
                    or kernel_payload.get("status")
                    or ""
                )

    if kernel_state:
        if session.get("kernel_status") != kernel_state:
            session["kernel_status"] = kernel_state
            changed = True
        mapped = _kernel_state_to_status(kernel_state)
        if session.get("status") != mapped:
            session["status"] = mapped
            changed = True
        if mapped == "RUNNING":
            now = _now()
            ttl = timedelta(seconds=_session_ttl_seconds())
            session["last_active_at"] = _iso(now)
            session["expires_at"] = _iso(now + ttl)
            changed = True
    else:
        if session.get("kernel_status"):
            session["kernel_status"] = ""
            changed = True
        if session.get("status") != "STOPPED":
            session["status"] = "STOPPED"
            changed = True

    return changed


def _ensure_jupyter_session_running(session: dict) -> tuple[bool, str | None]:
    """Ensure a real Jupyter session/kernel exists for the notebook."""
    if not _notebook_api_base_url():
        return True, None

    if not _wait_for_notebook_server_ready():
        return False, "notebook server is still starting"

    auth_token = _resolve_notebook_api_token_for_session(session)
    notebook_path = str(session.get("notebook_path") or "").strip()
    kernel_name = str(session.get("kernel") or _DEFAULT_KERNEL).strip() or _DEFAULT_KERNEL

    existing = _find_jupyter_session_for_notebook(notebook_path=notebook_path, token=auth_token)
    if existing is None:
        response = _notebook_api_request(
            "POST",
            "/api/sessions",
            token=auth_token,
            json_body={
                "path": notebook_path,
                "name": Path(notebook_path).name,
                "type": "notebook",
                "kernel": {"name": kernel_name},
            },
        )
        if response is None:
            return False, "failed to contact notebook server"
        if response.status_code not in {200, 201}:
            return False, f"failed to create jupyter session (status {response.status_code})"

    _sync_session_with_jupyter(session)
    return True, None


def _shutdown_kernel_for_session(session: dict, *, notebook_path_override: str | None = None) -> tuple[bool, str | None]:
    base = _notebook_api_base_url()
    if not base:
        return True, None
    auth_token = _resolve_notebook_api_token_for_session(session)

    kernel_ids: set[str] = set()
    kernel_id = str(session.get("kernel_id") or "").strip()
    if kernel_id:
        kernel_ids.add(kernel_id)

    candidate_paths: list[str] = []
    if notebook_path_override:
        candidate_paths.append(notebook_path_override)
    session_path = str(session.get("notebook_path") or "")
    if session_path:
        candidate_paths.append(session_path)
    url_path = _notebook_path_from_url(str(session.get("notebook_url") or "")) or _notebook_path_from_url(
        str(session.get("embed_url") or "")
    )
    if url_path:
        candidate_paths.append(url_path)

    jupyter_sessions: list[dict] = []
    seen_session_ids: set[str] = set()
    for path in candidate_paths:
        for entry in _find_jupyter_sessions_for_notebook(notebook_path=path, token=auth_token):
            sid = str(entry.get("id") or "").strip()
            if sid and sid not in seen_session_ids:
                seen_session_ids.add(sid)
                jupyter_sessions.append(entry)

    if not jupyter_sessions and not kernel_ids:
        current_app.logger.warning(
            "no matching jupyter session for notebook paths: %s",
            ", ".join(candidate_paths) if candidate_paths else "<empty>",
        )
        return False, "no matching jupyter session for notebook"

    session_ids: list[str] = []
    for entry in jupyter_sessions:
        session_id = str(entry.get("id") or "").strip()
        if session_id:
            session_ids.append(session_id)
        kernel = entry.get("kernel")
        if isinstance(kernel, dict):
            kid = str(kernel.get("id") or "").strip()
            if kid:
                kernel_ids.add(kid)

    # Prefer deleting all matching sessions (handles multiple notebook tabs).
    for session_id in session_ids:
        session_response = _notebook_api_request(
            "DELETE",
            f"/api/sessions/{session_id}",
            token=auth_token,
        )
        if session_response is None:
            return False, "failed to contact notebook server"
        if session_response.status_code not in {200, 202, 204, 404}:
            message = f"failed to shutdown session (status {session_response.status_code})"
            if session_response.status_code in {301, 302, 303, 307, 308}:
                location = session_response.headers.get("Location", "")
                if location:
                    message = f"{message}; redirect to {location}"
            return False, message

    if not kernel_ids:
        return True, None

    # Delete all matching kernels as a safeguard (including orphaned kernels).
    for kid in kernel_ids:
        response = _notebook_api_request("DELETE", f"/api/kernels/{kid}", token=auth_token)
        if response is None:
            return False, "failed to contact notebook server"
        if response.status_code in {200, 202, 204, 404}:
            continue
        message = f"failed to shutdown kernel (status {response.status_code})"
        if response.status_code == 403:
            try:
                body = response.text or ""
            except Exception:
                body = ""
            if body:
                trimmed = body[:500]
                current_app.logger.warning("jupyter kernel shutdown 403: %s", trimmed)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            if location:
                message = f"{message}; redirect to {location}"
        return False, message

    # Verify Jupyter no longer reports sessions for the notebook paths.
    for path in candidate_paths:
        if _find_jupyter_sessions_for_notebook(notebook_path=path, token=auth_token):
            return False, "kernel/session still active; notebook connection is still open"

    return True, None


def _candidate_notebook_delete_paths(notebook_path: str) -> list[str]:
    base = notebook_path.lstrip("/")
    if not base:
        return []
    candidates = [base]
    for extra in _candidate_notebook_paths(base):
        if extra not in candidates:
            candidates.append(extra)
    return candidates


def _delete_notebook_file(*, notebook_path: str, session: dict | None = None) -> tuple[bool, str | None]:
    base = _notebook_api_base_url()
    if not base:
        return True, None

    auth_token = _resolve_notebook_api_token_for_session(session)
    candidates = _candidate_notebook_delete_paths(notebook_path)
    if not candidates:
        return True, None

    for path in candidates:
        safe_path = quote(path.lstrip("/"), safe="/@._-~")
        response = _notebook_api_request("DELETE", f"/api/contents/{safe_path}", token=auth_token)
        if response is None:
            return False, "failed to contact notebook server"
        if response.status_code in {200, 202, 204, 404}:
            if response.status_code != 404:
                return True, None
            continue
        message = f"failed to delete notebook (status {response.status_code})"
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            if location:
                message = f"{message}; redirect to {location}"
        return False, message

    return True, None


def _build_notebook_url(*, notebook_path: str, session_token: str) -> str:
    safe_path = quote(notebook_path.lstrip("/"), safe="/@._-~")
    # 使用相对路径，通过前端代理访问 Jupyter Lab 服务
    return f"/jupyter/lab/tree/{safe_path}?token={session_token}"


def _notebook_root_dir() -> Path:
    configured = str(current_app.config.get("RESEARCH_NOTEBOOK_ROOT_DIR", "") or "").strip()
    root_dir = Path(configured).expanduser() if configured else Path(current_app.root_path).parent
    return root_dir.resolve()


def _resolve_notebook_file_path(notebook_path: str) -> Path:
    root_dir = _notebook_root_dir()
    target = (root_dir / notebook_path).resolve()
    try:
        target.relative_to(root_dir)
    except ValueError as exc:
        raise ValueError("notebook_path escapes notebook root directory") from exc
    return target


def _default_notebook_payload(*, kernel: str) -> dict[str, Any]:
    language = "python" if kernel.lower().startswith("python") else kernel.lower()
    return {
        "cells": [],
        "metadata": {
            "kernelspec": {
                "display_name": kernel,
                "language": language,
                "name": kernel,
            },
            "language_info": {
                "name": language,
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _ensure_notebook_file_writable(*, notebook_path: str, kernel: str) -> Path:
    notebook_file = _resolve_notebook_file_path(notebook_path)
    notebook_dir = notebook_file.parent
    notebook_dir.mkdir(parents=True, exist_ok=True)

    if not os.access(notebook_dir, os.W_OK):
        raise PermissionError("notebook directory is not writable")

    if notebook_file.exists():
        if notebook_file.is_dir():
            raise ValueError("notebook_path points to a directory")
    else:
        notebook_file.write_text(
            json.dumps(_default_notebook_payload(kernel=kernel), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if not os.access(notebook_file, os.W_OK):
        raise PermissionError("notebook file is not writable")
    return notebook_file


def _extract_session_token(session: dict) -> str:
    api_token = _notebook_api_token()
    if api_token:
        return api_token
    token = str(session.get("session_token") or "").strip()
    if token:
        return token
    notebook_url = str(session.get("notebook_url") or "").strip()
    if notebook_url:
        parsed = urlsplit(notebook_url)
        values = parse_qs(parsed.query).get("token") or []
        if values:
            parsed_token = (values[0] or "").strip()
            if parsed_token:
                return parsed_token
    return secrets.token_urlsafe(24)


def _hydrate_session_urls(session: dict) -> bool:
    notebook_path = _select_notebook_path(
        str(session.get("research_id") or "").strip(),
        session.get("notebook_path"),
    )
    session["notebook_path"] = notebook_path
    session_token = _extract_session_token(session)
    expected_url = _build_notebook_url(notebook_path=notebook_path, session_token=session_token)
    changed = False

    if session.get("session_token") != session_token:
        session["session_token"] = session_token
        changed = True
    if session.get("notebook_url") != expected_url:
        session["notebook_url"] = expected_url
        changed = True
    if session.get("embed_url") != expected_url:
        session["embed_url"] = expected_url
        changed = True

    return changed


def _session_response(session: dict) -> dict:
    return {
        "session_id": session["session_id"],
        "notebook_url": session["notebook_url"],
        "embed_url": session["embed_url"],
        "status": session["status"],
        "kernel_id": session.get("kernel_id") or "",
        "kernel_status": session.get("kernel_status") or "",
        "started_at": session["started_at"],
        "last_active_at": session["last_active_at"],
        "expires_at": session["expires_at"],
    }


def _is_expired(session: dict) -> bool:
    expires_at = _parse_iso(session.get("expires_at"))
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return _now() >= expires_at.astimezone(timezone.utc)


@bp_research.after_request
def _allow_iframe_headers(response):
    # Research JSON responses should not block iframe embedding in same-origin pages.
    response.headers.pop("X-Frame-Options", None)
    if not response.headers.get("Content-Security-Policy"):
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response


@bp_research.get("/items")
@_research_auth_required
def api_list_research_items():
    with _store_lock:
        items = _load_items()
        sessions = _load_sessions()
        sessions_changed = False
        for item in items.values():
            session = sessions.get(item["id"])
            if session is not None:
                changed = _sync_session_with_jupyter(session)
                if _is_expired(session):
                    session["status"] = "EXPIRED"
                    changed = True
                status = str(session.get("status") or "RUNNING")
                item["session_status"] = status
                if changed:
                    sessions[item["id"]] = session
                    sessions_changed = True
            else:
                item["session_status"] = None
        if sessions_changed:
            _save_sessions(sessions)
    ordered = sorted(items.values(), key=lambda item: (item.get("updated_at") or "", item["id"]), reverse=True)
    return jsonify({"items": ordered}), 200


@bp_research.post("/items")
@_research_auth_required
def api_create_research_item():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    try:
        payload = _validate_item_payload(data, require_id=True, require_notebook_path=False)
        research_id = payload["id"]
        notebook_path = _select_notebook_path(research_id, payload.get("notebook_path"))
    except ValueError as exc:
        return _error(str(exc), 400)

    with _store_lock:
        items = _load_items()
        if research_id in items:
            return _error("research id already exists", 409)
        now_iso = _iso(_now())
        item = {
            **payload,
            "notebook_path": notebook_path,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        items[research_id] = item
        _save_items(items)
    return jsonify({"research": item}), 201


@bp_research.get("/items/<research_id>")
@_research_auth_required
def api_get_research_item(research_id: str):
    try:
        normalized_id = _validate_id(research_id)
    except ValueError as exc:
        return _error(str(exc), 400)

    with _store_lock:
        items = _load_items()
        item = items.get(normalized_id)
    if item is None:
        return _error("research not found", 404)
    return jsonify({"research": item}), 200


@bp_research.put("/items/<research_id>")
@_research_auth_required
def api_update_research_item(research_id: str):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    try:
        normalized_id = _validate_id(research_id)
        payload = _validate_item_payload(data, require_id=False, require_notebook_path=False)
    except ValueError as exc:
        return _error(str(exc), 400)

    body_id = payload.get("id")
    if body_id is not None and body_id != normalized_id:
        return _error("id in body must match path id", 400)

    with _store_lock:
        items = _load_items()
        existing = items.get(normalized_id)
        if existing is None:
            return _error("research not found", 404)
        try:
            notebook_path = _select_notebook_path(
                normalized_id,
                payload.get("notebook_path"),
                existing.get("notebook_path"),
            )
        except ValueError as exc:
            return _error(str(exc), 400)
        updated = {
            "id": normalized_id,
            "title": payload["title"],
            "description": payload["description"],
            "notebook_path": notebook_path,
            "kernel": payload["kernel"],
            "status": payload["status"],
            "tags": payload["tags"],
            "created_at": existing.get("created_at") or _iso(_now()),
            "updated_at": _iso(_now()),
        }
        items[normalized_id] = updated
        _save_items(items)
    return jsonify({"research": updated}), 200


@bp_research.delete("/items/<research_id>")
@_research_auth_required
def api_delete_research_item(research_id: str):
    try:
        normalized_id = _validate_id(research_id)
    except ValueError as exc:
        return _error(str(exc), 400)

    with _store_lock:
        items = _load_items()
        item = items.get(normalized_id)
        if item is None:
            return _error("research not found", 404)
        sessions = _load_sessions()
        session = sessions.get(normalized_id)
        notebook_path_override = None
        try:
            notebook_path_override = _select_notebook_path(
                normalized_id,
                item.get("notebook_path"),
                item.get("notebook_path"),
            )
        except ValueError:
            notebook_path_override = None

        if session:
            ok, message = _shutdown_kernel_for_session(session, notebook_path_override=notebook_path_override)
            if not ok and message != "no matching jupyter session for notebook":
                return _error(message or "failed to shutdown kernel", 502)
        elif notebook_path_override:
            fallback_session = {
                "kernel_id": "",
                "notebook_path": notebook_path_override,
                "notebook_url": "",
                "embed_url": "",
                "session_token": "",
            }
            ok, message = _shutdown_kernel_for_session(
                fallback_session,
                notebook_path_override=notebook_path_override,
            )
            if not ok and message != "no matching jupyter session for notebook":
                return _error(message or "failed to shutdown kernel", 502)

        if notebook_path_override:
            ok, message = _delete_notebook_file(notebook_path=notebook_path_override, session=session)
            if not ok:
                return _error(message or "failed to delete notebook", 502)

        del items[normalized_id]
        _save_items(items)

        if session:
            del sessions[normalized_id]
            _save_sessions(sessions)

    return jsonify({"ok": True, "id": normalized_id}), 200


@bp_research.get("/items/<research_id>/notebook/session")
@_research_auth_required
def api_get_notebook_session(research_id: str):
    try:
        normalized_id = _validate_id(research_id)
    except ValueError as exc:
        return _error(str(exc), 400)

    with _store_lock:
        sessions = _load_sessions()
        session = sessions.get(normalized_id)
        if session:
            try:
                changed = _hydrate_session_urls(session)
            except ValueError as exc:
                return _error(str(exc), 409)
            changed = _sync_session_with_jupyter(session) or changed
            if _is_expired(session):
                session["status"] = "EXPIRED"
                changed = True
            if changed:
                sessions[normalized_id] = session
                _save_sessions(sessions)
            if session.get("status") == "EXPIRED":
                session = None

    if session is None:
        return _error("session not found", 404)
    return jsonify({"session": _session_response(session)}), 200


@bp_research.post("/items/<research_id>/notebook/session")
@_research_auth_required
def api_create_notebook_session(research_id: str):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error("request body must be a JSON object", 400)

    try:
        normalized_id = _validate_id(research_id)
    except ValueError as exc:
        return _error(str(exc), 400)

    lock = _session_lock_for(normalized_id)
    with lock:
        with _store_lock:
            items = _load_items()
            item = items.get(normalized_id)
            if item is None:
                return _error("research not found", 404)

            try:
                notebook_path = _select_notebook_path(
                    normalized_id,
                    data.get("notebook_path"),
                    item.get("notebook_path"),
                )
            except ValueError as exc:
                return _error(str(exc), 400)

            requested_kernel = data.get("kernel", item.get("kernel") or _DEFAULT_KERNEL)
            if not isinstance(requested_kernel, str) or not requested_kernel.strip():
                return _error("kernel must be a non-empty string", 400)
            kernel = requested_kernel.strip()

            if item.get("notebook_path") != notebook_path:
                item = {**item, "notebook_path": notebook_path, "updated_at": _iso(_now())}
                items[normalized_id] = item
                _save_items(items)

            try:
                _ensure_notebook_file_writable(notebook_path=notebook_path, kernel=kernel)
            except ValueError as exc:
                return _error(str(exc), 400)
            except PermissionError as exc:
                return _error(str(exc), 409)
            except OSError:
                return _error("failed to prepare notebook file", 500)

            sessions = _load_sessions()
            existing = sessions.get(normalized_id)
            if existing and not _is_expired(existing):
                if (
                    existing.get("status") == "RUNNING"
                    and existing.get("notebook_path") == notebook_path
                    and existing.get("kernel") == kernel
                ):
                    now = _now()
                    ttl = timedelta(seconds=_session_ttl_seconds())
                    existing["last_active_at"] = _iso(now)
                    existing["expires_at"] = _iso(now + ttl)
                    existing["status"] = "RUNNING"
                    try:
                        _hydrate_session_urls(existing)
                    except ValueError as exc:
                        return _error(str(exc), 409)
                    ok, message = _ensure_jupyter_session_running(existing)
                    if not ok:
                        return _error(message or "failed to start notebook session", 503)
                    sessions[normalized_id] = existing
                    _save_sessions(sessions)
                    return jsonify({"session": _session_response(existing)}), 200

            now = _now()
            ttl = timedelta(seconds=_session_ttl_seconds())
            session = {
                "session_id": f"sess_{secrets.token_hex(8)}",
                "research_id": normalized_id,
                "notebook_path": notebook_path,
                "kernel": kernel,
                "kernel_id": "",
                "kernel_status": "",
                "session_token": secrets.token_urlsafe(24),
                "notebook_url": "",
                "embed_url": "",
                "status": "RUNNING",
                "started_at": _iso(now),
                "last_active_at": _iso(now),
                "expires_at": _iso(now + ttl),
            }
            _hydrate_session_urls(session)
            ok, message = _ensure_jupyter_session_running(session)
            if not ok:
                return _error(message or "failed to start notebook session", 503)
            sessions[normalized_id] = session
            _save_sessions(sessions)

        return jsonify({"session": _session_response(session)}), 200


@bp_research.post("/items/<research_id>/notebook/session/refresh")
@_research_auth_required
def api_refresh_notebook_session(research_id: str):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error("request body must be a JSON object", 400)

    try:
        normalized_id = _validate_id(research_id)
    except ValueError as exc:
        return _error(str(exc), 400)

    lock = _session_lock_for(normalized_id)
    with lock:
        with _store_lock:
            sessions = _load_sessions()
            session = sessions.get(normalized_id)
            if session is None:
                return _error("session not found", 404)
            session_id = data.get("session_id")
            if not isinstance(session_id, str) or not session_id.strip():
                session_id = str(session.get("session_id") or "").strip()
            if not session_id:
                return _error("session not found", 404)
            if session.get("session_id") != session_id.strip():
                return _error("session not found", 404)
            if _is_expired(session):
                session["status"] = "EXPIRED"
                sessions[normalized_id] = session
                _save_sessions(sessions)
                return _error("session expired", 409)

            now = _now()
            ttl = timedelta(seconds=_session_ttl_seconds())
            session["status"] = "RUNNING"
            session["last_active_at"] = _iso(now)
            session["expires_at"] = _iso(now + ttl)
            try:
                _hydrate_session_urls(session)
            except ValueError as exc:
                return _error(str(exc), 409)
            _sync_session_with_jupyter(session)
            sessions[normalized_id] = session
            _save_sessions(sessions)

    return jsonify({"session": _session_response(session)}), 200


@bp_research.delete("/items/<research_id>/notebook/session")
@_research_auth_required
def api_delete_notebook_session(research_id: str):
    session_id = request.args.get("session_id", "")
    if not session_id:
        data = request.get_json(silent=True) or {}
        if isinstance(data, dict):
            session_id = str(data.get("session_id") or "")

    try:
        normalized_id = _validate_id(research_id)
    except ValueError as exc:
        return _error(str(exc), 400)

    lock = _session_lock_for(normalized_id)
    with lock:
        with _store_lock:
            sessions = _load_sessions()
            session = sessions.get(normalized_id)
            items = _load_items()
            item = items.get(normalized_id)
            notebook_path_override = None
            if item is not None:
                try:
                    notebook_path_override = _select_notebook_path(
                        normalized_id,
                        item.get("notebook_path"),
                        item.get("notebook_path"),
                    )
                except ValueError:
                    notebook_path_override = None
            if session is None:
                if item is None:
                    return _error("session not found", 404)
                if not notebook_path_override:
                    notebook_path_override = _default_notebook_path(normalized_id)
                fallback_session = {
                    "kernel_id": "",
                    "notebook_path": notebook_path_override or "",
                    "notebook_url": "",
                    "embed_url": "",
                    "session_token": "",
                }
                ok, message = _shutdown_kernel_for_session(
                    fallback_session,
                    notebook_path_override=notebook_path_override,
                )
                if not ok:
                    return _error(message or "failed to shutdown kernel", 502)
                return jsonify({"ok": True, "session_id": session_id.strip()}), 200
            if session_id:
                if session.get("session_id") != session_id.strip():
                    return _error("session not found", 404)
            ok, message = _shutdown_kernel_for_session(session, notebook_path_override=notebook_path_override)
            if not ok:
                return _error(message or "failed to shutdown kernel", 502)
            now = _now()
            ttl = timedelta(seconds=_session_ttl_seconds())
            session.update(
                {
                    "status": "STOPPED",
                    "kernel_id": "",
                    "kernel_status": "",
                    "session_id": "",
                    "session_token": "",
                    "notebook_url": "",
                    "embed_url": "",
                    "last_active_at": _iso(now),
                    "expires_at": _iso(now + ttl),
                }
            )
            sessions[normalized_id] = session
            _save_sessions(sessions)

    return jsonify({"ok": True, "session_id": session_id.strip()}), 200
