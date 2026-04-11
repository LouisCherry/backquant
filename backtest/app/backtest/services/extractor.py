from __future__ import annotations

import json
import math
import pickle
import re
from pathlib import Path

import pandas as pd

_DATE_COLUMN_TOKENS = ("date", "time", "datetime", "timestamp")


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


def _json_default(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "tolist"):
        converted = value.tolist()
        if isinstance(converted, list):
            return converted
        if isinstance(converted, tuple):
            return list(converted)
    return []


def _extract_nav_from_table(table_obj) -> list:
    if not hasattr(table_obj, "columns"):
        return []
    if "unit_net_value" in table_obj.columns:
        return _as_list(table_obj["unit_net_value"])
    columns = list(table_obj.columns)
    if len(columns) == 1:
        return _as_list(table_obj[columns[0]])
    return []


def _extract_benchmark_nav(result_obj: dict) -> list:
    benchmark_portfolio = result_obj.get("benchmark_portfolio")
    if benchmark_portfolio is not None:
        nav = _extract_nav_from_table(benchmark_portfolio)
        if nav:
            return nav

    for key in ("benchmark_curve", "benchmark_equity"):
        candidate = result_obj.get(key)
        if candidate is None:
            continue

        if isinstance(candidate, dict):
            for nav_key in ("benchmark_nav", "nav", "unit_net_value", "values", "curve"):
                nav = _as_list(candidate.get(nav_key))
                if nav:
                    return nav
            continue

        nav = _extract_nav_from_table(candidate)
        if nav:
            return nav

        nav = _as_list(candidate)
        if nav:
            return nav
    return []


def _extract_equity(portfolio_df) -> dict | None:
    if not hasattr(portfolio_df, "index") or not hasattr(portfolio_df, "columns"):
        return {"dates": [], "nav": [], "returns": [], "benchmark_nav": []}
    dates = [str(d.date()) if hasattr(d, "date") else str(d) for d in portfolio_df.index]
    # Try multiple column names for NAV (支持股票和期货)
    # 对期货：total_value是实际账户价值，优先级最高
    # 对股票：unit_net_value是单位净值，也支持
    nav = []
    for nav_col in ("total_value", "unit_net_value", "nav", "value", "portfolio_value"):
        if nav_col in portfolio_df.columns:
            nav = portfolio_df[nav_col].tolist()
            break
    returns = (
        portfolio_df["returns"].tolist()
        if "returns" in portfolio_df.columns
        else []
    )
    return {"dates": dates, "nav": nav, "returns": returns, "benchmark_nav": []}


def _extract_trades(trades_df) -> tuple[list, list]:
    if trades_df is None:
        return [], []

    if hasattr(trades_df, "to_dict"):
        records = trades_df.to_dict(orient="records")
        columns = []
        if hasattr(trades_df, "columns"):
            columns = [str(column) for column in list(trades_df.columns)]
        if not columns and records and isinstance(records[0], dict):
            columns = [str(key) for key in records[0].keys()]
        return records if isinstance(records, list) else [], columns

    if isinstance(trades_df, list):
        if trades_df and isinstance(trades_df[0], dict):
            columns = [str(key) for key in trades_df[0].keys()]
        else:
            columns = []
        return trades_df, columns

    return [], []


def _normalize_column_name(name: object) -> str:
    raw = str(name).strip().lower()
    normalized = re.sub(r"[^\w]+", "_", raw).strip("_")
    return normalized or "col"


def _normalize_columns(columns: list[object]) -> list[str]:
    normalized: list[str] = []
    used: dict[str, int] = {}
    for col in columns:
        base = _normalize_column_name(col)
        suffix = used.get(base, 0)
        used[base] = suffix + 1
        if suffix == 0:
            normalized.append(base)
        else:
            normalized.append(f"{base}_{suffix}")
    return normalized


