from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from urllib.parse import unquote
from subprocess import TimeoutExpired
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

from app.auth import auth_required
from app.backtest.services.runner import (
    RQALPHA_CANCELLED_EXIT_CODE,
    StrategyReferencedError,
    StrategyRenameConflictError,
    StrategyRenameCycleError,
    build_config_yaml,
    build_run_fingerprint,
    bind_run_fingerprint,
    clear_cancel_request,
    compile_strategy_debug,
    delete_job,
    delete_strategy_cascade,
    get_strategy_rename_map,
    cleanup_old_runs,
    create_job_dir,
    delete_strategy,
    find_reusable_job_id,
    is_cancel_requested,
    list_strategies,
    load_strategy_detail,
    load_strategy_metadata,
    load_strategy,
    locate_job_dir,
    list_strategy_jobs,
    read_status,
    rename_strategy,
    normalize_strategy_id,
    resolve_current_strategy_id,
    run_rqalpha,
    save_strategy,
    upsert_strategy_rename_mapping,
    update_job_index,
    write_job_meta,
    write_status,
)
from app.backtest.services.extractor import extract_result

bp_backtest = Blueprint("bp_backtest", __name__, url_prefix="/api/backtest")

_DEFAULT_ERROR_CODES = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    405: "METHOD_NOT_ALLOWED",
    422: "UNPROCESSABLE_ENTITY",
    404: "NOT_FOUND",
    409: "CONFLICT",
    500: "INTERNAL_ERROR",
}
_DEFAULT_ERROR_MESSAGES = {
    400: "bad request",
    401: "unauthorized",
    403: "forbidden",
    405: "method not allowed",
    422: "unprocessable entity",
    404: "not found",
    409: "conflict",
    500: "internal server error",
}
_DATE_FMT = "%Y-%m-%d"


def _error_response(http_status: int, code: str, message: str, **extra):
    payload = {"ok": False, "error": {"code": code, "message": message}}
    payload.update(extra)
    return jsonify(payload), http_status


