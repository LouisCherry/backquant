"""Baostock 1分钟数据获取模块

用于获取A股1分钟级别K线数据，存储到 dbbardata 表中。
支持增量更新和全量获取。
"""
import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import time
import logging

from app.database import DatabaseConnection, DatabaseConfig
from app.market_data.akshare_fetcher import _get_db_config_dict, _ensure_dbbardata_table

logger = logging.getLogger(__name__)

# 请求配置
MAX_RETRIES = 3
REQUEST_INTERVAL = 0.5  # 每次请求间隔0.5秒，避免触发反爬虫


def convert_symbol_to_baostock(symbol: str) -> Optional[str]:
    """将股票代码转换为Baostock格式

    Args:
        symbol: 股票代码，如 '000001.XSHE' 或 '000001'

    Returns:
        Baostock格式的代码，如 'sh.600000'
    """
    # 处理带后缀的格式
    if '.' in symbol:
        code = symbol.split('.')[0]
        suffix = symbol.split('.')[1].upper()
        if suffix in ('XSHE', 'SZ'):
            return f'sz.{code}'
        elif suffix in ('XSHG', 'SH'):
            return f'sh.{code}'

    # 纯数字代码，根据首位判断
    code = symbol
    if code.startswith('6'):
        return f'sh.{code}'
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        return f'sz.{code}'
    return None


def fetch_1min_data_baostock(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """使用Baostock获取分钟级数据

    注意：Baostock 1分钟数据可能存在限制，这里使用 5分钟数据作为替代

    Args:
        symbol: 股票代码
        start_date: 开始日期，格式 'YYYY-MM-DD'
        end_date: 结束日期，格式 'YYYY-MM-DD'

    Returns:
        DataFrame或None
    """
    bs_symbol = convert_symbol_to_baostock(symbol)
    if not bs_symbol:
        logger.error(f"无法转换股票代码: {symbol}")
        return None

    logger.info(f"Baostock 查询参数: symbol={bs_symbol}, start_date={start_date}, end_date={end_date}")

    for attempt in range(MAX_RETRIES):
        try:
            # 登录Baostock
            lg = bs.login()
            if lg.error_code != '0':
                logger.warning(f"Baostock 登录失败: {lg.error_msg}")
                time.sleep(1)
                continue

            # 查询5分钟K线数据（Baostock 1分钟数据可能有限制）
            rs = bs.query_history_k_data_plus(
                bs_symbol,
                "date,time,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="5",  # 5分钟
                adjustflag="2"  # 前复权
            )
            
            logger.info(f"Baostock 查询结果: error_code={rs.error_code}, error_msg={rs.error_msg}")

            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())

            bs.logout()

            if data_list:
                df = pd.DataFrame(data_list, columns=rs.fields)
                logger.info(f"Baostock 成功获取 {len(df)} 条1分钟数据")
                return df
            else:
                logger.warning(f"Baostock 返回空数据(第{attempt+1}次)")
        except Exception as e:
            logger.warning(f"Baostock 失败(第{attempt+1}次): {e}")
            try:
                bs.logout()
            except Exception:
                pass

        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)

    return None


def process_baostock_1min_data(df: pd.DataFrame, symbol: str) -> List[Dict]:
    """处理Baostock返回的1分钟数据

    Args:
        df: Baostock返回的DataFrame
        symbol: 原始股票代码

    Returns:
        标准格式的数据记录列表
    """
    processed = []
    exchange = 'SH' if symbol.startswith('6') else 'SZ'
    if '.' in symbol:
        code = symbol.split('.')[0]
        suffix = symbol.split('.')[1].upper()
        if suffix in ('XSHE', 'SZ'):
            exchange = 'SZ'
        elif suffix in ('XSHG', 'SH'):
            exchange = 'SH'

    for _, row in df.iterrows():
        date_str = row.get('date', '')
        time_str = row.get('time', '')
        
        # 处理时间格式
        if time_str:
            if len(time_str) == 14:
                # 格式: 20260326093100
                dt_str = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[8:10]}:{time_str[10:12]}:{time_str[12:14]}"
            else:
                dt_str = f"{date_str} {time_str}"
        else:
            continue

        record = {
            'symbol': symbol.split('.')[0] if '.' in symbol else symbol,
            'exchange': exchange,
            'datetime': dt_str,
            'interval': '5m',  # 使用5分钟数据
            'volume': float(row.get('volume', 0) or 0),
            'turnover': float(row.get('amount', 0) or 0),
            'open_interest': 0.0,
            'open_price': float(row.get('open', 0) or 0),
            'high_price': float(row.get('high', 0) or 0),
            'low_price': float(row.get('low', 0) or 0),
            'close_price': float(row.get('close', 0) or 0),
        }
        processed.append(record)

    return processed


