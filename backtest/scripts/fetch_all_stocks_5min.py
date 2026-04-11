#!/usr/bin/env python3
"""批量获取所有A股5分钟K线数据（Baostock优化版）

综合优化：
1. 复用Baostock连接：每个线程只登录一次，所有月份查询复用同一连接
2. 批量写入Parquet：使用 pandas DataFrame 和 write_parquet_safe 函数
3. 多进程支持：使用 multiprocessing 替代 threading，绕过GIL
4. 连接池：每个进程维护独立的Baostock连接

用法示例:
    # 获取所有A股近两年5分钟数据（默认8进程）
    python scripts/fetch_all_stocks_5min.py

    # 指定进程数
    python scripts/fetch_all_stocks_5min.py --workers 16

    # 指定日期范围
    python scripts/fetch_all_stocks_5min.py --start 20240409 --end 20260409

    # 全量更新
    python scripts/fetch_all_stocks_5min.py --full

    # 仅获取指定数量的股票（测试用）
    python scripts/fetch_all_stocks_5min.py --limit 10

    # 查看下载进度
    python scripts/fetch_all_stocks_5min.py --progress
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import baostock as bs
import h5py
import pickle
import time
import logging
import json
import multiprocessing
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

from app.utils.parquet_utils import write_parquet_safe

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3

PROGRESS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'backtest', 'fetch_5min_progress.json'
)


def get_stock_list() -> List[str]:
    """从项目数据中获取全部A股股票代码列表"""
    project_root = Path(__file__).resolve().parent.parent
    bundle_dir = project_root / 'data' / 'rqalpha' / 'bundle'

    stock_codes = []

    instruments_pk = bundle_dir / 'instruments.pk'
    if instruments_pk.exists():
        with open(instruments_pk, 'rb') as f:
            instruments = pickle.load(f)
        stock_codes = [
            item['order_book_id'] for item in instruments
            if item.get('order_book_id', '').endswith(('.XSHE', '.XSHG'))
            and item.get('instrument_type') == 'CS'
        ]
        logger.info(f"从 instruments.pk 获取到 {len(stock_codes)} 只A股")

    if not stock_codes:
        stocks_h5 = bundle_dir / 'stocks.h5'
        if stocks_h5.exists():
            with h5py.File(stocks_h5, 'r') as f:
                stock_codes = [k for k in f.keys() if k.endswith(('.XSHE', '.XSHG'))]
            logger.info(f"从 stocks.h5 获取到 {len(stock_codes)} 只A股")

    if not stock_codes:
        logger.error("无法获取股票列表，请检查 bundle 数据")

    return stock_codes


def convert_symbol_to_baostock(symbol: str) -> Optional[str]:
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


def _get_parquet_path(symbol: str) -> Path:
    """获取股票的Parquet文件路径"""
    project_root = Path(__file__).resolve().parent.parent
    parquet_dir = project_root / 'data' / 'parquet' / '5m'
    code = symbol.split('.')[0] if '.' in symbol else symbol
    return parquet_dir / f"{code}.parquet"


def load_progress() -> Dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'completed': [], 'failed': [], 'last_update': ''}


def save_progress(progress: Dict):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    progress['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def _parse_row(row_data: list, code: str, exchange: str) -> Optional[Dict]:
    """解析单行数据为字典格式"""
    time_str = row_data[1] if len(row_data) > 1 else ''
    if not time_str:
        return None

    if len(time_str) == 14:
        dt_str = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[8:10]}:{time_str[10:12]}:{time_str[12:14]}"
    else:
        dt_str = f"{row_data[0]} {time_str}"

    def safe_float(val, default=0.0):
        try:
            return float(val) if val else default
        except (ValueError, TypeError):
            return default

    return {
        'symbol': code,
        'exchange': exchange,
        'datetime': dt_str,
        'interval': '5m',
        'volume': safe_float(row_data[6] if len(row_data) > 6 else 0),
        'turnover': safe_float(row_data[7] if len(row_data) > 7 else 0),
        'open_interest': 0.0,
        'open_price': safe_float(row_data[2] if len(row_data) > 2 else 0),
        'high_price': safe_float(row_data[3] if len(row_data) > 3 else 0),
        'low_price': safe_float(row_data[4] if len(row_data) > 4 else 0),
        'close_price': safe_float(row_data[5] if len(row_data) > 5 else 0),
    }


def fetch_5min_single_stock(
    symbol: str,
    start_date: str,
    end_date: str,
    incremental: bool = True
) -> Tuple[str, bool, int, str]:
    """获取单只股票的5分钟数据（进程安全，复用连接，批量写入Parquet）

    优化点：
    1. Baostock 只登录一次，所有月份查询复用连接
    2. 批量写入Parquet：使用 pandas DataFrame 和 write_parquet_safe 函数
    3. 内存中累积所有月份的数据，最后一次写入

    Returns:
        (symbol, success, record_count, message)
    """
    bs_symbol = convert_symbol_to_baostock(symbol)
    if not bs_symbol:
        return symbol, False, 0, "无法转换股票代码"

    exchange = get_exchange_from_symbol(symbol)
    code = symbol.split('.')[0] if '.' in symbol else symbol
    parquet_path = _get_parquet_path(symbol)

    # 增量检查
    effective_start = start_date
    if incremental:
        try:
            if parquet_path.exists():
                df = pd.read_parquet(parquet_path)
                if not df.empty:
                    last_date = df['datetime'].max().strftime('%Y-%m-%d')
                    effective_start = (datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                    if effective_start > end_date:
                        return symbol, True, 0, "数据已是最新"
        except Exception:
            pass

    # 生成月份列表
    current = datetime.strptime(effective_start, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    months = []
    while current <= end_dt:
        month_start = current.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = min(next_month - timedelta(days=1), end_dt)
        months.append((
            max(month_start, current).strftime('%Y-%m-%d'),
            month_end.strftime('%Y-%m-%d')
        ))
        current = next_month

    if not months:
        return symbol, True, 0, "无需获取"

    # 复用 Baostock 连接：只登录一次
    all_records = []
    logged_in = False

    try:
        lg = bs.login()
        if lg.error_code != '0':
            return symbol, False, 0, f"Baostock登录失败: {lg.error_msg}"
        logged_in = True

        for m_start, m_end in months:
            success = False
            for attempt in range(MAX_RETRIES):
                try:
                    rs = bs.query_history_k_data_plus(
                        bs_symbol,
                        "date,time,open,high,low,close,volume,amount",
                        start_date=m_start,
                        end_date=m_end,
                        frequency="5",
                        adjustflag="2"
                    )

                    if rs.error_code != '0':
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(1)
                            continue
                        break

                    data_list = []
                    while rs.next():
                        data_list.append(rs.get_row_data())

                    for row_data in data_list:
                        record = _parse_row(row_data, code, exchange)
                        if record:
                            all_records.append(record)

                    success = True
                    break
                except Exception:
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(2 ** attempt)

            if not success and not all_records:
                # 如果第一个月就失败，可能股票代码无效
                pass

            time.sleep(0.1)

    except Exception as e:
        return symbol, False, 0, f"异常: {e}"
    finally:
        if logged_in:
            try:
                bs.logout()
            except Exception:
                pass

    # 批量写入Parquet
    if all_records:
        try:
            # 转换为DataFrame
            df = pd.DataFrame(all_records)
            
            # 如果文件已存在，先读取并合并数据
            if parquet_path.exists() and incremental:
                try:
                    existing_df = pd.read_parquet(parquet_path)
                    df = pd.concat([existing_df, df])
                    # 去重，保持唯一的(symbol, datetime, interval)组合
                    df = df.drop_duplicates(subset=['symbol', 'datetime', 'interval'])
                    # 按时间排序
                    df = df.sort_values('datetime')
                except Exception:
                    pass
            
            # 写入Parquet文件
            if write_parquet_safe(df, parquet_path):
                return symbol, True, len(all_records), f"获取 {len(all_records)} 条记录"
            else:
                return symbol, False, 0, "Parquet文件写入失败"
        except Exception as e:
            return symbol, False, 0, f"数据处理失败: {e}"

    return symbol, True, len(all_records), f"获取 {len(all_records)} 条记录"


def fetch_all_stocks(
    stock_list: List[str],
    start_date: str,
    end_date: str,
    incremental: bool = True,
    num_workers: int = 8,
    limit: int = 0
):
    """批量获取所有股票的5分钟数据（多进程版）

    Args:
        stock_list: 股票代码列表
        start_date: 开始日期 'YYYY-MM-DD'
        end_date: 结束日期 'YYYY-MM-DD'
        incremental: 是否增量更新
        num_workers: 进程数
        limit: 限制获取数量（0=不限制）
    """
    progress = load_progress()
    completed_set = set(progress.get('completed', []))

    if incremental:
        pending = [s for s in stock_list if s not in completed_set]
        logger.info(f"已完成: {len(completed_set)}, 待获取: {len(pending)}")
    else:
        pending = list(stock_list)
        progress['completed'] = []
        progress['failed'] = []

    if limit > 0:
        pending = pending[:limit]
        logger.info(f"限制获取数量: {limit}")

    if not pending:
        logger.info("所有股票数据已是最新，无需获取")
        return

    logger.info(f"开始获取 {len(pending)} 只股票的5分钟数据，进程数: {num_workers}")
    logger.info(f"日期范围: {start_date} ~ {end_date}")

    total_records = 0
    success_count = 0
    fail_count = 0
    start_time = time.time()

    # 使用 ProcessPoolExecutor 多进程并发
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(
                fetch_5min_single_stock,
                symbol, start_date, end_date, incremental
            ): symbol
            for symbol in pending
        }

        for future in as_completed(futures):
            symbol = futures[future]
            try:
                sym, success, count, msg = future.result()
                if success:
                    success_count += 1
                    total_records += count
                    progress['completed'].append(sym)
                    if sym in progress.get('failed', []):
                        progress['failed'].remove(sym)
                else:
                    fail_count += 1
                    if sym not in progress.get('failed', []):
                        progress['failed'].append(sym)
                    logger.warning(f"失败: {sym} - {msg}")
            except Exception as e:
                fail_count += 1
                if symbol not in progress.get('failed', []):
                    progress['failed'].append(symbol)
                logger.error(f"异常: {symbol} - {e}")

            # 定期保存进度（每10只保存一次）
            done_count = success_count + fail_count
            if done_count % 10 == 0:
                save_progress(progress)

            # 打印进度
            elapsed = time.time() - start_time
            speed = done_count / (elapsed / 60) if elapsed > 0 else 0
            eta = (len(pending) - done_count) / speed if speed > 0 else 0
            logger.info(
                f"进度: {done_count}/{len(pending)} "
                f"({done_count*100//len(pending)}%) | "
                f"成功: {success_count} 失败: {fail_count} | "
                f"总记录: {total_records} | "
                f"速度: {speed:.1f}只/分钟 | "
                f"预计剩余: {eta:.0f}分钟"
            )

    # 最终保存进度
    save_progress(progress)

    elapsed = time.time() - start_time
    logger.info(f"\n{'='*60}")
    logger.info(f"全部完成!")
    logger.info(f"  成功: {success_count}, 失败: {fail_count}")
    logger.info(f"  总记录数: {total_records}")
    logger.info(f"  耗时: {elapsed/60:.1f} 分钟")
    if progress.get('failed'):
        logger.info(f"  失败股票: {progress['failed'][:20]}{'...' if len(progress['failed']) > 20 else ''}")


def show_progress():
    """显示下载进度"""
    progress = load_progress()
    completed = progress.get('completed', [])
    failed = progress.get('failed', [])
    last_update = progress.get('last_update', 'N/A')

    stock_list = get_stock_list()

    print(f"\n5分钟数据下载进度:")
    print(f"  A股总数: {len(stock_list)}")
    print(f"  已完成: {len(completed)}")
    print(f"  失败: {len(failed)}")
    print(f"  待获取: {len(stock_list) - len(completed)}")
    print(f"  完成率: {len(completed)*100//len(stock_list) if stock_list else 0}%")
    print(f"  最后更新: {last_update}")

    if failed:
        print(f"\n  失败列表（前20个）:")
        for s in failed[:20]:
            print(f"    {s}")

    # 查询Parquet文件统计
    try:
        project_root = Path(__file__).resolve().parent.parent
        parquet_dir = project_root / 'data' / 'parquet' / '5m'
        
        if parquet_dir.exists():
            parquet_files = list(parquet_dir.glob('*.parquet'))
            print(f"\n  Parquet文件统计:")
            print(f"    股票数量: {len(parquet_files)}")
            
            # 统计总记录数和时间范围
            total_records = 0
            min_datetime = None
            max_datetime = None
            
            for parquet_file in parquet_files[:10]:  # 只统计前10个文件，避免性能问题
                try:
                    import pandas as pd
                    df = pd.read_parquet(parquet_file)
                    total_records += len(df)
                    if not df.empty:
                        file_min = df['datetime'].min()
                        file_max = df['datetime'].max()
                        if min_datetime is None or file_min < min_datetime:
                            min_datetime = file_min
                        if max_datetime is None or file_max > max_datetime:
                            max_datetime = file_max
                except Exception:
                    pass
            
            print(f"    总记录数: {total_records} (仅统计前10个文件)")
            if min_datetime and max_datetime:
                print(f"    时间范围: {min_datetime} ~ {max_datetime}")
        else:
            print(f"\n  Parquet文件统计:")
            print(f"    目录不存在: {parquet_dir}")
    except Exception as e:
        print(f"  Parquet文件查询失败: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='批量获取所有A股5分钟K线数据（Baostock优化版）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取所有A股近两年5分钟数据（默认8进程）
  python scripts/fetch_all_stocks_5min.py

  # 指定进程数
  python scripts/fetch_all_stocks_5min.py --workers 16

  # 指定日期范围
  python scripts/fetch_all_stocks_5min.py --start 20240409 --end 20260409

  # 全量更新
  python scripts/fetch_all_stocks_5min.py --full

  # 测试（仅获取10只股票）
  python scripts/fetch_all_stocks_5min.py --limit 10

  # 查看下载进度
  python scripts/fetch_all_stocks_5min.py --progress
        """
    )

    parser.add_argument('--workers', '-w', type=int, default=8,
                        help='进程数（默认8，建议4-16）')
    parser.add_argument('--start', type=str,
                        help='开始日期，格式 YYYYMMDD，默认为两年前')
    parser.add_argument('--end', type=str,
                        help='结束日期，格式 YYYYMMDD，默认为今天')
    parser.add_argument('--full', action='store_true',
                        help='全量更新，不跳过已有数据')
    parser.add_argument('--limit', type=int, default=0,
                        help='限制获取数量（0=不限制，测试用）')
    parser.add_argument('--progress', action='store_true',
                        help='查看下载进度')
    parser.add_argument('--symbol', type=str,
                        help='指定单个股票代码进行拉取，如 000001.XSHE')

    args = parser.parse_args()

    if args.progress:
        show_progress()
        return

    stock_list = get_stock_list()
    if not stock_list:
        print("错误: 无法获取股票列表")
        sys.exit(1)
    
    # 如果指定了单个股票，则只拉取该股票
    if args.symbol:
        stock_list = [args.symbol]
        print(f"只拉取指定股票: {args.symbol}")

    end_date = args.end if args.end else datetime.now().strftime('%Y%m%d')
    start_date = args.start if args.start else (datetime.now() - timedelta(days=365*2)).strftime('%Y%m%d')

    start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

    print(f"批量获取所有A股5分钟数据（Baostock优化版）")
    print(f"A股数量: {len(stock_list)}")
    print(f"日期范围: {start_fmt} ~ {end_fmt}")
    print(f"进程数: {args.workers}")
    print(f"更新模式: {'全量' if args.full else '增量'}")
    if args.limit > 0:
        print(f"限制数量: {args.limit}")
    print("=" * 60)

    try:
        fetch_all_stocks(
            stock_list=stock_list,
            start_date=start_fmt,
            end_date=end_fmt,
            incremental=not args.full,
            num_workers=args.workers,
            limit=args.limit
        )
    except KeyboardInterrupt:
        print("\n\n用户中断，进度已保存")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