def _standardize_dataframe(
    df: pd.DataFrame,
    *,
    convert_dates: bool = True,
    convert_numeric_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    normalized = df.copy()
    normalized.columns = _normalize_columns(list(normalized.columns))
    if convert_dates:
        for col in normalized.columns:
            if any(token in col for token in _DATE_COLUMN_TOKENS):
                normalized[col] = pd.to_datetime(normalized[col], errors="coerce")
    for col in convert_numeric_columns:
        if col in normalized.columns:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    return normalized


def _table_like_to_frame(value) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()

    if hasattr(value, "columns") and hasattr(value, "__getitem__"):
        data: dict[str, list] = {}
        for col in list(getattr(value, "columns", [])):
            try:
                data[str(col)] = _as_list(value[col])
            except Exception:
                continue
        if not data:
            return pd.DataFrame()
        frame = pd.DataFrame(data)
        if hasattr(value, "index"):
            index_values = _as_list(getattr(value, "index"))
            if len(index_values) == len(frame):
                frame.index = pd.Index(index_values, name="date")
        return frame

    if isinstance(value, list):
        return pd.DataFrame(value)
    if isinstance(value, dict):
        if not value:
            return pd.DataFrame()
        if all(isinstance(item, (list, tuple, pd.Series)) for item in value.values()):
            return pd.DataFrame(value)
        return pd.DataFrame([value])
    if hasattr(value, "to_dict"):
        try:
            records = value.to_dict(orient="records")
        except Exception:
            records = None
        if isinstance(records, list):
            return pd.DataFrame(records)
    return pd.DataFrame()


def _load_result_payload(result_pkl: Path) -> dict:
    with result_pkl.open("rb") as f:
        payload = pickle.load(f)
    if isinstance(payload, dict):
        return payload
    raise ValueError(f"result pickle payload must be dict: {result_pkl}")


def _build_metrics_df(result_payload: dict) -> pd.DataFrame:
    summary = result_payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    rows = [{"metric": str(key), "value": value} for key, value in summary.items()]
    return _standardize_dataframe(pd.DataFrame(rows, columns=["metric", "value"]), convert_dates=False)


def _build_nav_df(result_payload: dict) -> pd.DataFrame:
    portfolio_df = _table_like_to_frame(result_payload.get("portfolio"))
    nav_df = pd.DataFrame(columns=["date", "nav", "returns", "benchmark_nav"])

    if not portfolio_df.empty:
        working = portfolio_df.copy()
        if "date" in working.columns:
            dates = working["date"].tolist()
        else:
            dates = list(working.index)
        nav_df = pd.DataFrame({"date": dates})

        if "unit_net_value" in working.columns:
            nav_df["nav"] = working["unit_net_value"].tolist()
        elif "nav" in working.columns:
            nav_df["nav"] = working["nav"].tolist()
        elif len(working.columns) > 0:
            nav_df["nav"] = working.iloc[:, 0].tolist()
        else:
            nav_df["nav"] = []

        if "returns" in working.columns:
            nav_df["returns"] = working["returns"].tolist()
        else:
            nav_df["returns"] = [None] * len(nav_df)

    benchmark_nav = _extract_benchmark_nav(result_payload)
    if benchmark_nav:
        if nav_df.empty:
            nav_df = pd.DataFrame(
                {
                    "date": [None] * len(benchmark_nav),
                    "nav": [None] * len(benchmark_nav),
                    "returns": [None] * len(benchmark_nav),
                }
            )
        padded_benchmark_nav = list(benchmark_nav)[: len(nav_df)]
        if len(padded_benchmark_nav) < len(nav_df):
            padded_benchmark_nav.extend([None] * (len(nav_df) - len(padded_benchmark_nav)))
        nav_df["benchmark_nav"] = padded_benchmark_nav
    elif "benchmark_nav" not in nav_df.columns:
        nav_df["benchmark_nav"] = [None] * len(nav_df)

    return _standardize_dataframe(
        nav_df,
        convert_numeric_columns=("nav", "returns", "benchmark_nav"),
    )


def _build_trades_df(result_payload: dict) -> pd.DataFrame:
    trades_df = _table_like_to_frame(result_payload.get("trades"))
    return _standardize_dataframe(trades_df)


def extract_result(result_pkl: Path, out_json: Path) -> dict:
    with result_pkl.open("rb") as f:
        r = pickle.load(f)

    summary = r.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}

    # 支持多种portfolio字段名（股票、期货、混合）
    portfolio = None
    for portfolio_key in ("portfolio", "future_portfolio", "stock_portfolio", "portfolios"):
        if portfolio_key in r:
            portfolio = r[portfolio_key]
            break

    equity = _extract_equity(portfolio) if portfolio is not None else None
    benchmark_nav = _extract_benchmark_nav(r)
    if not isinstance(equity, dict):
        equity = {"dates": [], "nav": [], "returns": [], "benchmark_nav": []}
    equity["benchmark_nav"] = benchmark_nav if isinstance(benchmark_nav, list) else []
    trades, trade_columns = _extract_trades(r.get("trades")) if "trades" in r else ([], [])

    payload = _sanitize_nan({
        "summary": summary,
        "equity": equity,
        "trades": trades,
        "trade_columns": trade_columns,
        "raw_keys": sorted([str(key) for key in r.keys()]),
    })
    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    return payload


def load_results(output_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """返回 metrics_df, nav_df, trades_df；并做列名/日期类型标准化。"""
    resolved_output_dir = Path(output_dir).expanduser()
    if not resolved_output_dir.is_absolute():
        resolved_output_dir = (Path.cwd() / resolved_output_dir).resolve()
    if not resolved_output_dir.exists():
        raise FileNotFoundError(f"output_dir not found: {resolved_output_dir}")
    if not resolved_output_dir.is_dir():
        raise NotADirectoryError(f"output_dir must be a directory: {resolved_output_dir}")

    result_pkl = resolved_output_dir / "result.pkl"
    if result_pkl.exists():
        result_payload = _load_result_payload(result_pkl)
        metrics_df = _build_metrics_df(result_payload)
        nav_df = _build_nav_df(result_payload)
        trades_df = _build_trades_df(result_payload)
        return metrics_df, nav_df, trades_df

    metrics_path = resolved_output_dir / "metrics.csv"
    nav_path = resolved_output_dir / "nav.csv"
    trades_path = resolved_output_dir / "trades.csv"
    required_paths = [metrics_path, nav_path, trades_path]
    if not all(path.exists() for path in required_paths):
        raise FileNotFoundError(
            "missing result files under output_dir; expected result.pkl or metrics.csv/nav.csv/trades.csv"
        )

    metrics_df = pd.read_csv(metrics_path)
    nav_df = pd.read_csv(nav_path)
    trades_df = pd.read_csv(trades_path)
    return (
        _standardize_dataframe(metrics_df, convert_dates=False),
        _standardize_dataframe(nav_df, convert_numeric_columns=("nav", "returns", "benchmark_nav")),
        _standardize_dataframe(trades_df),
    )
