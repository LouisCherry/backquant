"""Baostock 数据获取模块

用于获取A股历史数据，支持：
1. 获取当前上市A股列表
2. 获取1分钟/5分钟级别K线数据
3. 存储到 Parquet 文件
"""
import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import time
import logging
import os
from pathlib import Path

from app.market_data.akshare_fetcher import _get_parquet_root
from app.market_data.data_writer import get_data_writer, DataWriterFactory
from app.utils.parquet_utils import read_parquet_safe, write_parquet_safe

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
REQUEST_INTERVAL = 0.1


def convert_symbol_to_baostock(symbol: str) -> Optional[str]:
    """将股票代码转换为Baostock格式
    
    Args:
        symbol: 股票代码，如 '000001.XSHE' 或 '000001'
        
    Returns:
        Baostock格式的代码，如 'sh.600000'
    """
    if '.' in symbol:
        code = symbol.split('.')[0]
        suffix = symbol.split('.')[1].upper()
        if suffix in ('XSHE', 'SZ'):
            return f'sz.{code}'
        elif suffix in ('XSHG', 'SH'):
            return f'sh.{code}'
    
    code = symbol
    if code.startswith('6'):
        return f'sh.{code}'
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        return f'sz.{code}'
    return None


def convert_baostock_to_symbol(bs_symbol: str) -> str:
    """将Baostock格式转换为标准格式
    
    Args:
        bs_symbol: Baostock格式，如 'sh.600000'
        
    Returns:
        标准格式，如 '600000.XSHG'
    """
    if '.' not in bs_symbol:
        return bs_symbol
    
    exchange, code = bs_symbol.split('.')
    if exchange.lower() == 'sh':
        return f"{code}.XSHG"
    elif exchange.lower() == 'sz':
        return f"{code}.XSHE"
    return bs_symbol


def get_all_stocks_baostock(
    filter_st: bool = True,
    filter_b_stock: bool = True
) -> List[Dict]:
    """获取当前上市A股列表
    
    Args:
        filter_st: 是否过滤ST、*ST股票（暂时不启用，因为 Baostock 不返回名称）
        filter_b_stock: 是否过滤B股
        
    Returns:
        股票列表，格式 [{'code': '000001', 'name': '', 'symbol': '000001.XSHE'}, ...]
    """
    logger.info("开始从 Baostock 获取股票列表...")
    
    lg = bs.login()
    if lg.error_code != '0':
        logger.error(f"Baostock 登录失败: {lg.error_msg}")
        return []
    
    try:
        # 使用昨天的日期，避免当天数据未更新
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        rs = bs.query_all_stock(day=yesterday)
        
        if rs.error_code != '0':
            logger.error(f"查询股票列表失败: {rs.error_msg}")
            return []
        
        stocks = []
        while rs.next():
            row = rs.get_row_data()
            code = row[0]
            name = row[1] if len(row) > 1 else ''
            
            if not code:
                continue
            
            # 只保留A股（上证6开头，深证0/3开头）
            # 过滤掉指数（sh.000xxx, sz.399xxx）
            if code.startswith('sh.6'):
                # 上证A股
                pass
            elif code.startswith('sz.0') or code.startswith('sz.3'):
                # 深证A股（主板和创业板）
                pass
            else:
                # 过滤掉其他（指数、B股等）
                continue
            
            # 过滤B股
            if filter_b_stock:
                if code.startswith('sh.9') or code.startswith('sz.2'):
                    continue
            
            symbol = convert_baostock_to_symbol(code)
            
            stocks.append({
                'code': code.split('.')[1],
                'name': name,
                'symbol': symbol,
                'bs_code': code
            })
        
        bs.logout()
        
        logger.info(f"成功获取 {len(stocks)} 只A股")
        return stocks
        
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        try:
            bs.logout()
        except Exception:
            pass
        return []