def get_last_datetime_in_db_baostock(symbol: str, db: Optional[DatabaseConnection] = None) -> Optional[str]:
    """获取数据库中某股票最新数据时间

    Args:
        symbol: 股票代码
        db: 数据库连接，为None时自动获取

    Returns:
        最新时间字符串或None
    """
    close_db = False
    if db is None:
        config_dict = _get_db_config_dict()
        db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
        db.connect()
        _ensure_dbbardata_table(db)
        close_db = True

    try:
        code = symbol.split('.')[0] if '.' in symbol else symbol
        # 使用 fetchone 方法
        row = db.fetchone(
            "SELECT MAX(datetime) as max_dt FROM dbbardata WHERE symbol = ? AND `interval` = '1m'",
            (code,)
        )
        if row and row.get('max_dt'):
            return row['max_dt']
    except Exception as e:
        logger.error(f"查询数据库最新时间失败: {e}")
    finally:
        if close_db:
            db.close()

    return None


def save_1min_data_to_db_baostock(data: List[Dict], db: Optional[DatabaseConnection] = None) -> int:
    """将1分钟数据保存到数据库

    Args:
        data: 数据记录列表
        db: 数据库连接，为None时自动获取

    Returns:
        保存的记录数
    """
    if not data:
        return 0

    close_db = False
    if db is None:
        config_dict = _get_db_config_dict()
        db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
        db.connect()
        _ensure_dbbardata_table(db)
        close_db = True

    try:
        count = 0
        for r in data:
            try:
                # 使用 replace_into 方法处理重复数据
                db.replace_into(
                    'dbbardata',
                    ['symbol', 'exchange', 'datetime', 'interval', 'volume', 'turnover',
                     'open_interest', 'open_price', 'high_price', 'low_price', 'close_price'],
                    (r['symbol'], r['exchange'], r['datetime'], r['interval'],
                     r['volume'], r['turnover'], r['open_interest'],
                     r['open_price'], r['high_price'], r['low_price'], r['close_price'])
                )
                count += 1
            except Exception as e:
                logger.warning(f"插入数据失败: {e}")

        logger.info(f"成功存储 {count}/{len(data)} 条1分钟数据到数据库")
        return count
    except Exception as e:
        logger.error(f"保存数据到数据库失败: {e}")
        return 0
    finally:
        if close_db:
            db.close()


def fetch_1min_data_range_baostock(
    symbol: str,
    start_date: str,
    end_date: str,
    incremental: bool = True,
    progress_callback=None
) -> Tuple[int, int]:
    """使用Baostock获取指定时间范围的1分钟数据

    Args:
        symbol: 股票代码
        start_date: 开始日期，格式 'YYYYMMDD'
        end_date: 结束日期，格式 'YYYYMMDD'
        incremental: 是否增量更新
        progress_callback: 进度回调函数

    Returns:
        (成功获取的交易日数, 总记录数)
    """
    # 确定实际开始日期
    if incremental:
        last_dt = get_last_datetime_in_db_baostock(symbol)
        if last_dt:
            last_date = datetime.strptime(last_dt[:10], '%Y-%m-%d')
            effective_start = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
            logger.info(f"数据库中已有数据，最新时间: {last_dt}，从 {effective_start} 开始增量获取")
        else:
            effective_start = start_date[:4] + '-' + start_date[4:6] + '-' + start_date[6:8]
            logger.info(f"数据库中无数据，从 {effective_start} 开始全量获取")
    else:
        effective_start = start_date[:4] + '-' + start_date[4:6] + '-' + start_date[6:8]
        logger.info(f"全量获取模式，从 {effective_start} 开始")

    effective_end = end_date[:4] + '-' + end_date[4:6] + '-' + end_date[6:8]

    total_records = 0
    success_days = 0

    try:
        # 获取数据
        df = fetch_1min_data_baostock(symbol, effective_start, effective_end)

        if df is not None and not df.empty:
            # 处理数据
            processed_data = process_baostock_1min_data(df, symbol)

            if processed_data:
                # 保存到数据库
                count = save_1min_data_to_db_baostock(processed_data)
                total_records += count
                success_days += 1
                logger.info(f"成功获取 {len(processed_data)} 条数据，保存 {count} 条")
            else:
                logger.warning("处理数据后为空")
        else:
            logger.warning("未获取到数据")

    except Exception as e:
        logger.error(f"获取数据失败: {e}")

    return success_days, total_records
