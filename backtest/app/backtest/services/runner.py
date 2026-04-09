from __future__ import annotations

import argparse
import json
import hashlib
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import textwrap
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from app.database import DatabaseConnection, get_db_connection

_STRATEGY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-\u4E00-\u9FFF]+$")
_STRATEGY_ID_MAX_LENGTH = 128
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
VALID_JOB_STATUSES = {"QUEUED", "RUNNING", "FAILED", "FINISHED", "CANCELLED"}
RQALPHA_CANCELLED_EXIT_CODE = -99999
_INDEX_KEEP = object()
_NOTEBOOK_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d")
_NOTEBOOK_DEFAULT_FREQUENCY = "1d"
_NOTEBOOK_DEFAULT_INIT_CASH = 100000
_NOTEBOOK_DEFAULT_BENCHMARK = "000300.XSHG"
_NOTEBOOK_DEFAULT_OUTPUT_ROOT = Path("research") / "artifacts" / "backtests"
_NOTEBOOK_LOGGER_NAME = "backtest.research.runner"
_DEFAULT_DEMO_STRATEGY_ID = "demo"
_DEFAULT_DEMO_STRATEGY_CODE = textwrap.dedent(
    """\
    # RQAlpha 默认策略示例
    from rqalpha.api import *

    def init(context):
        # 选择一个股票（平安银行）
        context.s1 = "000001.XSHE"

    def handle_bar(context, bar_dict):
        # 如果当前没有持仓
        position = context.portfolio.positions[context.s1]

        if position.quantity == 0:
            # 用全部资金买入
            order_percent(context.s1, 1.0)
    """
)

_GOLDEN_CROSS_DEMO_STRATEGY_ID = "golden_cross_demo"
_GOLDEN_CROSS_DEMO_STRATEGY_CODE = textwrap.dedent(
    """\
    import numpy as np
    from rqalpha.api import *


    # 在这个方法中编写任何的初始化逻辑。context对象将会在你的算法策略的任何方法之间做传递。
    def init(context):
        context.s1 = "000001.XSHE"

        # 使用MACD需要设置长短均线和macd平均线的参数
        context.SHORTPERIOD = 12
        context.LONGPERIOD = 26
        context.SMOOTHPERIOD = 9
        context.OBSERVATION = 100


    # 你选择的证券的数据更新将会触发此段逻辑，例如日或分钟历史数据切片或者是实时数据切片更新
    def handle_bar(context, bar_dict):
        # 开始编写你的主要的算法逻辑

        # bar_dict[order_book_id] 可以拿到某个证券的bar信息
        # context.portfolio 可以拿到现在的投资组合状态信息

        # 使用order_shares(id_or_ins, amount)方法进行落单

        # TODO: 开始编写你的算法吧！

        # 读取历史数据，使用sma方式计算均线准确度和数据长度无关，但是在使用ema方式计算均线时建议将历史数据窗口适当放大，结果会更加准确
        prices = history_bars(context.s1, context.OBSERVATION,'1d','close')

        # 手动计算MACD
        def ema(prices, period):
            weights = np.exp(np.linspace(-1., 0., period))
            weights /= weights.sum()
            return np.convolve(prices, weights, mode='valid')
        
        ema12 = ema(prices, context.SHORTPERIOD)
        ema26 = ema(prices, context.LONGPERIOD)
        macd = ema12[-len(ema26):] - ema26
        signal = ema(macd, context.SMOOTHPERIOD)
        hist = macd[-len(signal):] - signal

        plot("macd", macd[-1])
        plot("macd signal", signal[-1])

        # macd 是长短均线的差值，signal是macd的均线，使用macd策略有几种不同的方法，我们这里采用macd线突破signal线的判断方法

        # 如果macd从上往下跌破macd_signal

        if macd[-1] - signal[-1] < 0 and macd[-2] - signal[-2] > 0:
            # 获取当前投资组合中股票的仓位
            curPosition = get_position(context.s1).quantity
            #进行清仓
            if curPosition > 0:
                order_target_value(context.s1, 0)

        # 如果短均线从下往上突破长均线，为入场信号
        if macd[-1] - signal[-1] > 0 and macd[-2] - signal[-2] < 0:
            # 满仓入股
            order_target_percent(context.s1, 1)
    """
)

# 内置策略列表（不可删除）
_BUILTIN_STRATEGY_IDS = {_DEFAULT_DEMO_STRATEGY_ID, _GOLDEN_CROSS_DEMO_STRATEGY_ID}

_BUILTIN_STRATEGIES = {
    _DEFAULT_DEMO_STRATEGY_ID: _DEFAULT_DEMO_STRATEGY_CODE,
    _GOLDEN_CROSS_DEMO_STRATEGY_ID: _GOLDEN_CROSS_DEMO_STRATEGY_CODE,
}


_PROCESS_LOCK = threading.Lock()
_RUNNING_PROCESSES: dict[str, subprocess.Popen] = {}
_CANCEL_REQUESTED_JOB_IDS: set[str] = set()
_RENAME_LOCK = threading.Lock()
_RENAME_DB_FILENAME = "backtest_meta.sqlite3"