def _ok_response(data: dict | list | None = None, *, message: str = "ok", code: int = 200, http_status: int = 200):
    payload: dict[str, object] = {"ok": True, "code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), http_status


def _json_http_error_handler(error):
    status = int(getattr(error, "code", 500) or 500)
    code = _DEFAULT_ERROR_CODES.get(status, "INTERNAL_ERROR")
    message = _DEFAULT_ERROR_MESSAGES.get(status, "internal server error")

    if isinstance(error, HTTPException) and status == 400:
        description = error.description
        if isinstance(description, str) and description.strip():
            message = description

    if status == 500:
        original = getattr(error, "original_exception", None)
        if original is not None:
            current_app.logger.exception("internal server error: %s", original)

    return _error_response(status, code, message)


def _json_unexpected_error_handler(error):
    if isinstance(error, HTTPException):
        return error
    current_app.logger.exception("internal server error: %s", error)
    return _error_response(500, "INTERNAL_ERROR", "internal server error")


def _register_json_error_handlers(app):
    if app.extensions.get("bp_backtest_json_errors_registered"):
        return
    app.extensions["bp_backtest_json_errors_registered"] = True

    for status in (400, 401, 403, 404, 405, 409, 422, 500):
        app.register_error_handler(status, _json_http_error_handler)
    app.register_error_handler(Exception, _json_unexpected_error_handler)


def _parse_bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@bp_backtest.record_once
def _on_blueprint_registered(state):
    _register_json_error_handlers(state.app)


def _parse_int_arg(name: str, default: int, *, min_value: int, max_value: int | None = None) -> int:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")

    if value < min_value:
        if min_value == 0:
            raise ValueError(f"{name} must be >= 0")
        raise ValueError(f"{name} must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be <= {max_value}")
    return value


def _parse_date_arg(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format")
    normalized = value.strip()
    try:
        parsed = datetime.strptime(normalized, _DATE_FMT).date()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format") from exc
    return parsed.isoformat()


def _parse_cash(value: object) -> int | float:
    if isinstance(value, bool):
        raise ValueError("cash must be a number")
    try:
        cash_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("cash must be a number") from exc
    if cash_value <= 0:
        raise ValueError("cash must be > 0")
    if cash_value.is_integer():
        return int(cash_value)
    return cash_value


def _allowed_frequencies() -> set[str]:
    configured = current_app.config.get("BACKTEST_ALLOWED_FREQUENCIES", ("1d",))
    if isinstance(configured, str):
        values = [configured]
    elif isinstance(configured, (list, tuple, set, frozenset)):
        values = [str(item) for item in configured]
    else:
        values = ["1d"]
    normalized = {item.strip() for item in values if item and item.strip()}
    return normalized or {"1d"}


def _decode_path_component(value: str) -> str:
    return unquote(value)


def _normalize_strategy_field(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    try:
        return normalize_strategy_id(value)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("strategy_id"):
            message = f"{field_name}{message[len('strategy_id'):]}"
        raise ValueError(message) from exc


def _validate_run_request(data: dict) -> dict:
    required_fields = ("strategy_id", "start_date", "end_date")
    missing = [field for field in required_fields if field not in data or data.get(field) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")

    strategy_id_raw = data.get("strategy_id")
    strategy_id = _normalize_strategy_field(strategy_id_raw, field_name="strategy_id")

    start_date = _parse_date_arg("start_date", data.get("start_date"))
    end_date = _parse_date_arg("end_date", data.get("end_date"))
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")

    cash = _parse_cash(data.get("cash", 100000))

    benchmark_raw = data.get("benchmark", "000300.XSHG")
    if not isinstance(benchmark_raw, str) or not benchmark_raw.strip():
        raise ValueError("benchmark must be a non-empty string")
    benchmark = benchmark_raw.strip()

    frequency_raw = data.get("frequency", "1d")
    if not isinstance(frequency_raw, str) or not frequency_raw.strip():
        raise ValueError("frequency must be a non-empty string")
    frequency = frequency_raw.strip()
    allowed_frequencies = _allowed_frequencies()
    if frequency not in allowed_frequencies:
        allowed = ", ".join(sorted(allowed_frequencies))
        raise ValueError(f"frequency must be one of: {allowed}")

    return {
        "strategy_id": strategy_id,
        "start_date": start_date,
        "end_date": end_date,
        "cash": cash,
        "benchmark": benchmark,
        "frequency": frequency,
    }


def _compile_timeout_seconds() -> int:
    raw_timeout = current_app.config.get("BACKTEST_COMPILE_TIMEOUT", 10)
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        timeout = 10
    return max(1, timeout)


def _compile_result_payload(
    *,
    ok: bool,
    stdout: str = "",
    stderr: str = "",
    diagnostics: list[dict] | None = None,
) -> dict:
    payload_diagnostics = diagnostics if isinstance(diagnostics, list) else []
    normalized: list[dict] = []
    for item in payload_diagnostics:
        if not isinstance(item, dict):
            continue
        try:
            line = max(0, int(item.get("line", 0)))
        except (TypeError, ValueError):
            line = 0
        try:
            column = max(0, int(item.get("column", 0)))
        except (TypeError, ValueError):
            column = 0
        normalized.append(
            {
                "line": line,
                "column": column,
                "level": str(item.get("level") or "error"),
                "message": str(item.get("message") or ""),
            }
        )
    normalized.sort(key=lambda item: (item["line"], item["column"], item["message"]))
    return {
        "ok": bool(ok),
        "stdout": str(stdout or ""),
        "stderr": str(stderr or ""),
        "diagnostics": normalized,
    }


def _compile_error_payload(message: str, *, line: int = 0, column: int = 0) -> dict:
    return _compile_result_payload(
        ok=False,
        stderr=message,
        diagnostics=[
            {
                "line": line,
                "column": column,
                "level": "error",
                "message": message,
            }
        ],
    )


def _compile_http_status(result: dict, result_kind: str) -> int:
    if bool(result.get("ok")):
        return 200
    if result_kind == "compile_error":
        return 422
    return 500


def _audit_compile_event(
    *,
    strategy_id: str,
    use_temporary_code: bool,
    status: int,
    result: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    diagnostics_count = 0
    ok = None
    if isinstance(result, dict):
        diagnostics = result.get("diagnostics")
        diagnostics_count = len(diagnostics) if isinstance(diagnostics, list) else 0
        ok = bool(result.get("ok"))
    current_app.logger.info(
        "AUDIT strategy_compile user_id=%s is_admin=%s strategy_id=%s temporary_code=%s status=%s ok=%s diagnostics=%s duration_ms=%s error_code=%s error_message=%s",
        getattr(g, "user_id", None),
        getattr(g, "is_admin", None),
        strategy_id,
        use_temporary_code,
        status,
        ok,
        diagnostics_count,
        duration_ms,
        error_code,
        error_message,
    )


@bp_backtest.get("/strategies")
@auth_required
def api_list_strategies():
    try:
        limit = _parse_int_arg("limit", 100, min_value=1, max_value=500)
        offset = _parse_int_arg("offset", 0, min_value=0)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))

    q = request.args.get("q")
    try:
        strategies, total = list_strategies(q=q, limit=limit, offset=offset)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))

    return _ok_response({"strategies": strategies, "total": total})


@bp_backtest.post("/strategies/<strategy_id>")
@auth_required
def api_save_strategy(strategy_id: str):
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    if not isinstance(code, str) or not code.strip():
        return _error_response(400, "INVALID_ARGUMENT", "code is empty")
    try:
        canonical_strategy_id = resolve_current_strategy_id(_decode_path_component(strategy_id))
        save_strategy(canonical_strategy_id, code)
        metadata = load_strategy_metadata(canonical_strategy_id)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))
    return _ok_response(metadata)

