#!/usr/bin/env python3
"""批量获取所有A股1分钟数据（AKShare分批次版）

采用分批次（每3个月）获取的方式，减少API调用频率。
支持多进程并发获取，提高下载速度。

用法示例:
    # 获取所有A股近两年1分钟数据
    python scripts/fetch_all_stocks_1min_akshare.py

    # 指定进程数
    python scripts/fetch_all_stocks_1min_akshare.py --workers 8

    # 指定日期范围
    python scripts/fetch_all_stocks_1min_akshare.py --start 20240101 --end 20260409

    # 全量更新
    python scripts/fetch_all_stocks_1min_akshare.py --full

    # 查看统计信息
    python scripts/fetch_all_stocks_1min_akshare.py --stats
"""
import sys
import os
import argparse
import multiprocessing
from datetime import datetime, timedelta
import time
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def get_stock_list() -> list:
    """从项目数据中获取全部A股股票代码列表"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundle_dir = os.path.join(project_root, 'data', 'rqalpha', 'bundle')
    
    stock_codes = []
    
    # 尝试从instruments.pk获取
    instruments_pk = os.path.join(bundle_dir, 'instruments.pk')
    if os.path.exists(instruments_pk):
        try:
            import pickle
            with open(instruments_pk, 'rb') as f:
                instruments = pickle.load(f)
            stock_codes = [
                item['order_book_id'] for item in instruments
                if item.get('order_book_id', '').endswith(('.XSHE', '.XSHG'))
                and item.get('instrument_type') == 'CS'
            ]
            logger.info(f"从 instruments.pk 获取到 {len(stock_codes)} 只A股")
        except Exception as e:
            logger.error(f"读取 instruments.pk 失败: {e}")
    
    return stock_codes


def generate_3month_batches(start_date: str, end_date: str) -> list:
    """生成3个月的时间批次"""
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


def process_stock(symbol: str, start_date: str, end_date: str, incremental: bool = True) -> tuple:
    """处理单只股票"""
    try:
        ak_symbol = convert_symbol_to_akshare(symbol)
        if not ak_symbol:
            return symbol, False, 0, "无法转换股票代码"
        
        # 生成3个月批次
        batches = generate_3month_batches(start_date, end_date)
        
        total_records = 0
        total_days = 0
        
        for i, (batch_start, batch_end) in enumerate(batches):
            success_days, records = fetch_1min_data_range(
                symbol=symbol,
                start_date=batch_start,
                end_date=batch_end,
                incremental=incremental
            )
            total_days += success_days
            total_records += records
            
            # 批次之间添加延迟
            if i < len(batches) - 1:
                time.sleep(BATCH_DELAY)
        
        return symbol, True, total_records, f"成功获取 {total_days} 个交易日，{total_records} 条记录"
        
    except Exception as e:
        return symbol, False, 0, f"异常: {e}"


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='批量获取所有A股1分钟K线数据（AKShare分批次版）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取所有A股近两年1分钟数据
  python scripts/fetch_all_stocks_1min_akshare.py

  # 指定进程数
  python scripts/fetch_all_stocks_1min_akshare.py --workers 8

  # 指定日期范围
  python scripts/fetch_all_stocks_1min_akshare.py --start 20240101 --end 20260409

  # 全量更新
  python scripts/fetch_all_stocks_1min_akshare.py --full

  # 查看统计信息
  python scripts/fetch_all_stocks_1min_akshare.py --stats
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
    parser.add_argument('--stats', action='store_true',
                        help='查看统计信息')

    args = parser.parse_args()

    # 查看统计信息
    if args.stats:
        stats = get_1min_data_stats()
        symbols = stats.get('symbols', [])
        print(f"\n1分钟数据统计:")
        print(f"  股票数量: {stats.get('total_symbols', 0)}")
        if symbols:
            print("\n  各股票数据情况:")
            for s in symbols:
                print(f"    {s['symbol']}: {s['count']} 条 ({s['min_datetime']} ~ {s['max_datetime']})")
        return

    # 获取股票列表
    stock_list = get_stock_list()
    if not stock_list:
        logger.error("无法获取股票列表")
        return
    
    # 设置日期范围
    end_date = args.end or datetime.now().strftime('%Y%m%d')
    start_date = args.start or (datetime.now() - timedelta(days=365*2)).strftime('%Y%m%d')
    
    logger.info(f"开始批量获取 {len(stock_list)} 只A股的1分钟数据")
    logger.info(f"日期范围: {start_date} ~ {end_date}")
    logger.info(f"更新模式: {'全量' if args.full else '增量'}")
    logger.info(f"使用AKShare分批次获取方案")
    
    # 使用多进程
    num_workers = min(args.workers, multiprocessing.cpu_count())
    logger.info(f"使用 {num_workers} 个进程")
    
    total_success = 0
    total_failed = 0
    total_records = 0
    start_time = time.time()
    
    # 分批处理，每批处理一定数量的股票
    batch_size = 100
    for i in range(0, len(stock_list), batch_size):
        batch_stocks = stock_list[i:i+batch_size]
        logger.info(f"处理批次 {i//batch_size + 1}/{(len(stock_list)+batch_size-1)//batch_size}，股票数量: {len(batch_stocks)}")
        
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(process_stock, symbol, start_date, end_date, not args.full): symbol
                for symbol in batch_stocks
            }
            
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    sym, success, records, msg = future.result()
                    if success:
                        total_success += 1
                        total_records += records
                        logger.info(f"成功: {sym} - {msg}")
                    else:
                        total_failed += 1
                        logger.warning(f"失败: {sym} - {msg}")
                except Exception as e:
                    total_failed += 1
                    logger.error(f"异常: {symbol} - {e}")
        
        # 批次之间添加延迟
        if i + batch_size < len(stock_list):
            logger.info(f"等待10秒后开始下一批次...")
            time.sleep(10)
    
    elapsed = time.time() - start_time
    logger.info(f"\n{'='*60}")
    logger.info(f"全部完成!")
    logger.info(f"  成功: {total_success}, 失败: {total_failed}")
    logger.info(f"  总记录数: {total_records}")
    logger.info(f"  耗时: {elapsed/60:.1f} 分钟")


if __name__ == "__main__":
    main()