_BACKTEST_META_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS backtest_strategy_rename_map (
    from_id TEXT NOT NULL PRIMARY KEY,
    to_id TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT,
    CHECK (from_id <> to_id)
)
"""


def _ensure_backtest_meta_schema(db) -> None:
    """Create backtest_meta tables if they do not exist (SQLite only).

    For MariaDB the schema is managed by db/init.sql at container startup.
    """
    if db.config.db_type == 'sqlite':
        db.execute(_BACKTEST_META_DDL_SQLITE)
_COMPILE_SANDBOX_DIR_NAME = "compile_sandbox"
_COMPILE_WORKER_SOURCE = textwrap.dedent(
    """\
    import ast
    import importlib.util
    import json
    import sys

    def _to_int(value, default):
        try:
            ivalue = int(value)
            if ivalue < 0:
                return default
            return ivalue
        except Exception:
            return default

    def _diag(line, column, level, message):
        return {
            "line": _to_int(line, 0),
            "column": _to_int(column, 0),
            "level": str(level or "error"),
            "message": str(message or ""),
        }

    def _normalize_import_name(name):
        if not isinstance(name, str):
            return ""
        normalized = name.strip()
        if not normalized:
            return ""
        return normalized.split(".", 1)[0]

    payload_raw = sys.stdin.read()
    if not payload_raw.strip():
        payload = {}
    else:
        payload = json.loads(payload_raw)
    code = payload.get("code")
    if not isinstance(code, str):
        code = ""

    diagnostics = []
    stdout_lines = []
    stderr_lines = []
    ok = True

    syntax_tree = None
    try:
        syntax_tree = ast.parse(code, filename="strategy.py")
        stdout_lines.append("syntax check passed")
    except SyntaxError as exc:
        ok = False
        diagnostics.append(
            _diag(
                getattr(exc, "lineno", 0),
                getattr(exc, "offset", 0),
                "error",
                "syntax error: {0}".format(getattr(exc, "msg", "invalid syntax")),
            )
        )
        stderr_lines.append("syntax check failed")

    if syntax_tree is not None:
        import_sites = {}
        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = _normalize_import_name(alias.name)
                    if module_name and module_name not in import_sites:
                        import_sites[module_name] = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0) + 1)
            elif isinstance(node, ast.ImportFrom):
                line = getattr(node, "lineno", 0)
                col = getattr(node, "col_offset", 0) + 1
                if getattr(node, "level", 0):
                    ok = False
                    diagnostics.append(_diag(line, col, "error", "relative import is not supported in compile sandbox"))
                    continue
                module_name = _normalize_import_name(getattr(node, "module", ""))
                if module_name and module_name not in import_sites:
                    import_sites[module_name] = (line, col)

        for module_name, (line, col) in import_sites.items():
            try:
                # 尝试直接导入模块，这是最直接的检测方法
                __import__(module_name)
                # 导入成功，模块已安装
                spec = importlib.util.find_spec(module_name)
            except ImportError:
                # 导入失败，尝试使用 find_spec
                try:
                    spec = importlib.util.find_spec(module_name)
                except Exception as exc:
                    ok = False
                    diagnostics.append(_diag(line, col, "error", "dependency check failed for '{0}': {1}".format(module_name, exc)))
                    continue
            except Exception as exc:
                # 其他错误，尝试使用 find_spec
                try:
                    spec = importlib.util.find_spec(module_name)
                except Exception as exc2:
                    ok = False
                    diagnostics.append(_diag(line, col, "error", "dependency check failed for '{0}': {1}".format(module_name, exc2)))
                    continue
            if spec is None:
                ok = False
                diagnostics.append(_diag(line, col, "error", "dependency '{0}' is not installed".format(module_name)))

        if ok:
            stdout_lines.append("dependency check passed")
        else:
            stderr_lines.append("dependency check failed")

    diagnostics.sort(key=lambda item: (item.get("line", 0), item.get("column", 0), item.get("message", "")))
    if diagnostics:
        detail_lines = []
        for item in diagnostics:
            line = _to_int(item.get("line"), 0)
            column = _to_int(item.get("column"), 0)
            message = str(item.get("message") or "")
            if not message:
                continue
            if line > 0:
                detail_lines.append("line {0}, column {1}: {2}".format(line, max(1, column), message))
            else:
                detail_lines.append(message)
        if detail_lines:
            stderr_lines.append("\\n".join(detail_lines))
    result = {
        "ok": bool(ok),
        "stdout": "\\n".join(stdout_lines).strip(),
        "stderr": "\\n".join(stderr_lines).strip(),
        "diagnostics": diagnostics,
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    """
)


class StrategyReferencedError(RuntimeError):
    def __init__(self, strategy_id: str, job_ids: list[str]):
        self.strategy_id = strategy_id
        self.job_ids = job_ids
        super().__init__(f"strategy '{strategy_id}' is referenced by existing jobs")


class StrategyRenameConflictError(RuntimeError):
    pass


class StrategyRenameCycleError(RuntimeError):
    pass


def _now_iso8601() -> str:
    return datetime.now().astimezone().isoformat()


def _base_dir() -> Path:
    base = Path(current_app.config["BACKTEST_BASE_DIR"]).expanduser()
    if not base.is_absolute():
        raise ValueError("BACKTEST_BASE_DIR must be an absolute path")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _storage_dirs() -> dict[str, Path]:
    base = _base_dir()
    strategies_dir = base / "strategies"
    runs_dir = base / "runs"
    runs_index_dir = base / "runs_index"
    dedupe_index_dir = base / "dedupe_index"
    strategies_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs_index_dir.mkdir(parents=True, exist_ok=True)
    dedupe_index_dir.mkdir(parents=True, exist_ok=True)
    return {
        "base": base,
        "strategies": strategies_dir,
        "runs": runs_dir,
        "runs_index": runs_index_dir,
        "dedupe_index": dedupe_index_dir,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(json.dumps(payload, ensure_ascii=False))
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _validate_strategy_id(strategy_id: str) -> str:
    if not isinstance(strategy_id, str):
        raise ValueError("strategy_id must be a string")
    normalized = strategy_id
    if not normalized:
        raise ValueError("strategy_id is empty")
    if len(normalized) > _STRATEGY_ID_MAX_LENGTH:
        raise ValueError(f"strategy_id exceeds max length {_STRATEGY_ID_MAX_LENGTH}")
    if not _STRATEGY_ID_PATTERN.fullmatch(normalized):
        raise ValueError("strategy_id contains invalid characters")
    return normalized


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str):
        raise ValueError("job_id must be a string")
    normalized = job_id.strip()
    if not normalized:
        raise ValueError("job_id is empty")
    if not _JOB_ID_PATTERN.fullmatch(normalized):
        raise ValueError("job_id contains invalid characters")
    return normalized


def normalize_strategy_id(strategy_id: str) -> str:
    return _validate_strategy_id(strategy_id)


def _rename_db_path() -> Path:
    configured = str(current_app.config.get("BACKTEST_RENAME_DB_PATH", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = (_base_dir() / path).resolve()
    else:
        path = _base_dir() / _RENAME_DB_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _rename_db_transaction():
    """Context manager that yields an open DatabaseConnection inside a transaction.

    SQLite: enables WAL + foreign keys, uses BEGIN IMMEDIATE for exclusive writes.
    MariaDB: uses conn.begin() to start a transaction (disables autocommit).
    commit() is called on success; rollback() on any exception.
    """
    with get_db_connection('backtest_meta') as db:
        _ensure_backtest_meta_schema(db)
        if db.config.db_type == 'sqlite':
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA journal_mode = WAL")
        db.begin_transaction()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def _fetch_rename_map(db: DatabaseConnection) -> dict[str, str]:
    rows = db.fetchall(
        """
        SELECT from_id, to_id
        FROM backtest_strategy_rename_map
        """
    )
    mapping: dict[str, str] = {}
    for row in rows:
        from_id = row["from_id"]
        to_id = row["to_id"]
        if not isinstance(from_id, str) or not isinstance(to_id, str):
            continue
        try:
            normalized_from = _validate_strategy_id(from_id)
            normalized_to = _validate_strategy_id(to_id)
        except ValueError:
            current_app.logger.warning(
                "ignore invalid strategy rename mapping: from_id=%r to_id=%r",
                from_id,
                to_id,
            )
            continue
        mapping[normalized_from] = normalized_to
    return mapping


def resolve_current_strategy_id(strategy_id: str, rename_map: dict[str, str] | None = None) -> str:
    current = _validate_strategy_id(strategy_id)
    mapping = rename_map if rename_map is not None else get_strategy_rename_map()
    visited: set[str] = set()

    while True:
        if current in visited:
            raise StrategyRenameCycleError(f"strategy rename map cycle detected at '{current}'")
        visited.add(current)
        next_id = mapping.get(current)
        if not next_id:
            return current
        current = _validate_strategy_id(next_id)


def _compress_rename_map(rename_map: dict[str, str]) -> dict[str, str]:
    compressed: dict[str, str] = {}
    for from_id in sorted(rename_map):
        canonical = resolve_current_strategy_id(from_id, rename_map)
        if canonical != from_id:
            compressed[from_id] = canonical
    return compressed


def _sync_rename_map(
    db: DatabaseConnection,
    *,
    mapping: dict[str, str],
    updated_by: str | None = None,
) -> None:
    current = _fetch_rename_map(db)
    current_keys = set(current)
    target_keys = set(mapping)

    for from_id in current_keys - target_keys:
        db.execute(
            "DELETE FROM backtest_strategy_rename_map WHERE from_id = ?",
            (from_id,),
        )

    now = datetime.utcnow().isoformat()
    for from_id, to_id in mapping.items():
        if current.get(from_id) == to_id:
            continue
        db.upsert(
            table='backtest_strategy_rename_map',
            insert_cols=['from_id', 'to_id', 'updated_by', 'updated_at'],
            insert_vals=(from_id, to_id, updated_by, now),
            conflict_col='from_id',
            update_cols=['to_id', 'updated_by', 'updated_at'],
        )


def get_strategy_rename_map() -> dict[str, str]:
    with get_db_connection('backtest_meta') as db:
        _ensure_backtest_meta_schema(db)
        raw_map = _fetch_rename_map(db)
    return _compress_rename_map(raw_map)


def _record_rename_in_map(rename_map: dict[str, str], *, from_id: str, to_id: str) -> dict[str, str]:
    normalized_from = _validate_strategy_id(from_id)
    normalized_to = _validate_strategy_id(to_id)
    if normalized_from == normalized_to:
        return _compress_rename_map(rename_map)

    compressed = _compress_rename_map(rename_map)
    canonical_to = resolve_current_strategy_id(normalized_to, compressed)
    canonical_from = resolve_current_strategy_id(normalized_from, compressed)

    # Idempotent write if source and target already resolve to the same canonical ID.
    if canonical_from == canonical_to:
        idempotent = dict(compressed)
        if normalized_from != canonical_to:
            idempotent[normalized_from] = canonical_to
        idempotent.pop(canonical_to, None)
        return _compress_rename_map(idempotent)

    updated = dict(compressed)
    for key in list(updated):
        if resolve_current_strategy_id(key, compressed) == canonical_from:
            updated[key] = canonical_to
    updated[canonical_from] = canonical_to

    # Avoid having target ID appear as an old ID key.
    updated.pop(canonical_to, None)

    flattened = _compress_rename_map(updated)
    _ = resolve_current_strategy_id(canonical_from, flattened)
    return flattened


def record_strategy_rename(
    from_id: str,
    to_id: str,
    *,
    updated_by: str | None = None,
) -> dict[str, str]:
    normalized_from = _validate_strategy_id(from_id)
    normalized_to = _validate_strategy_id(to_id)
    if normalized_from == normalized_to:
        return get_strategy_rename_map()

    with _RENAME_LOCK, _rename_db_transaction() as db:
        current = _fetch_rename_map(db)
        flattened = _record_rename_in_map(current, from_id=normalized_from, to_id=normalized_to)
        _sync_rename_map(db, mapping=flattened, updated_by=updated_by)
        return flattened


def list_strategy_aliases(strategy_id: str) -> tuple[str, set[str]]:
    normalized = _validate_strategy_id(strategy_id)
    rename_map = get_strategy_rename_map()
    canonical = resolve_current_strategy_id(normalized, rename_map)
    aliases = {canonical}
    for legacy_id, current_id in rename_map.items():
        if current_id == canonical:
            aliases.add(legacy_id)
    return canonical, aliases


def _timestamp_to_utc_iso8601(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_utc_iso8601() -> str:
    return _timestamp_to_utc_iso8601(time.time())


def _iso8601_to_timestamp(value: str) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _dedupe_index_path(fingerprint: str) -> Path:
    return _storage_dirs()["dedupe_index"] / f"{fingerprint}.json"


def _strategy_path(strategy_id: str) -> Path:
    normalized = _validate_strategy_id(strategy_id)
    return _storage_dirs()["strategies"] / f"{normalized}.py"


def _strategy_meta_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def _read_strategy_created_at(path: Path) -> str | None:
    meta_path = _strategy_meta_path(path)
    if not meta_path.exists():
        return None
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    created_at_raw = payload.get("created_at")
    if not isinstance(created_at_raw, str) or not created_at_raw.strip():
        return None
    created_at_ts = _iso8601_to_timestamp(created_at_raw)
    if created_at_ts is None:
        return None
    return _timestamp_to_utc_iso8601(created_at_ts)


def _write_strategy_created_at(path: Path, created_at: str) -> None:
    created_at_ts = _iso8601_to_timestamp(created_at)
    if created_at_ts is None:
        raise ValueError("created_at must be a valid ISO 8601 datetime")
    _write_json(_strategy_meta_path(path), {"created_at": _timestamp_to_utc_iso8601(created_at_ts)})


def _delete_strategy_meta(path: Path) -> None:
    try:
        _strategy_meta_path(path).unlink()
    except FileNotFoundError:
        return


def _strategy_record_from_path(path: Path) -> dict:
    stat = path.stat()
    return {
        "id": path.stem,
        "created_at": _read_strategy_created_at(path),
        "updated_at": _timestamp_to_utc_iso8601(stat.st_mtime),
        "size": stat.st_size,
    }


def save_strategy(strategy_id: str, code: str) -> Path:
    path = _strategy_path(strategy_id)
    existed_before = path.exists()
    path.write_text(code, encoding="utf-8")
    if not existed_before:
        _write_strategy_created_at(path, _now_utc_iso8601())
    return path


def ensure_default_demo_strategy() -> bool:
    """确保所有内置策略存在"""
    success = True
    for strategy_id, strategy_code in _BUILTIN_STRATEGIES.items():
        try:
            path = _strategy_path(strategy_id)
        except Exception:
            logging.getLogger(__name__).exception(f"failed to resolve {strategy_id} strategy path")
            success = False
            continue

        if path.exists():
            continue

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(strategy_code, encoding="utf-8")
            _write_strategy_created_at(path, _now_utc_iso8601())
        except Exception:
            logging.getLogger(__name__).exception(f"failed to create {strategy_id} strategy")
            success = False

    return success


def load_strategy(strategy_id: str) -> str:
    # 优先返回内置策略
    if strategy_id in _BUILTIN_STRATEGIES:
        return _BUILTIN_STRATEGIES[strategy_id]

    path = _strategy_path(strategy_id)
    if not path.exists():
        raise FileNotFoundError(f"strategy {path.stem} not found")
    return path.read_text(encoding="utf-8")


def load_strategy_metadata(strategy_id: str) -> dict:
    path = _strategy_path(strategy_id)
    if not path.exists():
        raise FileNotFoundError(f"strategy {path.stem} not found")
    return _strategy_record_from_path(path)


def load_strategy_detail(strategy_id: str) -> dict:
    path = _strategy_path(strategy_id)
    if not path.exists():
        raise FileNotFoundError(f"strategy {path.stem} not found")
    payload = _strategy_record_from_path(path)
    payload["code"] = path.read_text(encoding="utf-8")
    return payload


def list_strategies(*, q: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    query = (q or "").strip().lower()
    strategies: list[dict] = []
    for path in _storage_dirs()["strategies"].glob("*.py"):
        strategy_id = path.stem
        if query and query not in strategy_id.lower():
            continue
        payload = _strategy_record_from_path(path)
        payload["_sort_mtime"] = path.stat().st_mtime
        strategies.append(payload)

    strategies.sort(key=lambda item: item["_sort_mtime"], reverse=True)
    total = len(strategies)
    paginated = strategies[offset : offset + limit]
    for item in paginated:
        item.pop("_sort_mtime", None)
    return paginated, total


def _strategy_id_from_job_index_payload(index_payload: dict) -> str | None:
    strategy_id = index_payload.get("strategy_id")
    if isinstance(strategy_id, str) and strategy_id.strip():
        return strategy_id.strip()

    job_dir_raw = index_payload.get("job_dir")
    if not isinstance(job_dir_raw, str) or not job_dir_raw.strip():
        return None

    job_dir = Path(job_dir_raw).expanduser()
    meta = _read_job_meta(job_dir)
    strategy_from_meta = meta.get("strategy_id")
    if isinstance(strategy_from_meta, str) and strategy_from_meta.strip():
        return strategy_from_meta.strip()
    return None


def _collect_strategy_reference_job_ids(accepted_strategy_ids: set[str]) -> list[str]:
    if not accepted_strategy_ids:
        return []
    job_ids: list[str] = []
    seen: set[str] = set()
    index_dir = _storage_dirs()["runs_index"]
    for index_path in index_dir.glob("*.json"):
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        strategy_value = _strategy_id_from_job_index_payload(payload)
        if strategy_value not in accepted_strategy_ids:
            continue

        job_id_raw = payload.get("job_id")
        if not isinstance(job_id_raw, str) or not job_id_raw.strip():
            job_id_raw = index_path.stem
        try:
            job_id = _validate_job_id(job_id_raw)
        except ValueError:
            continue
        if job_id in seen:
            continue
        seen.add(job_id)
        job_ids.append(job_id)
    return job_ids


def find_strategy_reference_job_ids(strategy_id: str, *, limit: int = 20) -> list[str]:
    normalized = _validate_strategy_id(strategy_id)
    _, accepted_strategy_ids = list_strategy_aliases(normalized)
    max_items = max(1, int(limit))
    return _collect_strategy_reference_job_ids(accepted_strategy_ids)[:max_items]


def _delete_job_artifacts(job_id: str) -> bool:
    normalized_job_id = _validate_job_id(job_id)
    dirs = _storage_dirs()
    index_path = dirs["runs_index"] / f"{normalized_job_id}.json"
    job_dir = locate_job_dir(normalized_job_id)
    existed = bool(job_dir and job_dir.exists()) or index_path.exists()

    with _PROCESS_LOCK:
        proc = _RUNNING_PROCESSES.get(normalized_job_id)
    if proc is not None:
        try:
            _terminate_process(proc)
        except Exception:
            pass
    clear_cancel_request(normalized_job_id)

    if index_path.exists():
        index_path.unlink()
    if job_dir is not None and job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=False)

    for dedupe_path in dirs["dedupe_index"].glob("*.json"):
        try:
            payload = json.loads(dedupe_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("job_id") or "") != normalized_job_id:
            continue
        try:
            dedupe_path.unlink()
        except FileNotFoundError:
            continue

    return existed


def delete_job(job_id: str) -> bool:
    return _delete_job_artifacts(job_id)


def delete_strategy(strategy_id: str) -> None:
    # 保护内置策略不被删除
    if strategy_id in _BUILTIN_STRATEGY_IDS:
        raise ValueError(f"Cannot delete built-in strategy: {strategy_id}")

    path = _strategy_path(strategy_id)
    if not path.exists():
        raise FileNotFoundError(f"strategy {path.stem} not found")

    reference_job_ids = find_strategy_reference_job_ids(path.stem, limit=5)
    if reference_job_ids:
        raise StrategyReferencedError(path.stem, reference_job_ids)

    path.unlink()
    _delete_strategy_meta(path)


def delete_strategy_cascade(
    strategy_id: str,
    *,
    updated_by: str | None = None,
) -> tuple[str, int]:
    normalized_strategy_id = _validate_strategy_id(strategy_id)

    # 保护内置策略不被删除
    if normalized_strategy_id in _BUILTIN_STRATEGY_IDS:
        raise ValueError(f"Cannot delete built-in strategy: {normalized_strategy_id}")

    with _RENAME_LOCK, _rename_db_transaction() as db:
        raw_map = _fetch_rename_map(db)
        flattened_map = _compress_rename_map(raw_map)
        canonical_strategy_id = resolve_current_strategy_id(normalized_strategy_id, flattened_map)

        strategy_path = _strategy_path(canonical_strategy_id)
        if not strategy_path.exists():
            raise FileNotFoundError(f"strategy {canonical_strategy_id} not found")

        accepted_strategy_ids = {canonical_strategy_id}
        for from_id, to_id in flattened_map.items():
            if to_id == canonical_strategy_id:
                accepted_strategy_ids.add(from_id)

        job_ids = _collect_strategy_reference_job_ids(accepted_strategy_ids)
        for job_id in job_ids:
            _delete_job_artifacts(job_id)

        strategy_path.unlink()
        _delete_strategy_meta(strategy_path)

        filtered_map = {
            from_id: to_id
            for from_id, to_id in flattened_map.items()
            if from_id not in accepted_strategy_ids and to_id not in accepted_strategy_ids
        }
        _sync_rename_map(db, mapping=filtered_map, updated_by=updated_by)

    return canonical_strategy_id, len(job_ids)


def rename_strategy(
    from_id: str,
    to_id: str,
    *,
    code: str | None = None,
    updated_by: str | None = None,
) -> dict:
    normalized_from = _validate_strategy_id(from_id)
    normalized_to = _validate_strategy_id(to_id)

    if normalized_from == normalized_to:
        return {
            "from_id": normalized_from,
            "to_id": normalized_to,
            "deleted_old": False,
            "warning": "noop: from_id equals to_id",
            "no_op": True,
        }

    with _RENAME_LOCK, _rename_db_transaction() as db:
        current_map = _fetch_rename_map(db)
        flattened_map = _compress_rename_map(current_map)
        canonical_from = resolve_current_strategy_id(normalized_from, flattened_map)
        canonical_to = resolve_current_strategy_id(normalized_to, flattened_map)

        source_path = _strategy_path(canonical_from)
        if not source_path.exists():
            raise FileNotFoundError(f"strategy {canonical_from} not found")

        if canonical_from == canonical_to:
            return {
                "from_id": normalized_from,
                "to_id": canonical_to,
                "deleted_old": False,
                "warning": "noop: source already resolves to target",
                "no_op": True,
            }

        target_path = _strategy_path(canonical_to)
        if target_path.exists() and canonical_to != canonical_from:
            raise StrategyRenameConflictError(f"target strategy '{canonical_to}' already exists")

        source_code = source_path.read_text(encoding="utf-8")
        source_created_at = _read_strategy_created_at(source_path)
        target_code = source_code if code is None else code
        target_path.write_text(target_code, encoding="utf-8")
        if source_created_at is not None:
            _write_strategy_created_at(target_path, source_created_at)

        deleted_old = False
        try:
            source_path.unlink()
            _delete_strategy_meta(source_path)
            deleted_old = True

            flattened_next = _record_rename_in_map(flattened_map, from_id=canonical_from, to_id=canonical_to)
            _sync_rename_map(db, mapping=flattened_next, updated_by=updated_by)
        except Exception:
            if target_path.exists():
                try:
                    target_path.unlink()
                except OSError:
                    pass
            _delete_strategy_meta(target_path)
            if deleted_old:
                try:
                    source_path.write_text(source_code, encoding="utf-8")
                except OSError:
                    pass
                if source_created_at is not None:
                    try:
                        _write_strategy_created_at(source_path, source_created_at)
                    except OSError:
                        pass
            raise

        return {
            "from_id": normalized_from,
            "to_id": canonical_to,
            "deleted_old": deleted_old,
            "warning": "",
            "no_op": False,
        }


def upsert_strategy_rename_mapping(
    from_id: str,
    to_id: str,
    *,
    updated_by: str | None = None,
) -> dict[str, str]:
    normalized_from = _validate_strategy_id(from_id)
    normalized_to = _validate_strategy_id(to_id)
    if normalized_from == normalized_to:
        raise ValueError("from_id must be different from to_id")
    return record_strategy_rename(normalized_from, normalized_to, updated_by=updated_by)


def build_run_fingerprint(
    *,
    strategy_id: str,
    start_date: str,
    end_date: str,
    cash: int,
    benchmark: str,
    frequency: str,
    code: str,
) -> str:
    payload = {
        "strategy_id": strategy_id,
        "start_date": start_date,
        "end_date": end_date,
        "cash": cash,
        "benchmark": benchmark,
        "frequency": frequency,
        "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bind_run_fingerprint(fingerprint: str, job_id: str) -> Path:
    path = _dedupe_index_path(fingerprint)
    _write_json(
        path,
        {
            "fingerprint": fingerprint,
            "job_id": job_id,
            "updated_at_ts": time.time(),
        },
    )
    return path


def find_reusable_job_id(fingerprint: str, window_seconds: int) -> str | None:
    if window_seconds <= 0:
        return None
    path = _dedupe_index_path(fingerprint)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        job_id = str(payload["job_id"])
        updated_at_ts = float(payload.get("updated_at_ts", 0))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

    if time.time() - updated_at_ts > window_seconds:
        return None

    job_dir = locate_job_dir(job_id)
    if job_dir is None:
        return None

    try:
        status_payload = read_status(job_dir)
    except (FileNotFoundError, OSError, ValueError):
        return None

    # Terminal failed/cancelled tasks should be re-runnable; others can be reused in the idempotency window.
    if status_payload.get("status") in {"FAILED", "CANCELLED"}:
        return None

    bind_run_fingerprint(fingerprint, job_id)
    return job_id


def write_status(
    job_dir: Path,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    if status not in VALID_JOB_STATUSES:
        raise ValueError(f"invalid job status: {status}")
    if error_code and not error_message:
        raise ValueError("error_message is required when error_code is provided")
    payload = {
        "status": status,
        "error": (
            {
                "code": error_code,
                "message": error_message,
            }
            if error_code
            else None
        ),
        "updated_at": _now_iso8601(),
    }
    _write_json(job_dir / "status.json", payload)
    try:
        update_job_index(
            job_dir.name,
            status=status,
            updated_at=payload["updated_at"],
            error=payload["error"],
        )
    except Exception:
        # Status file is the source of truth; index sync failure should not break writes.
        pass
    return payload


def read_status(job_dir: Path) -> dict:
    path = job_dir / "status.json"
    if not path.exists():
        raise FileNotFoundError(f"status file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = payload.get("status")
    if status not in VALID_JOB_STATUSES:
        raise ValueError(f"invalid job status: {status}")

    raw_error = payload.get("error")
    error = None
    if isinstance(raw_error, dict):
        code = raw_error.get("code")
        message = raw_error.get("message")
        if code and message:
            error = {"code": str(code), "message": str(message)}
    elif isinstance(raw_error, str) and raw_error:
        # Backward compatibility for legacy status.json files.
        error = {"code": "LEGACY_ERROR", "message": raw_error}

    return {
        "status": status,
        "error": error,
        "updated_at": payload.get("updated_at"),
    }


def write_job_index(job_id: str, job_dir: Path) -> Path:
    index_path = _storage_dirs()["runs_index"] / f"{job_id}.json"
    payload = {
        "job_id": job_id,
        "job_dir": str(job_dir.resolve()),
        "updated_at": _now_iso8601(),
    }
    _write_json(index_path, payload)
    return index_path


def update_job_index(
    job_id: str,
    *,
    strategy_id: str | None = None,
    status: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    params: dict | None = None,
    error=_INDEX_KEEP,
) -> Path | None:
    index_path = _storage_dirs()["runs_index"] / f"{job_id}.json"
    if not index_path.exists():
        return None

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        payload = {}

    payload["job_id"] = str(payload.get("job_id") or job_id)

    if strategy_id is not None:
        payload["strategy_id"] = _validate_strategy_id(strategy_id)
    if status is not None:
        if status not in VALID_JOB_STATUSES:
            raise ValueError(f"invalid job status: {status}")
        payload["status"] = status
    if created_at is not None:
        payload["created_at"] = created_at
    if updated_at is not None:
        payload["updated_at"] = updated_at
    elif not payload.get("updated_at"):
        payload["updated_at"] = _now_iso8601()
    if isinstance(params, dict):
        payload["params"] = params
    if error is not _INDEX_KEEP:
        payload["error"] = error

    _write_json(index_path, payload)
    return index_path


def write_job_meta(
    job_dir: Path,
    *,
    strategy_id: str,
    start_date: str,
    end_date: str,
    cash: int | float,
    benchmark: str,
    frequency: str,
    code_sha256: str,
) -> Path:
    normalized_strategy_id = _validate_strategy_id(strategy_id)
    payload = {
        "strategy_id": normalized_strategy_id,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "cash": cash,
        "benchmark": str(benchmark),
        "frequency": str(frequency),
        "code_sha256": str(code_sha256),
        "created_at": _now_iso8601(),
    }
    path = job_dir / "job_meta.json"
    _write_json(path, payload)
    return path


def _read_job_meta(job_dir: Path) -> dict:
    path = job_dir / "job_meta.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def list_strategy_jobs(
    strategy_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    status: str | None = None,
) -> tuple[list[dict], int]:
    normalized_strategy_id = _validate_strategy_id(strategy_id)
    canonical_strategy_id, accepted_strategy_ids = list_strategy_aliases(normalized_strategy_id)
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    status_filter = None
    if status is not None:
        status_filter = status.strip().upper()
        if status_filter not in VALID_JOB_STATUSES:
            allowed = ", ".join(sorted(VALID_JOB_STATUSES))
            raise ValueError(f"status must be one of: {allowed}")

    index_dir = _storage_dirs()["runs_index"]
    jobs: list[dict] = []

    for index_path in index_dir.glob("*.json"):
        try:
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(index_payload, dict):
            continue

        job_id = str(index_payload.get("job_id") or index_path.stem)
        job_dir_raw = index_payload.get("job_dir")
        if not isinstance(job_dir_raw, str) or not job_dir_raw.strip():
            continue
        job_dir = Path(job_dir_raw).expanduser()
        if not job_dir.is_absolute() or job_dir.name != job_id or not job_dir.is_dir():
            continue

        meta = _read_job_meta(job_dir)
        strategy_from_meta = meta.get("strategy_id")
        strategy_from_index = index_payload.get("strategy_id")
        strategy_value = strategy_from_index or strategy_from_meta
        if strategy_value not in accepted_strategy_ids:
            continue

        status_value = index_payload.get("status")
        error_value = index_payload.get("error")
        updated_at = index_payload.get("updated_at")

        if status_value not in VALID_JOB_STATUSES or not isinstance(updated_at, str):
            try:
                status_payload = read_status(job_dir)
                status_value = status_payload.get("status")
                error_value = status_payload.get("error")
                updated_at = status_payload.get("updated_at")
            except (FileNotFoundError, OSError, ValueError):
                status_value = None
                error_value = None
                updated_at = None

        if status_value not in VALID_JOB_STATUSES:
            continue
        if status_filter and status_value != status_filter:
            continue

        params_payload = index_payload.get("params")
        params = params_payload if isinstance(params_payload, dict) else {}
        if not params:
            params = {
                "start_date": meta.get("start_date"),
                "end_date": meta.get("end_date"),
                "cash": meta.get("cash"),
                "benchmark": meta.get("benchmark"),
                "frequency": meta.get("frequency"),
            }

        created_at = index_payload.get("created_at")
        if not isinstance(created_at, str) or not created_at.strip():
            created_at = meta.get("created_at")
        if not isinstance(created_at, str) or not created_at.strip():
            created_at = _timestamp_to_utc_iso8601(job_dir.stat().st_ctime)

        if not isinstance(updated_at, str) or not updated_at.strip():
            updated_at = _timestamp_to_utc_iso8601(job_dir.stat().st_mtime)

        sort_ts = _iso8601_to_timestamp(updated_at)
        if sort_ts is None:
            sort_ts = job_dir.stat().st_mtime

        jobs.append(
            {
                "job_id": job_id,
                "strategy_id": canonical_strategy_id,
                "status": status_value,
                "error": error_value if isinstance(error_value, dict) else None,
                "created_at": created_at,
                "updated_at": updated_at,
                "params": params,
                "_sort_ts": sort_ts,
            }
        )

    jobs.sort(key=lambda item: (item["_sort_ts"], item["job_id"]), reverse=True)
    total = len(jobs)
    paginated = jobs[offset : offset + limit]
    for item in paginated:
        item.pop("_sort_ts", None)
    return paginated, total


def locate_job_dir(job_id: str) -> Path | None:
    try:
        normalized_job_id = _validate_job_id(job_id)
    except ValueError:
        return None

    dirs = _storage_dirs()
    index_path = dirs["runs_index"] / f"{normalized_job_id}.json"
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            job_dir = Path(payload["job_dir"]).expanduser()
            if job_dir.is_absolute() and job_dir.is_dir() and job_dir.name == normalized_job_id:
                return job_dir
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
            pass

    for date_bucket in dirs["runs"].iterdir():
        if not date_bucket.is_dir():
            continue
        candidate = date_bucket / normalized_job_id
        if candidate.is_dir():
            try:
                write_job_index(normalized_job_id, candidate)
            except OSError:
                pass
            return candidate
    return None


def cleanup_old_runs(keep_days: int | None = None) -> None:
    dirs = _storage_dirs()
    if keep_days is None:
        keep_days = int(current_app.config.get("BACKTEST_KEEP_DAYS", 30))
    if keep_days < 0:
        keep_days = 0
    threshold = date.today() - timedelta(days=keep_days)

    for date_bucket in dirs["runs"].iterdir():
        if not date_bucket.is_dir():
            continue
        try:
            bucket_date = date.fromisoformat(date_bucket.name)
        except ValueError:
            continue
        if bucket_date < threshold:
            shutil.rmtree(date_bucket, ignore_errors=False)


def create_job_dir() -> tuple[str, Path]:
    dirs = _storage_dirs()
    job_id = uuid.uuid4().hex
    job_dir = dirs["runs"] / date.today().isoformat() / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    write_job_index(job_id, job_dir)
    return job_id, job_dir


def build_config_yaml(
    *,
    start_date: str,
    end_date: str,
    cash: int,
    benchmark: str,
    frequency: str,
    output_file: str,
) -> str:
    bundle_path = Path(current_app.config["RQALPHA_BUNDLE_PATH"]).expanduser()
    if not bundle_path.is_absolute():
        raise ValueError("RQALPHA_BUNDLE_PATH must be an absolute path")

    result_path = Path(output_file)
    if not result_path.is_absolute():
        raise ValueError("output_file must be an absolute path")

    # Derive progress file path from result file
    progress_path = result_path.parent / "progress.json"

    return textwrap.dedent(
        f"""\
        version: 0.1.6
        whitelist: [base, extra, validator, mod]

        base:
          start_date: {start_date}
          end_date: {end_date}
          frequency: {frequency}
          data_bundle_path: {bundle_path.resolve()}
          benchmark: {benchmark}
          accounts:
            STOCK: {cash}
            FUTURE: {cash}

        mod:
          sys_analyser:
            enabled: true
            plot: false
            output_file: {result_path}
          sys_progress:
            enabled: true
            output_file: {progress_path}
        """
    )

def is_cancel_requested(job_id: str) -> bool:
    with _PROCESS_LOCK:
        return job_id in _CANCEL_REQUESTED_JOB_IDS


def clear_cancel_request(job_id: str) -> None:
    with _PROCESS_LOCK:
        _CANCEL_REQUESTED_JOB_IDS.discard(job_id)


def request_job_cancel(job_id: str) -> bool:
    with _PROCESS_LOCK:
        _CANCEL_REQUESTED_JOB_IDS.add(job_id)
        proc = _RUNNING_PROCESSES.get(job_id)

    if proc is None:
        return False
    try:
        if proc.poll() is None:
            proc.terminate()
            return True
    except Exception:
        return False
    return False


def _register_running_process(job_id: str, proc: subprocess.Popen) -> None:
    with _PROCESS_LOCK:
        _RUNNING_PROCESSES[job_id] = proc


def _unregister_running_process(job_id: str) -> None:
    with _PROCESS_LOCK:
        _RUNNING_PROCESSES.pop(job_id, None)


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _resolve_rqalpha_command() -> list[str]:
    configured_command = str(current_app.config.get("RQALPHA_COMMAND", "") or "").strip()
    if configured_command:
        command_parts = shlex.split(configured_command)
        if not command_parts:
            raise ValueError("RQALPHA_COMMAND is empty")
        return command_parts

    executable_path = shutil.which("rqalpha")
    if executable_path:
        return [executable_path]

    # Fallback for environments where PATH does not expose rqalpha.
    return [sys.executable, "-m", "rqalpha"]


def _normalize_compile_result(raw_payload: dict | None) -> dict:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    diagnostics_raw = payload.get("diagnostics")
    diagnostics: list[dict] = []
    if isinstance(diagnostics_raw, list):
        for item in diagnostics_raw:
            if not isinstance(item, dict):
                continue
            line = item.get("line", 0)
            column = item.get("column", 0)
            try:
                line = max(0, int(line))
            except (TypeError, ValueError):
                line = 0
            try:
                column = max(0, int(column))
            except (TypeError, ValueError):
                column = 0
            diagnostics.append(
                {
                    "line": line,
                    "column": column,
                    "level": str(item.get("level") or "error"),
                    "message": str(item.get("message") or ""),
                }
            )
    diagnostics.sort(key=lambda item: (item["line"], item["column"], item["message"]))
    return {
        "ok": bool(payload.get("ok")),
        "stdout": str(payload.get("stdout") or ""),
        "stderr": str(payload.get("stderr") or ""),
        "diagnostics": diagnostics,
    }


def _compile_preexec() -> None:
    try:
        import resource  # type: ignore

        memory_limit = 256 * 1024 * 1024
        file_limit = 2 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
    except Exception:
        # Best effort sandbox hardening; continue even if OS does not expose resource limits.
        pass


def compile_strategy_debug(code: str, *, timeout_seconds: int = 10) -> tuple[dict, str]:
    timeout = max(1, int(timeout_seconds))
    sandbox_root = _base_dir() / _COMPILE_SANDBOX_DIR_NAME
    sandbox_root.mkdir(parents=True, exist_ok=True)

    payload = json.dumps({"code": code}, ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix="compile_", dir=str(sandbox_root)) as sandbox_dir:
        # 保留系统的 PYTHONPATH，确保能找到已安装的包
        # 明确添加用户站点包路径，解决 -I 选项导致的问题
        python_path = os.environ.get("PYTHONPATH", "")
        # 添加用户站点包路径
        import site
        user_site = site.getusersitepackages()
        if user_site and user_site not in python_path:
            if python_path:
                python_path = f"{user_site}:{python_path}"
            else:
                python_path = user_site
        
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": sandbox_dir,
            "TMPDIR": sandbox_dir,
            "PYTHONNOUSERSITE": "0",  # 允许使用用户站点包
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": python_path,
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "NO_PROXY": "*",
        }
        try:
            completed = subprocess.run(
                # 移除 -I 选项，允许 Python 加载所有站点包
                [sys.executable, "-c", _COMPILE_WORKER_SOURCE],
                input=payload,
                text=True,
                capture_output=True,
                cwd=sandbox_dir,
                env=env,
                timeout=timeout,
                preexec_fn=_compile_preexec if os.name == "posix" else None,
                check=False,
            )
        except subprocess.TimeoutExpired:
            timeout_message = f"compile timeout after {timeout}s"
            return (
                {
                    "ok": False,
                    "stdout": "",
                    "stderr": timeout_message,
                    "diagnostics": [
                        {
                            "line": 0,
                            "column": 0,
                            "level": "error",
                            "message": timeout_message,
                        }
                    ],
                },
                "internal_error",
            )
        except Exception as exc:
            message = f"compile worker crashed: {type(exc).__name__}: {exc}"
            return (
                {
                    "ok": False,
                    "stdout": "",
                    "stderr": message,
                    "diagnostics": [
                        {
                            "line": 0,
                            "column": 0,
                            "level": "error",
                            "message": message,
                        }
                    ],
                },
                "internal_error",
            )

    stdout_text = completed.stdout.strip()
    stderr_text = completed.stderr.strip()
    if completed.returncode != 0:
        message = stderr_text or f"compile worker exited with code {completed.returncode}"
        return (
            {
                "ok": False,
                "stdout": "",
                "stderr": message,
                "diagnostics": [
                    {
                        "line": 0,
                        "column": 0,
                        "level": "error",
                        "message": message,
                    }
                ],
            },
            "internal_error",
        )
    try:
        normalized = _normalize_compile_result(json.loads(stdout_text or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        message = f"invalid compile worker output: {exc}"
        return (
            {
                "ok": False,
                "stdout": "",
                "stderr": message,
                "diagnostics": [
                    {
                        "line": 0,
                        "column": 0,
                        "level": "error",
                        "message": message,
                    }
                ],
            },
            "internal_error",
        )

    if stderr_text:
        normalized["stderr"] = "\n".join(part for part in [normalized["stderr"], stderr_text] if part).strip()
    if normalized["ok"]:
        return normalized, "ok"
    if any(item.get("line", 0) > 0 for item in normalized["diagnostics"]):
        return normalized, "compile_error"
    return normalized, "internal_error"


def run_rqalpha(job_id: str, job_dir: Path) -> int:
    timeout = int(current_app.config.get("BACKTEST_TIMEOUT", 900))
    log_path = job_dir / "run.log"
    command = [
        *_resolve_rqalpha_command(),
        "run",
        "-f",
        "strategy.py",
        "--config",
        "config.yml",
    ]
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=str(job_dir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _register_running_process(job_id, proc)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if is_cancel_requested(job_id):
                    _terminate_process(proc)
                    return RQALPHA_CANCELLED_EXIT_CODE

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process(proc)
                    raise subprocess.TimeoutExpired(cmd="rqalpha run", timeout=timeout)

                try:
                    return proc.wait(timeout=min(0.5, remaining))
                except subprocess.TimeoutExpired:
                    continue
        finally:
            _unregister_running_process(job_id)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _normalize_notebook_date(raw_value: object, field_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"{field_name} is required, format must be YYYY-MM-DD or YYYYMMDD")
    value = raw_value.strip()
    for fmt in _NOTEBOOK_DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"{field_name} must be YYYY-MM-DD or YYYYMMDD")


def _normalize_output_root(raw_value: object | None) -> Path:
    if raw_value is None or str(raw_value).strip() == "":
        output_root = (_project_root() / _NOTEBOOK_DEFAULT_OUTPUT_ROOT).resolve()
    else:
        output_root = Path(str(raw_value)).expanduser()
        if not output_root.is_absolute():
            output_root = (_project_root() / output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    return output_root


def _resolve_bundle_path_from_params(params: dict) -> Path:
    bundle_value = params.get("bundle_path")
    if bundle_value is None or str(bundle_value).strip() == "":
        bundle_value = os.environ.get("RQALPHA_BUNDLE_PATH", "").strip()
    if not bundle_value:
        raise ValueError(
            "bundle_path is required. Pass params['bundle_path'] or set environment variable "
            "RQALPHA_BUNDLE_PATH=/abs/path/to/rqalpha_bundle"
        )

    bundle_path = Path(str(bundle_value)).expanduser()
    if not bundle_path.is_absolute():
        bundle_path = (Path.cwd() / bundle_path).resolve()
    if not bundle_path.exists():
        raise ValueError(f"bundle_path does not exist: {bundle_path}")
    if not bundle_path.is_dir():
        raise ValueError(f"bundle_path must be a directory: {bundle_path}")
    return bundle_path


def _build_research_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def _create_output_dir(output_root: Path) -> tuple[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        run_id = _build_research_run_id()
        output_dir = output_root / run_id
        try:
            output_dir.mkdir(parents=True, exist_ok=False)
            return run_id, output_dir
        except FileExistsError:
            continue
    raise RuntimeError("failed to allocate unique run_id after 20 attempts")


def _parse_notebook_params(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")

    strategy_value = params.get("strategy_path")
    if not isinstance(strategy_value, str) or not strategy_value.strip():
        raise ValueError("strategy_path is required")
    strategy_path = Path(strategy_value).expanduser()
    if not strategy_path.is_absolute():
        strategy_path = (Path.cwd() / strategy_path).resolve()
    if not strategy_path.exists():
        raise ValueError(f"strategy_path does not exist: {strategy_path}")
    if not strategy_path.is_file():
        raise ValueError(f"strategy_path must be a file: {strategy_path}")

    start_date = _normalize_notebook_date(params.get("start_date"), "start_date")
    end_date = _normalize_notebook_date(params.get("end_date"), "end_date")
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    frequency = str(params.get("frequency", _NOTEBOOK_DEFAULT_FREQUENCY) or "").strip()
    if not frequency:
        raise ValueError("frequency must be a non-empty string")

    init_cash_raw = params.get("init_cash", _NOTEBOOK_DEFAULT_INIT_CASH)
    if isinstance(init_cash_raw, bool):
        raise ValueError("init_cash must be a positive number")
    try:
        init_cash = float(init_cash_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("init_cash must be a positive number") from exc
    if init_cash <= 0:
        raise ValueError("init_cash must be a positive number")

    benchmark = str(params.get("benchmark", _NOTEBOOK_DEFAULT_BENCHMARK) or "").strip()
    if not benchmark:
        raise ValueError("benchmark must be a non-empty string")

    symbol_raw = params.get("symbol")
    symbol = None
    if symbol_raw is not None:
        symbol_text = str(symbol_raw).strip()
        if symbol_text:
            symbol = symbol_text

    output_root = _normalize_output_root(params.get("output_root"))
    bundle_path = _resolve_bundle_path_from_params(params)
    return {
        "strategy_path": strategy_path.resolve(),
        "start_date": start_date,
        "end_date": end_date,
        "frequency": frequency,
        "init_cash": init_cash,
        "benchmark": benchmark,
        "symbol": symbol,
        "output_root": output_root,
        "bundle_path": bundle_path.resolve(),
    }


def _build_research_config(
    *,
    strategy_path: Path,
    start_date: str,
    end_date: str,
    frequency: str,
    init_cash: float,
    benchmark: str,
    symbol: str | None,
    bundle_path: Path,
    output_dir: Path,
    data_source_type: str = 'hdf5',
) -> dict:
    result_pickle = (output_dir / "result.pkl").resolve()
    report_dir = (output_dir / "report").resolve()
    log_path = (output_dir / "backtest.log").resolve()
    progress_file = (output_dir / "progress.json").resolve()
    config: dict[str, object] = {
        "base": {
            "strategy_file": str(strategy_path.resolve()),
            "start_date": start_date,
            "end_date": end_date,
            "frequency": frequency,
            "accounts": {
                "stock": float(init_cash),
                "future": float(init_cash),
            },
            "benchmark": benchmark,
            "data_bundle_path": str(bundle_path.resolve()),
            "auto_update_bundle": False,
        },
        "extra": {
            "log_level": "info",
            "log_file": str(log_path),
            "context_vars": {
                "data_source_type": data_source_type,
            },
        },
        "mod": {
            "sys_analyser": {
                "enabled": True,
                "plot": False,
                "output_file": str(result_pickle),
                "report_save_path": str(report_dir),
                "benchmark": benchmark,
            },
            "sys_progress": {
                "enabled": True,
                "output_file": str(progress_file),
            }
        },
    }
    if symbol:
        extra_config = config.get("extra")
        if isinstance(extra_config, dict):
            extra_config["context_vars"] = {"symbol": symbol}
    return config


def _write_research_config_yaml(config: dict, output_dir: Path) -> Path:
    config_path = output_dir / "config.yml"
    try:
        import yaml  # type: ignore

        config_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    except Exception:
        config_text = json.dumps(config, ensure_ascii=False, indent=2)
    config_path.write_text(config_text, encoding="utf-8")
    return config_path


def _create_file_logger(run_id: str, log_path: Path) -> tuple[logging.Logger, logging.Handler]:
    logger_name = f"{_NOTEBOOK_LOGGER_NAME}.{run_id}"
    run_logger = logging.getLogger(logger_name)
    run_logger.setLevel(logging.INFO)
    run_logger.propagate = False
    for stale_handler in list(run_logger.handlers):
        run_logger.removeHandler(stale_handler)
        try:
            stale_handler.close()
        except Exception:
            pass

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    run_logger.addHandler(file_handler)
    return run_logger, file_handler


def _run_rqalpha_in_process(*, strategy_path: Path, config: dict, log_path: Path, data_source_type: str = 'hdf5') -> None:
    """在进程内运行 RQAlpha 回测
    
    Args:
        strategy_path: 策略文件路径
        config: RQAlpha 配置字典
        log_path: 日志文件路径
        data_source_type: 数据源类型，'hdf5' 或 'parquet'
    """
    try:
        import rqalpha
    except Exception as exc:
        raise RuntimeError("rqalpha is not installed or failed to import") from exc

    with log_path.open("a", encoding="utf-8") as log_file, redirect_stdout(log_file), redirect_stderr(log_file):
        try:
            # 如果使用 Parquet 数据源，需要注入自定义数据源
            if data_source_type == 'parquet':
                # 对于 Parquet 数据源，我们需要修改 RQAlpha 的数据加载逻辑
                # 这里采用一个简化的方法：使用 monkey patching
                
                from rqalpha.utils.config import parse_config
                from rqalpha import main as rqalpha_main
                from app.backtest.services.parquet_data_source import ParquetDataSource
                
                # 保存原始的 BaseDataSource
                from rqalpha.data.base_data_source import BaseDataSource
                original_base_data_source = BaseDataSource
                
                # 临时替换 BaseDataSource
                import rqalpha.data.base_data_source as base_data_source_module
                base_data_source_module.BaseDataSource = ParquetDataSource
                
                # 同时替换 rqalpha.data 模块中的 BaseDataSource
                import rqalpha.data as data_module
                if hasattr(data_module, 'BaseDataSource'):
                    original_data_base_data_source = data_module.BaseDataSource
                    data_module.BaseDataSource = ParquetDataSource
                
                # 最重要：替换 rqalpha.main 模块中的 BaseDataSource
                # 这是关键！因为 main.run 函数直接使用了 BaseDataSource
                if 'BaseDataSource' in rqalpha_main.__dict__:
                    original_main_base_data_source = rqalpha_main.__dict__['BaseDataSource']
                    print(f"[DEBUG] 找到 rqalpha.main.BaseDataSource: {original_main_base_data_source}")
                else:
                    original_main_base_data_source = None
                    print(f"[DEBUG] 未找到 rqalpha.main.BaseDataSource")
                
                print(f"[DEBUG] 替换为 ParquetDataSource: {ParquetDataSource}")
                rqalpha_main.__dict__['BaseDataSource'] = ParquetDataSource
                
                # 验证替换是否成功
                print(f"[DEBUG] 替换后的 rqalpha.main.BaseDataSource: {rqalpha_main.__dict__.get('BaseDataSource')}")
                
                try:
                    # 运行回测
                    rqalpha.run_file(str(strategy_path), config=config)
                finally:
                    # 恢复原始的 BaseDataSource
                    base_data_source_module.BaseDataSource = original_base_data_source
                    if 'original_data_base_data_source' in locals():
                        data_module.BaseDataSource = original_data_base_data_source
                    if original_main_base_data_source is not None:
                        rqalpha_main.__dict__['BaseDataSource'] = original_main_base_data_source
            else:
                # 使用默认的 HDF5 数据源
                rqalpha.run_file(str(strategy_path), config=config)
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
            raise RuntimeError(f"rqalpha exited unexpectedly with code {exit_code}") from exc


def _json_default_for_notebook(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _read_summary_from_result_pickle(result_pickle_path: Path) -> dict:
    import pickle

    with result_pickle_path.open("rb") as f:
        payload = pickle.load(f)
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return {}
    return summary


def _resolve_positions_path(output_dir: Path) -> Path | None:
    report_dir = output_dir / "report"
    candidates = [
        report_dir / "stock_positions.csv",
        report_dir / "future_positions.csv",
        report_dir / "positions_weight.csv",
    ]
    for source_path in candidates:
        if not source_path.exists():
            continue
        target_path = output_dir / "positions.csv"
        shutil.copy2(source_path, target_path)
        return target_path
    return None


def run_backtest(params: dict) -> dict:
    """
    运行一次回测并把所有输出写到 output_dir。
    返回信息至少包含：
    run_id, output_dir,
    metrics_path, nav_path, trades_path,
    positions_path(optional), summary_path(optional), log_path(optional)
    """

    normalized = _parse_notebook_params(params)
    run_id, output_dir = _create_output_dir(normalized["output_root"])
    log_path = output_dir / "backtest.log"
    run_logger, file_handler = _create_file_logger(run_id, log_path)
    run_logger.info("backtest run started: run_id=%s", run_id)
    
    # 获取数据源类型（从环境变量或参数）
    data_source_type = params.get('data_source_type') or os.environ.get('MARKET_DATA_STORAGE_TYPE', 'hdf5')
    run_logger.info("data source type: %s", data_source_type)

    try:
        config = _build_research_config(
            strategy_path=normalized["strategy_path"],
            start_date=normalized["start_date"],
            end_date=normalized["end_date"],
            frequency=normalized["frequency"],
            init_cash=normalized["init_cash"],
            benchmark=normalized["benchmark"],
            symbol=normalized["symbol"],
            bundle_path=normalized["bundle_path"],
            output_dir=output_dir,
            data_source_type=data_source_type,
        )
        config_path = _write_research_config_yaml(config, output_dir)

        _run_rqalpha_in_process(
            strategy_path=normalized["strategy_path"],
            config=config,
            log_path=log_path,
            data_source_type=data_source_type,
        )

        result_pickle_path = output_dir / "result.pkl"
        if not result_pickle_path.exists():
            raise RuntimeError(
                "result.pkl not found after run. Please check sys_analyser.output_file and backtest.log"
            )

        from app.backtest.services.extractor import load_results

        metrics_df, nav_df, trades_df = load_results(output_dir)
        metrics_path = output_dir / "metrics.csv"
        nav_path = output_dir / "nav.csv"
        trades_path = output_dir / "trades.csv"
        metrics_df.to_csv(metrics_path, index=False, encoding="utf-8-sig")
        nav_df.to_csv(nav_path, index=False, encoding="utf-8-sig")
        trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")

        summary = _read_summary_from_result_pickle(result_pickle_path)
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default_for_notebook),
            encoding="utf-8",
        )

        positions_path = _resolve_positions_path(output_dir)
        run_logger.info("backtest run finished: run_id=%s output_dir=%s", run_id, output_dir)

        payload: dict[str, str] = {
            "run_id": run_id,
            "output_dir": str(output_dir),
            "metrics_path": str(metrics_path),
            "nav_path": str(nav_path),
            "trades_path": str(trades_path),
            "summary_path": str(summary_path),
            "log_path": str(log_path),
            "result_pickle_path": str(result_pickle_path),
            "config_path": str(config_path),
        }
        if positions_path is not None:
            payload["positions_path"] = str(positions_path)
        return payload
    except Exception:
        run_logger.exception("backtest run failed: run_id=%s", run_id)
        raise
    finally:
        run_logger.removeHandler(file_handler)
        try:
            file_handler.close()
        except Exception:
            pass


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a notebook-oriented RQAlpha backtest in-process.")
    parser.add_argument("--strategy", required=True, help="strategy file path")
    parser.add_argument("--start", required=True, help="start date, format YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--end", required=True, help="end date, format YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--frequency", default=_NOTEBOOK_DEFAULT_FREQUENCY, help="backtest frequency")
    parser.add_argument("--init-cash", type=float, default=float(_NOTEBOOK_DEFAULT_INIT_CASH), help="initial cash")
    parser.add_argument("--benchmark", default=_NOTEBOOK_DEFAULT_BENCHMARK, help="benchmark symbol")
    parser.add_argument("--symbol", default=None, help="optional context symbol")
    parser.add_argument("--output-root", default=None, help="output root directory")
    parser.add_argument("--bundle-path", default=None, help="rqalpha bundle path")
    return parser.parse_args(argv)


def _cli_main(argv: list[str] | None = None) -> int:
    args = _parse_cli_args(argv)
    params = {
        "strategy_path": args.strategy,
        "start_date": args.start,
        "end_date": args.end,
        "frequency": args.frequency,
        "init_cash": args.init_cash,
        "benchmark": args.benchmark,
        "symbol": args.symbol,
        "output_root": args.output_root,
        "bundle_path": args.bundle_path,
    }
    try:
        result = run_backtest(params)
    except Exception as exc:
        print(f"run_backtest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