@bp_backtest.get("/strategies/<strategy_id>")
@auth_required
def api_get_strategy(strategy_id: str):
    try:
        canonical_strategy_id = resolve_current_strategy_id(_decode_path_component(strategy_id))
        payload = load_strategy_detail(canonical_strategy_id)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))
    except FileNotFoundError:
        return _error_response(404, "NOT_FOUND", "strategy not found")
    return _ok_response(payload)


@bp_backtest.delete("/strategies/<strategy_id>")
@auth_required
def api_delete_strategy(strategy_id: str):
    try:
        cascade = _parse_bool_arg("cascade", default=False)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))

    updated_by = str(getattr(g, "user_id", "")) or None
    try:
        canonical_strategy_id = resolve_current_strategy_id(_decode_path_component(strategy_id))
        if cascade:
            canonical_strategy_id, deleted_jobs = delete_strategy_cascade(
                canonical_strategy_id,
                updated_by=updated_by,
            )
            return _ok_response(
                {
                    "strategy_id": canonical_strategy_id,
                    "deleted_jobs": deleted_jobs,
                },
                message="deleted",
            )
        delete_strategy(canonical_strategy_id)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))
    except FileNotFoundError:
        return _error_response(404, "NOT_FOUND", "strategy not found")
    except StrategyReferencedError as exc:
        return _error_response(
            409,
            "CONFLICT",
            "strategy is referenced by existing jobs",
            data={"strategy_id": exc.strategy_id, "job_ids": exc.job_ids},
        )

    return _ok_response({"strategy_id": canonical_strategy_id, "deleted": True}, message="deleted")