def fetch_minute_data_baostock(
    symbol: str,
    start_date: str,
    end_date: str,
    frequency: str = '5',
    adjustflag: str = '2'
) -> Optional[pd.DataFrame]:
    """使用Baostock获取分钟级数据
    
    Args:
        symbol: 股票代码（标准格式，如 '000001.XSHE'）
        start_date: 开始日期，格式 'YYYY-MM-DD'
        end_date: 结束日期，格式 'YYYY-MM-DD'
        frequency: 数据频率，'5' 表示5分钟，'15' 表示15分钟，'30' 表示30分钟，'60' 表示60分钟
        adjustflag: 复权类型，'2' 前复权，'1' 后复权，'3' 不复权
        
    Returns:
        DataFrame或None
    """
    bs_symbol = convert_symbol_to_baostock(symbol)
    if not bs_symbol:
        logger.error(f"无法转换股票代码: {symbol}")
        return None
    
    for attempt in range(MAX_RETRIES):
        try:
            lg = bs.login()
            if lg.error_code != '0':
                logger.warning(f"Baostock 登录失败(第{attempt+1}次): {lg.error_msg}")
                time.sleep(1)
                continue
            
            rs = bs.query_history_k_data_plus(
                bs_symbol,
                "date,time,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency=frequency,
                adjustflag=adjustflag
            )
            
            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())
            
            bs.logout()
            
            if data_list:
                df = pd.DataFrame(data_list, columns=rs.fields)
                logger.debug(f"Baostock 成功获取 {len(df)} 条{frequency}分钟数据")
                return df
            else:
                logger.debug(f"Baostock 返回空数据(第{attempt+1}次): {symbol}")
                return None
                
        except Exception as e:
            logger.warning(f"Baostock 失败(第{attempt+1}次): {symbol} - {e}")
            try:
                bs.logout()
            except Exception:
                pass
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    
    return None


def process_baostock_minute_data(
    df: pd.DataFrame,
    symbol: str,
    frequency: str = '5m'
) -> pd.DataFrame:
    """处理Baostock返回的分钟数据
    
    Args:
        df: Baostock返回的DataFrame
        symbol: 原始股票代码
        frequency: 数据频率标识
        
    Returns:
        处理后的DataFrame
    """
    if df is None or df.empty:
        return pd.DataFrame()
    
    exchange = 'SH' if symbol.startswith('6') else 'SZ'
    if '.' in symbol:
        suffix = symbol.split('.')[1].upper()
        if suffix in ('XSHE', 'SZ'):
            exchange = 'SZ'
        elif suffix in ('XSHG', 'SH'):
            exchange = 'SH'
    
    processed_data = []
    
    for _, row in df.iterrows():
        date_str = row.get('date', '')
        time_str = row.get('time', '')
        
        if not time_str:
            continue
        
        if len(time_str) == 14:
            dt_str = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[8:10]}:{time_str[10:12]}:{time_str[12:14]}"
        else:
            dt_str = f"{date_str} {time_str}"
        
        record = {
            'symbol': symbol.split('.')[0] if '.' in symbol else symbol,
            'exchange': exchange,
            'datetime': dt_str,
            'interval': frequency,
            'volume': float(row.get('volume', 0) or 0),
            'turnover': float(row.get('amount', 0) or 0),
            'open_interest': 0.0,
            'open_price': float(row.get('open', 0) or 0),
            'high_price': float(row.get('high', 0) or 0),
            'low_price': float(row.get('low', 0) or 0),
            'close_price': float(row.get('close', 0) or 0),
        }
        processed_data.append(record)
    
    return pd.DataFrame(processed_data)


