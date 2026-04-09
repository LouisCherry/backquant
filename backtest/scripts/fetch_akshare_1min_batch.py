#!/usr/bin/env python3
"""AKShare 1分钟数据批量获取脚本（分批次版）

用于获取A股1分钟级别K线数据并存储到数据库。
采用分批次（每3个月）获取的方式，减少API调用频率。

用法示例:
    # 获取单只股票近两年数据
    python scripts/fetch_akshare_1min_batch.py --symbol 000001.XSHE

    # 获取指定日期范围
    python scripts/fetch_akshare_1min_batch.py --symbol 000001.XSHE --start 20240101 --end 20241231

    # 全量更新（覆盖已有数据）
    python scripts/fetch_akshare_1min_batch.py --symbol 000001.XSHE --full

    # 查看统计信息
    python scripts/fetch_akshare_1min_batch.py --stats
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from datetime import datetime, timedelta
import time
import logging
from app.market_data.akshare_fetcher import (
    fetch_1min_data_range,
    get_1min_data_stats,
    convert_symbol_to_akshare
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 批次配置
BATCH_INTERVAL = 90  # 3个月约90天
BATCH_DELAY = 5  # 批次之间的延迟（秒）


def generate_3month_batches(start_date: str, end_date: str) -> list:
    """生成3个月的时间批次

    Args:
        start_date: 开始日期，格式 'YYYYMMDD'
        end_date: 结束日期，格式 'YYYYMMDD'

    Returns:
        批次列表，每个元素为 (start, end) 元组
    """
    batches = []
    start_dt = datetime.strptime(start_date, '%Y%m%d')
    end_dt = datetime.strptime(end_date, '%Y%m%d')
    
    current = start_dt
    while current < end_dt:
        batch_end = min(current + timedelta(days=BATCH_INTERVAL), end_dt)
        batches.append((
            current.strftime('%Y%m%d'),
            batch_end.strftime('%Y%m%d')
        ))
        current = batch_end + timedelta(days=1)
    
    return batches


def print_progress(current, total, message):
    """打印进度信息"""
    percent = (current / total) * 100 if total > 0 else 0
    print(f"\r[{current}/{total}] {percent:.1f}% - {message}", end='', flush=True)


def main():
    parser = argparse.ArgumentParser(
        description='批量获取A股1分钟K线数据（分批次版）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取平安银行近两年数据
  python scripts/fetch_akshare_1min_batch.py --symbol 000001.XSHE

  # 获取指定日期范围
  python scripts/fetch_akshare_1min_batch.py --symbol 000001.XSHE --start 20240101 --end 20241231

  # 全量更新（不跳过已有数据）
  python scripts/fetch_akshare_1min_batch.py --symbol 000001.XSHE --full

  # 查看统计信息
  python scripts/fetch_akshare_1min_batch.py --stats
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
            stats = get_1min_data_stats(args.symbol)
            if stats:
                print(f"\n股票 {args.symbol} 的1分钟数据统计:")
                print(f"  记录数: {stats.get('count', 0)}")
                print(f"  最早时间: {stats.get('min_datetime', 'N/A')}")
                print(f"  最晚时间: {stats.get('max_datetime', 'N/A')}")
            else:
                print(f"\n股票 {args.symbol} 暂无1分钟数据")
        else:
            stats = get_1min_data_stats()
            symbols = stats.get('symbols', [])
            print(f"\n1分钟数据统计:")
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
    ak_symbol = convert_symbol_to_akshare(args.symbol)
    if not ak_symbol:
        print(f"错误: 无法识别的股票代码格式: {args.symbol}")
        print("支持的格式: 000001.XSHE, 000001, 600000.XSHG, 600000 等")
        sys.exit(1)

    # 设置日期范围
    end_date = args.end or datetime.now().strftime('%Y%m%d')
    start_date = args.start or (datetime.now() - timedelta(days=365*2)).strftime('%Y%m%d')

    # 生成3个月批次
    batches = generate_3month_batches(start_date, end_date)
    
    print(f"开始获取 {args.symbol} 的1分钟数据")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"更新模式: {'全量' if args.full else '增量'}")
    print(f"分批次获取: {len(batches)} 个批次（每3个月一批）")
    print("-" * 60)

    total_success_days = 0
    total_records = 0

    try:
        for i, (batch_start, batch_end) in enumerate(batches):
            print(f"\n批次 {i+1}/{len(batches)}: {batch_start} ~ {batch_end}")
            print("-" * 40)
            
            # 获取当前批次数据
            success_days, records = fetch_1min_data_range(
                symbol=args.symbol,
                start_date=batch_start,
                end_date=batch_end,
                incremental=not args.full,
                progress_callback=print_progress
            )
            
            total_success_days += success_days
            total_records += records
            
            print(f"\n批次 {i+1} 完成: 成功 {success_days} 个交易日，{records} 条记录")
            
            # 批次之间添加延迟
            if i < len(batches) - 1:
                print(f"\n等待 {BATCH_DELAY} 秒后开始下一批次...")
                time.sleep(BATCH_DELAY)

        print(f"\n{'-' * 60}")
        print(f"全部完成! 成功获取 {total_success_days} 个交易日，共 {total_records} 条记录")
        print(f"总批次: {len(batches)}")

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