@bp_backtest.post("/strategies/<from_id>/rename")
@auth_required
def api_rename_strategy(from_id: str):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error_response(400, "INVALID_ARGUMENT", "request body must be a JSON object")

    if "to_id" not in data:
        return _error_response(400, "INVALID_ARGUMENT", "to_id is required")
    to_id_raw = data.get("to_id")
    if not isinstance(to_id_raw, str):
        return _error_response(400, "INVALID_ARGUMENT", "to_id must be a string")

    code_override = None
    if "code" in data:
        if not isinstance(data.get("code"), str):
            return _error_response(400, "INVALID_ARGUMENT", "code must be a string")
        code_override = data.get("code")

    updated_by = str(getattr(g, "user_id", "")) or None
    try:
        normalized_from_id = _normalize_strategy_field(_decode_path_component(from_id), field_name="from_id")
        normalized_to_id = _normalize_strategy_field(to_id_raw, field_name="to_id")
        result = rename_strategy(normalized_from_id, normalized_to_id, code=code_override, updated_by=updated_by)
    except ValueError as exc:
        return _error_response(422, "UNPROCESSABLE_ENTITY", str(exc))
    except FileNotFoundError:
        return _error_response(404, "NOT_FOUND", "strategy not found")
    except StrategyRenameConflictError as exc:
        return _error_response(409, "CONFLICT", str(exc))
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))

    return _ok_response(
        {
            "from_id": result["from_id"],
            "to_id": result["to_id"],
            "deleted_old": bool(result["deleted_old"]),
            "warning": result.get("warning", ""),
        }
    )


@bp_backtest.get("/strategy-renames")
@auth_required
def api_get_strategy_renames():
    try:
        rename_map = get_strategy_rename_map()
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))
    return _ok_response({"map": rename_map})


@bp_backtest.post("/strategy-renames")
@auth_required
def api_upsert_strategy_renames():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error_response(400, "INVALID_ARGUMENT", "request body must be a JSON object")

    from_id_raw = data.get("from_id")
    to_id_raw = data.get("to_id")
    if from_id_raw is None:
        return _error_response(400, "INVALID_ARGUMENT", "from_id is required")
    if to_id_raw is None:
        return _error_response(400, "INVALID_ARGUMENT", "to_id is required")
    if not isinstance(from_id_raw, str):
        return _error_response(400, "INVALID_ARGUMENT", "from_id must be a string")
    if not isinstance(to_id_raw, str):
        return _error_response(400, "INVALID_ARGUMENT", "to_id must be a string")

    updated_by = str(getattr(g, "user_id", "")) or None
    try:
        normalized_from_id = _normalize_strategy_field(from_id_raw, field_name="from_id")
        normalized_to_id = _normalize_strategy_field(to_id_raw, field_name="to_id")
        rename_map = upsert_strategy_rename_mapping(normalized_from_id, normalized_to_id, updated_by=updated_by)
    except ValueError as exc:
        return _error_response(422, "UNPROCESSABLE_ENTITY", str(exc))
    except StrategyRenameConflictError as exc:
        return _error_response(409, "CONFLICT", str(exc))
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))

    return _ok_response({"map": rename_map})


@bp_backtest.get("/strategies/<strategy_id>/jobs")
@auth_required
def api_list_strategy_jobs(strategy_id: str):
    try:
        limit = _parse_int_arg("limit", 100, min_value=1, max_value=1000)
        offset = _parse_int_arg("offset", 0, min_value=0)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))

    status_raw = request.args.get("status")
    status = status_raw.strip() if isinstance(status_raw, str) and status_raw.strip() else None

    try:
        canonical_strategy_id = resolve_current_strategy_id(_decode_path_component(strategy_id))
        jobs, total = list_strategy_jobs(canonical_strategy_id, limit=limit, offset=offset, status=status)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))

    data = {"strategy_id": canonical_strategy_id, "jobs": jobs, "total": total}
    payload = {"ok": True, "code": 200, "message": "ok", "data": data}
    # Backward compatibility for legacy history page: it reads payload.jobs/total.
    payload.update(data)
    return jsonify(payload), 200