def save_minute_data_to_parquet(
    df: pd.DataFrame,
    symbol: str,
    frequency: str = '5m',
    parquet_root: Optional[Path] = None
) -> Tuple[int, Path]:
    """将分钟数据保存到 Parquet 文件（使用数据写入器工厂）
    
    Args:
        df: 数据DataFrame
        symbol: 股票代码
        frequency: 数据频率
        parquet_root: Parquet 根目录（保留参数兼容性，实际使用配置）
        
    Returns:
        (保存的记录数, Parquet 文件路径)
    """
    if df is None or df.empty:
        return 0, Path('')
    
    # Use data writer factory for flexible output format
    writer = get_data_writer()
    
    # Try to read existing data for merge (if using parquet)
    try:
        existing_df = writer.read_minute_data(symbol, frequency)
        if existing_df is not None and not existing_df.empty:
            # Merge with existing data
            df_combined = pd.concat([existing_df, df], ignore_index=True)
            df_combined = df_combined.drop_duplicates(subset=['datetime'], keep='last')
            df_combined = df_combined.sort_values('datetime').reset_index(drop=True)
            df_to_save = df_combined
        else:
            df_to_save = df
    except Exception as e:
        logger.debug(f"读取已有数据失败（可能不存在），将保存新数据: {e}")
        df_to_save = df
    
    # Write using the data writer
    if writer.write_minute_data(df_to_save, symbol, frequency):
        count = len(df_to_save)
        logger.debug(f"成功存储 {len(df)} 条新数据，总计 {count} 条: {symbol}")
        
        # Return path for compatibility (get from config)
        if parquet_root is None:
            parquet_root = _get_parquet_root()
        code = symbol.split('.')[0] if '.' in symbol else symbol
        parquet_path = parquet_root / frequency / f'{code}.parquet'
        
        return count, parquet_path
    else:
        logger.error(f"保存数据失败: {symbol}")
        return 0, Path('')


def fetch_minute_data_range_baostock(
    symbol: str,
    start_date: str,
    end_date: str,
    frequency: str = '5',
    incremental: bool = True,
    storage_type: str = 'parquet'
) -> Tuple[int, int]:
    """使用Baostock获取指定时间范围的分钟数据
    
    Args:
        symbol: 股票代码
        start_date: 开始日期，格式 'YYYYMMDD'
        end_date: 结束日期，格式 'YYYYMMDD'
        frequency: 数据频率，'5' 表示5分钟
        incremental: 是否增量更新
        storage_type: 存储类型，'parquet' 或 'db'
        
    Returns:
        (成功获取的交易日数, 总记录数)
    """
    freq_map = {
        '5': '5m',
        '15': '15m',
        '30': '30m',
        '60': '60m'
    }
    freq_label = freq_map.get(frequency, '5m')
    
    effective_start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    effective_end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    
    if incremental and storage_type == 'parquet':
        # Use data writer to read existing data (supports both parquet and hdf5)
        try:
            writer = get_data_writer()
            df_existing = writer.read_minute_data(symbol, freq_label)
            if df_existing is not None and not df_existing.empty and 'datetime' in df_existing.columns:
                max_datetime = df_existing['datetime'].max()
                last_date = pd.to_datetime(max_datetime).date()
                effective_start = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
                logger.debug(f"{symbol}: 增量更新（从 {effective_start} 开始）")
        except Exception as e:
            logger.debug(f"读取已有数据失败（可能不存在）: {e}")
    
    try:
        df_raw = fetch_minute_data_baostock(
            symbol=symbol,
            start_date=effective_start,
            end_date=effective_end,
            frequency=frequency,
            adjustflag='2'
        )
        
        if df_raw is None or df_raw.empty:
            return 0, 0
        
        df_processed = process_baostock_minute_data(df_raw, symbol, freq_label)
        
        if df_processed.empty:
            return 0, 0
        
        if storage_type == 'parquet':
            count, _ = save_minute_data_to_parquet(df_processed, symbol, freq_label)
        else:
            count = len(df_processed)
        
        unique_dates = df_processed['datetime'].str[:10].nunique()
        
        return unique_dates, count
        
    except Exception as e:
        logger.error(f"获取数据失败: {symbol} - {e}")
        return 0, 0
