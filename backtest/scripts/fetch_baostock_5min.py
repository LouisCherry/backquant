#!/usr/bin/env python3
"""Baostock 5分钟数据获取脚本

用于获取A股5分钟级别K线数据并存储到数据库。

用法示例:
    # 获取单只股票近两年5分钟数据
    python scripts/fetch_baostock_5min.py --symbol 000001.XSHE

    # 获取指定日期范围
    python scripts/fetch_baostock_5min.py --symbol 000001.XSHE --start 20240401 --end 20260409

    # 全量更新（覆盖已有数据）
    python scripts/fetch_baostock_5min.py --symbol 000001.XSHE --full

    # 查看统计信息
    python scripts/fetch_baostock_5min.py --stats

    # 查看某只股票的统计
    python scripts/fetch_baostock_5min.py --stats --symbol 000001.XSHE
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import baostock as bs
import pandas as pd
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from app.database import DatabaseConnection, DatabaseConfig
from app.market_data.akshare_fetcher import _get_db_config_dict, _ensure_dbbardata_table

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def convert_symbol_to_baostock(symbol: str) -> Optional[str]:
    """将股票代码转换为Baostock格式

    000001.XSHE -> sz.000001
    600000.XSHG -> sh.600000
    000001 -> sz.000001
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


def get_exchange_from_symbol(symbol: str) -> str:
    if '.' in symbol:
        suffix = symbol.split('.')[1].upper()
        if suffix == 'XSHE':
            return 'SZ'
        elif suffix == 'XSHG':
            return 'SH'
    code = symbol.split('.')[0] if '.' in symbol else symbol
    if code.startswith('6'):
        return 'SH'
    elif code.startswith('4') or code.startswith('8'):
        return 'BJ'
    return 'SZ'


def fetch_5min_data(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """使用Baostock获取5分钟数据

    Args:
        symbol: 股票代码（原始格式，如 000001.XSHE）
        start_date: 开始日期，格式 'YYYY-MM-DD'
        end_date: 结束日期，格式 'YYYY-MM-DD'

    Returns:
        DataFrame或None
    """
    bs_symbol = convert_symbol_to_baostock(symbol)
    if not bs_symbol:
        logger.error(f"无法转换股票代码: {symbol}")
        return None

    logger.info(f"Baostock 查询: symbol={bs_symbol}, start={start_date}, end={end_date}")

    for attempt in range(MAX_RETRIES):
        try:
            lg = bs.login()
            if lg.error_code != '0':
                logger.warning(f"Baostock 登录失败: {lg.error_msg}")
                time.sleep(1)
                continue

            rs = bs.query_history_k_data_plus(
                bs_symbol,
                "date,time,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="5",
                adjustflag="2"
            )

            logger.info(f"Baostock 查询结果: {rs.error_code} {rs.error_msg}")

            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())

            bs.logout()

            if data_list:
                df = pd.DataFrame(data_list, columns=rs.fields)
                logger.info(f"成功获取 {len(df)} 条5分钟数据")
                return df
            else:
                logger.warning(f"返回空数据(第{attempt+1}次)")
        except Exception as e:
            logger.warning(f"获取失败(第{attempt+1}次): {e}")
            try:
                bs.logout()
            except Exception:
                pass

        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)

    return None