@bp_backtest.post("/strategies/<strategy_id>/compile")
@auth_required
def api_compile_strategy(strategy_id: str):
    start_time = time.monotonic()
    decoded_strategy_id = _decode_path_component(strategy_id)
    try:
        strategy_id = normalize_strategy_id(decoded_strategy_id)
        strategy_id = resolve_current_strategy_id(strategy_id)
    except ValueError as exc:
        result = _compile_error_payload(str(exc))
        _audit_compile_event(
            strategy_id=decoded_strategy_id,
            use_temporary_code=False,
            status=400,
            result=result,
            error_code="INVALID_ARGUMENT",
            error_message=str(exc),
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return jsonify(result), 400
    except StrategyRenameCycleError as exc:
        result = _compile_error_payload(str(exc))
        _audit_compile_event(
            strategy_id=strategy_id,
            use_temporary_code=False,
            status=409,
            result=result,
            error_code="CONFLICT",
            error_message=str(exc),
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return jsonify(result), 409

    if not bool(getattr(g, "is_admin", False)):
        result = _compile_error_payload("admin role required")
        _audit_compile_event(
            strategy_id=strategy_id,
            use_temporary_code=False,
            status=403,
            result=result,
            error_code="FORBIDDEN",
            error_message="admin role required",
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return jsonify(result), 403

    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        message = "request body must be a JSON object"
        result = _compile_error_payload(message)
        _audit_compile_event(
            strategy_id=strategy_id,
            use_temporary_code=False,
            status=400,
            result=result,
            error_code="INVALID_ARGUMENT",
            error_message=message,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return jsonify(result), 400

    code_in_body = data.get("code")
    if code_in_body is not None and not isinstance(code_in_body, str):
        message = "code must be a string"
        result = _compile_error_payload(message)
        _audit_compile_event(
            strategy_id=strategy_id,
            use_temporary_code=False,
            status=400,
            result=result,
            error_code="INVALID_ARGUMENT",
            error_message=message,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return jsonify(result), 400

    use_temporary_code = isinstance(code_in_body, str) and bool(code_in_body.strip())
    try:
        if use_temporary_code:
            code = code_in_body
        else:
            code = load_strategy(strategy_id)
    except ValueError as exc:
        result = _compile_error_payload(str(exc))
        _audit_compile_event(
            strategy_id=strategy_id,
            use_temporary_code=use_temporary_code,
            status=400,
            result=result,
            error_code="INVALID_ARGUMENT",
            error_message=str(exc),
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return jsonify(result), 400
    except FileNotFoundError:
        message = "strategy code not found"
        result = _compile_error_payload(message)
        _audit_compile_event(
            strategy_id=strategy_id,
            use_temporary_code=use_temporary_code,
            status=400,
            result=result,
            error_code="INVALID_ARGUMENT",
            error_message=message,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return jsonify(result), 400

    timeout_seconds = _compile_timeout_seconds()
    result, result_kind = compile_strategy_debug(code, timeout_seconds=timeout_seconds)
    result = _compile_result_payload(
        ok=bool(result.get("ok")),
        stdout=str(result.get("stdout") or ""),
        stderr=str(result.get("stderr") or ""),
        diagnostics=result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else [],
    )
    http_status = _compile_http_status(result, result_kind)
    _audit_compile_event(
        strategy_id=strategy_id,
        use_temporary_code=use_temporary_code,
        status=http_status,
        result=result,
        duration_ms=int((time.monotonic() - start_time) * 1000),
    )
    return jsonify(result), http_status


def _run_job(app, job_id: str, job_dir: Path) -> None:
    with app.app_context():
        try:
            if is_cancel_requested(job_id):
                write_status(job_dir, "CANCELLED", "JOB_CANCELLED", "job cancelled by user")
                return

            write_status(job_dir, "RUNNING")
            return_code = run_rqalpha(job_id, job_dir)

            if return_code == RQALPHA_CANCELLED_EXIT_CODE or is_cancel_requested(job_id):
                write_status(job_dir, "CANCELLED", "JOB_CANCELLED", "job cancelled by user")
                return

            if return_code != 0:
                write_status(
                    job_dir,
                    "FAILED",
                    "RQALPHA_EXIT_NONZERO",
                    f"rqalpha exit code={return_code}; see run.log",
                )
                return

            result_pkl = job_dir / "result.pkl"
            if not result_pkl.exists():
                write_status(
                    job_dir,
                    "FAILED",
                    "RESULT_FILE_MISSING",
                    "result.pkl not found; check sys_analyser.output_file",
                )
                return

            try:
                extract_result(result_pkl, job_dir / "extracted.json")
            except Exception as exc:
                current_app.logger.warning("extract_result failed: %s", exc)
                # 即使提取结果失败，回测本身已经完成，仍将状态设置为 FINISHED
            write_status(job_dir, "FINISHED")
        except TimeoutExpired:
            timeout = int(current_app.config.get("BACKTEST_TIMEOUT", 900))
            write_status(
                job_dir,
                "FAILED",
                "RQALPHA_TIMEOUT",
                f"rqalpha timeout after {timeout}s; see run.log",
            )
        except Exception as exc:
            write_status(
                job_dir,
                "FAILED",
                "INTERNAL_ERROR",
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            clear_cancel_request(job_id)

@bp_backtest.post("/run")
@auth_required
def api_run_backtest():
    data = request.get_json(silent=True) or {}
    try:
        normalized = _validate_run_request(data)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))

    strategy_id = normalized["strategy_id"]
    start_date = normalized["start_date"]
    end_date = normalized["end_date"]
    cash = normalized["cash"]
    benchmark = normalized["benchmark"]
    frequency = normalized["frequency"]

    try:
        strategy_id = resolve_current_strategy_id(strategy_id)
        code = load_strategy(strategy_id)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))
    except StrategyRenameCycleError as exc:
        return _error_response(409, "CONFLICT", str(exc))
    except FileNotFoundError:
        return _error_response(404, "NOT_FOUND", "strategy not found")
    code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()

    run_fingerprint = build_run_fingerprint(
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
        cash=cash,
        benchmark=benchmark,
        frequency=frequency,
        code=code,
    )
    idempotency_window_seconds = int(current_app.config.get("BACKTEST_IDEMPOTENCY_WINDOW_SECONDS", 30))
    reusable_job_id = find_reusable_job_id(run_fingerprint, idempotency_window_seconds)
    if reusable_job_id:
        return jsonify({"job_id": reusable_job_id})

    try:
        cleanup_old_runs()
    except Exception as exc:
        current_app.logger.warning("cleanup_old_runs failed: %s", exc)

    job_id, job_dir = create_job_dir()

    (job_dir / "strategy.py").write_text(code, encoding="utf-8")

    cfg = build_config_yaml(
        start_date=start_date,
        end_date=end_date,
        cash=cash,
        benchmark=benchmark,
        frequency=frequency,
        output_file=str((job_dir / "result.pkl").resolve()),
    )
    (job_dir / "config.yml").write_text(cfg, encoding="utf-8")
    status_payload = write_status(job_dir, "QUEUED")
    write_job_meta(
        job_dir,
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
        cash=cash,
        benchmark=benchmark,
        frequency=frequency,
        code_sha256=code_sha256,
    )
    update_job_index(
        job_id,
        strategy_id=strategy_id,
        status="QUEUED",
        created_at=status_payload.get("updated_at"),
        updated_at=status_payload.get("updated_at"),
        params={
            "start_date": start_date,
            "end_date": end_date,
            "cash": cash,
            "benchmark": benchmark,
            "frequency": frequency,
        },
        error=None,
    )
    bind_run_fingerprint(run_fingerprint, job_id)

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_job, args=(app, job_id, job_dir), daemon=False)
    thread.start()

    return jsonify({"job_id": job_id})

@bp_backtest.get("/jobs/<job_id>")
@auth_required
def api_job_status(job_id: str):
    job_dir = locate_job_dir(job_id)
    if job_dir is None:
        return _error_response(404, "NOT_FOUND", "not found")
    try:
        status_payload = read_status(job_dir)
    except (FileNotFoundError, OSError, ValueError):
        return _error_response(404, "STATUS_NOT_FOUND", "status not found")

    error = status_payload.get("error")
    return jsonify(
        {
            "job_id": job_id,
            "status": status_payload.get("status"),
            "error": error,
            "error_message": error.get("message") if error else None,
        }
    )


@bp_backtest.get("/jobs/<job_id>/progress")
@auth_required
def api_job_progress(job_id: str):
    """Get detailed progress information for a running backtest job.

    Returns RQAlpha sys_progress output if available, or a fallback estimate
    based on job status.
    """
    job_dir = locate_job_dir(job_id)
    if job_dir is None:
        return _error_response(404, "NOT_FOUND", "not found")

    try:
        status_payload = read_status(job_dir)
    except (FileNotFoundError, OSError, ValueError):
        return _error_response(404, "STATUS_NOT_FOUND", "status not found")

    status = status_payload.get("status")

    # Try to read RQAlpha's sys_progress output
    progress_file = job_dir / "progress.json"
    if progress_file.exists():
        try:
            progress_data = json.loads(progress_file.read_text(encoding="utf-8"))
            # RQAlpha sys_progress provides detailed progress
            return jsonify({
                "job_id": job_id,
                "status": status,
                "progress": progress_data,
            })
        except (OSError, json.JSONDecodeError):
            pass  # Fall through to default progress

    # Fallback: estimate progress based on status
    default_progress = {
        "percentage": 0,
        "stage": "queued",
    }
    if status == "RUNNING":
        default_progress = {
            "percentage": 50,
            "stage": "backtesting",
        }
    elif status == "FINISHED":
        default_progress = {
            "percentage": 100,
            "stage": "finished",
        }
    elif status in ("FAILED", "CANCELLED"):
        default_progress = {
            "percentage": 100,
            "stage": status.lower(),
        }

    return jsonify({
        "job_id": job_id,
        "status": status,
        "progress": default_progress,
    })


@bp_backtest.delete("/jobs/<job_id>")
@auth_required
def api_delete_job(job_id: str):
    try:
        deleted = delete_job(job_id)
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))

    if not deleted:
        return _error_response(404, "NOT_FOUND", "job not found")

    return _ok_response({"job_id": job_id, "deleted": True}, message="deleted")


import math


def _sanitize_nan(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    return obj


def _normalize_result_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    payload = _sanitize_nan(payload)
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    def _normalize_nav_series(value: object) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in ("benchmark_nav", "nav", "unit_net_value", "values", "curve"):
                nav_value = value.get(key)
                if isinstance(nav_value, list):
                    return nav_value
        return []

    equity = payload.get("equity")
    if not isinstance(equity, dict):
        equity = {}
    dates = equity.get("dates")
    nav = equity.get("nav")
    returns = equity.get("returns")
    benchmark_nav = _normalize_nav_series(equity.get("benchmark_nav"))
    if not benchmark_nav:
        for key in ("benchmark_nav", "benchmark_curve", "benchmark_equity", "benchmark_portfolio"):
            benchmark_nav = _normalize_nav_series(payload.get(key))
            if benchmark_nav:
                break
    normalized_equity = {
        "dates": dates if isinstance(dates, list) else [],
        "nav": nav if isinstance(nav, list) else [],
        "returns": returns if isinstance(returns, list) else [],
        "benchmark_nav": benchmark_nav,
    }
    trades = payload.get("trades")
    if not isinstance(trades, list):
        trades = []
    trade_columns = payload.get("trade_columns")
    if not isinstance(trade_columns, list):
        if trades and isinstance(trades[0], dict):
            trade_columns = [str(key) for key in trades[0].keys()]
        else:
            trade_columns = []
    raw_keys = payload.get("raw_keys")
    if not isinstance(raw_keys, list):
        raw_keys = sorted([str(key) for key in payload.keys()])
    return {
        "summary": summary,
        "equity": normalized_equity,
        "trades": trades,
        "trade_columns": trade_columns,
        "raw_keys": raw_keys,
    }

@bp_backtest.get("/jobs/<job_id>/result")
@auth_required
def api_job_result(job_id: str):
    job_dir = locate_job_dir(job_id)
    if job_dir is None:
        return _error_response(404, "NOT_FOUND", "not found")

    try:
        status_payload = read_status(job_dir)
    except (FileNotFoundError, OSError, ValueError):
        return _error_response(404, "STATUS_NOT_FOUND", "status not found")

    status = status_payload.get("status")
    if status != "FINISHED":
        error = status_payload.get("error")
        return _error_response(
            409,
            "RESULT_NOT_READY",
            "result not ready",
            status=status,
            detail=error.get("message") if error else None,
        )

    path = job_dir / "extracted.json"
    if not path.exists():
        return _error_response(500, "RESULT_FILE_MISSING", "result file missing")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _error_response(500, "RESULT_PARSE_ERROR", "result file is invalid")

    normalized = _normalize_result_payload(payload)

    page_raw = request.args.get("page")
    page_size_raw = request.args.get("page_size")
    if page_raw is not None or page_size_raw is not None:
        try:
            page = _parse_int_arg("page", 1, min_value=1)
            page_size = _parse_int_arg("page_size", 100, min_value=1, max_value=1000)
        except ValueError as exc:
            return _error_response(400, "INVALID_ARGUMENT", str(exc))
        trades = normalized["trades"]
        trades_total = len(trades)
        start = (page - 1) * page_size
        end = start + page_size
        normalized["trades"] = trades[start:end]
        normalized["trades_total"] = trades_total
        normalized["page"] = page
        normalized["page_size"] = page_size
    else:
        normalized["trades_total"] = len(normalized["trades"])

    return jsonify(normalized)


def _read_log_slice(log_path: Path, *, offset: int | None, tail: int | None) -> tuple[str, int, int, int]:
    size = log_path.stat().st_size
    start = 0
    if offset is not None:
        start = max(0, min(offset, size))
    elif tail is not None:
        start = max(size - tail, 0)

    with log_path.open("rb") as f:
        f.seek(start)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    next_offset = start + len(data)
    return text, start, next_offset, size

@bp_backtest.get("/jobs/<job_id>/log")
@auth_required
def api_job_log(job_id: str):
    job_dir = locate_job_dir(job_id)
    if job_dir is None:
        return _error_response(404, "NOT_FOUND", "not found")

    log_path = job_dir / "run.log"
    if not log_path.exists():
        return _error_response(404, "LOG_NOT_FOUND", "log not found")

    offset = request.args.get("offset")
    tail = request.args.get("tail")
    if offset is not None and tail is not None:
        return _error_response(400, "INVALID_ARGUMENT", "offset and tail cannot be used together")

    if offset is None and tail is None:
        return current_app.response_class(log_path.read_text(encoding="utf-8"), mimetype="text/plain")

    try:
        offset_value = _parse_int_arg("offset", 0, min_value=0) if offset is not None else None
        tail_value = _parse_int_arg("tail", 0, min_value=1, max_value=1024 * 1024) if tail is not None else None
    except ValueError as exc:
        return _error_response(400, "INVALID_ARGUMENT", str(exc))

    content, start, next_offset, size = _read_log_slice(log_path, offset=offset_value, tail=tail_value)
    return jsonify(
        {
            "job_id": job_id,
            "content": content,
            "offset": start,
            "next_offset": next_offset,
            "size": size,
        }
    )