def process_5min_data(df: pd.DataFrame, symbol: str) -> List[Dict]:
    """处理Baostock返回的5分钟数据，转换为标准格式"""
    processed = []
    exchange = get_exchange_from_symbol(symbol)

    for _, row in df.iterrows():
        date_str = row.get('date', '')
        time_str = row.get('time', '')

        if not time_str:
            continue

        # Baostock time格式: 20240401093000 -> 2024-04-01 09:30:00
        if len(time_str) == 14:
            dt_str = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[8:10]}:{time_str[10:12]}:{time_str[12:14]}"
        else:
            dt_str = f"{date_str} {time_str}"

        record = {
            'symbol': symbol.split('.')[0] if '.' in symbol else symbol,
            'exchange': exchange,
            'datetime': dt_str,
            'interval': '5m',
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


def get_last_datetime_in_db(symbol: str) -> Optional[str]:
    """获取数据库中某股票最新5分钟数据时间"""
    config_dict = _get_db_config_dict()
    db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
    db.connect()
    _ensure_dbbardata_table(db)

    try:
        code = symbol.split('.')[0] if '.' in symbol else symbol
        row = db.fetchone(
            "SELECT MAX(datetime) as max_dt FROM dbbardata WHERE symbol = ? AND `interval` = '5m'",
            (code,)
        )
        if row and row.get('max_dt'):
            return row['max_dt']
    except Exception as e:
        logger.error(f"查询数据库最新时间失败: {e}")
    finally:
        db.close()

    return None


def save_5min_data_to_db(data: List[Dict]) -> int:
    """将5分钟数据保存到数据库"""
    if not data:
        return 0

    config_dict = _get_db_config_dict()
    db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
    db.connect()
    _ensure_dbbardata_table(db)

    try:
        count = 0
        for r in data:
            try:
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

        logger.info(f"成功存储 {count}/{len(data)} 条5分钟数据到数据库")
        return count
    except Exception as e:
        logger.error(f"保存数据到数据库失败: {e}")
        return 0
    finally:
        db.close()


def fetch_5min_data_range(
    symbol: str,
    start_date: str,
    end_date: str,
    incremental: bool = True,
    progress_callback=None
) -> Tuple[int, int]:
    """获取指定时间范围的5分钟数据

    Baostock 分钟级数据需要按月查询，这里按月分批获取。

    Args:
        symbol: 股票代码
        start_date: 开始日期，格式 'YYYYMMDD'
        end_date: 结束日期，格式 'YYYYMMDD'
        incremental: 是否增量更新
        progress_callback: 进度回调

    Returns:
        (成功月数, 总记录数)
    """
    # 确定实际开始日期
    if incremental:
        last_dt = get_last_datetime_in_db(symbol)
        if last_dt:
            last_date = datetime.strptime(last_dt[:10], '%Y-%m-%d')
            effective_start = (last_date + timedelta(days=1))
            logger.info(f"数据库中已有数据，最新时间: {last_dt}，从 {effective_start.strftime('%Y-%m-%d')} 开始增量获取")
        else:
            effective_start = datetime.strptime(start_date, '%Y%m%d')
            logger.info(f"数据库中无数据，从 {effective_start.strftime('%Y-%m-%d')} 开始全量获取")
    else:
        effective_start = datetime.strptime(start_date, '%Y%m%d')
        logger.info(f"全量获取模式，从 {effective_start.strftime('%Y-%m-%d')} 开始")

    effective_end = datetime.strptime(end_date, '%Y%m%d')

    # 按月分批获取
    total_records = 0
    success_months = 0
    current = effective_start

    months = []
    while current <= effective_end:
        month_start = current.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = min(next_month - timedelta(days=1), effective_end)

        months.append((
            max(month_start, effective_start).strftime('%Y-%m-%d'),
            month_end.strftime('%Y-%m-%d')
        ))

        current = next_month

    logger.info(f"共 {len(months)} 个月需要获取")

    for i, (m_start, m_end) in enumerate(months):
        if progress_callback:
            progress_callback(i + 1, len(months), f"正在获取 {m_start} ~ {m_end} 的数据...")

        logger.info(f"[{i+1}/{len(months)}] 获取 {symbol} {m_start} ~ {m_end} 的5分钟数据")

        df = fetch_5min_data(symbol, m_start, m_end)

        if df is not None and not df.empty:
            processed_data = process_5min_data(df, symbol)

            if processed_data:
                count = save_5min_data_to_db(processed_data)
                total_records += count
                success_months += 1
                logger.info(f"  成功获取 {len(processed_data)} 条数据，保存 {count} 条")
            else:
                logger.warning(f"  处理数据后为空")
        else:
            logger.warning(f"  未获取到数据")

        time.sleep(0.5)

    logger.info(f"完成! 成功获取 {success_months}/{len(months)} 个月，共 {total_records} 条记录")
    return success_months, total_records


def get_5min_data_stats(symbol: Optional[str] = None) -> Dict:
    """获取5分钟数据统计信息"""
    config_dict = _get_db_config_dict()
    db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
    db.connect()
    _ensure_dbbardata_table(db)

    try:
        if symbol:
            code = symbol.split('.')[0] if '.' in symbol else symbol
            row = db.fetchone(
                "SELECT COUNT(*) as cnt, MIN(datetime) as min_dt, MAX(datetime) as max_dt FROM dbbardata WHERE symbol = ? AND `interval` = '5m'",
                (code,)
            )
            if row:
                return {
                    'symbol': symbol,
                    'count': row.get('cnt', 0) or 0,
                    'min_datetime': row.get('min_dt'),
                    'max_datetime': row.get('max_dt'),
                }
        else:
            rows = db.fetchall(
                "SELECT symbol, COUNT(*) as cnt, MIN(datetime) as min_dt, MAX(datetime) as max_dt FROM dbbardata WHERE `interval` = '5m' GROUP BY symbol"
            )
            stats = []
            for row in rows:
                stats.append({
                    'symbol': row.get('symbol'),
                    'count': row.get('cnt', 0) or 0,
                    'min_datetime': row.get('min_dt'),
                    'max_datetime': row.get('max_dt'),
                })
            return {'symbols': stats, 'total_symbols': len(stats)}
    except Exception as e:
        logger.error(f"获取统计数据失败: {e}")
        return {}
    finally:
        db.close()

    return {}


def print_progress(current, total, message):
    percent = (current / total) * 100 if total > 0 else 0
    print(f"\r[{current}/{total}] {percent:.1f}% - {message}", end='', flush=True)


def main():
    parser = argparse.ArgumentParser(
        description='获取A股5分钟K线数据（Baostock）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取平安银行近两年5分钟数据
  python scripts/fetch_baostock_5min.py --symbol 000001.XSHE

  # 获取指定日期范围
  python scripts/fetch_baostock_5min.py --symbol 000001.XSHE --start 20240401 --end 20260409

  # 全量更新
  python scripts/fetch_baostock_5min.py --symbol 000001.XSHE --full

  # 查看统计信息
  python scripts/fetch_baostock_5min.py --stats
        """
    )

    parser.add_argument('--symbol', '-s', type=str,
                        help='股票代码，如 000001.XSHE 或 000001')
    parser.add_argument('--start', type=str,
                        help='开始日期，格式 YYYYMMDD，默认为两年前')
    parser.add_argument('--end', type=str,
                        help='结束日期，格式 YYYYMMDD，默认为今天')
    parser.add_argument('--full', action='store_true',
                        help='全量更新，不跳过已有数据')
    parser.add_argument('--stats', action='store_true',
                        help='查看统计信息')

    args = parser.parse_args()

    # 查看统计信息
    if args.stats:
        if args.symbol:
            stats = get_5min_data_stats(args.symbol)
            if stats:
                print(f"\n股票 {args.symbol} 的5分钟数据统计:")
                print(f"  记录数: {stats.get('count', 0)}")
                print(f"  最早时间: {stats.get('min_datetime', 'N/A')}")
                print(f"  最晚时间: {stats.get('max_datetime', 'N/A')}")
            else:
                print(f"\n股票 {args.symbol} 暂无5分钟数据")
        else:
            stats = get_5min_data_stats()
            symbols = stats.get('symbols', [])
            print(f"\n5分钟数据统计:")
            print(f"  股票数量: {stats.get('total_symbols', 0)}")
            if symbols:
                print("\n  各股票数据情况:")
                for s in symbols:
                    print(f"    {s['symbol']}: {s['count']} 条 ({s['min_datetime']} ~ {s['max_datetime']})")
        return

    # 获取数据
    if not args.symbol:
        parser.print_help()
        print("\n错误: 必须指定 --symbol 参数")
        sys.exit(1)

    # 验证股票代码
    bs_symbol = convert_symbol_to_baostock(args.symbol)
    if not bs_symbol:
        print(f"错误: 无法识别的股票代码格式: {args.symbol}")
        print("支持的格式: 000001.XSHE, 000001, 600000.XSHG, 600000 等")
        sys.exit(1)

    # 设置日期范围
    end_date = args.end or datetime.now().strftime('%Y%m%d')
    start_date = args.start or (datetime.now() - timedelta(days=365*2)).strftime('%Y%m%d')

    print(f"开始获取 {args.symbol} 的5分钟数据（Baostock）")
    print(f"Baostock代码: {bs_symbol}")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"更新模式: {'全量' if args.full else '增量'}")
    print("-" * 50)

    try:
        success_months, total_records = fetch_5min_data_range(
            symbol=args.symbol,
            start_date=start_date,
            end_date=end_date,
            incremental=not args.full,
            progress_callback=print_progress
        )

        print(f"\n{'-' * 50}")
        print(f"完成! 成功获取 {success_months} 个月，共 {total_records} 条记录")

    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
