#!/usr/bin/env python3
"""测试 Parquet 存储功能

这个脚本用于测试 akshare_fetcher.py 中的 Parquet 存储功能是否正常工作。
"""
import sys
import os
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 设置环境变量
os.environ.setdefault('DB_TYPE', 'sqlite')
os.environ.setdefault('BACKTEST_BASE_DIR', str(project_root / 'data'))

from app.market_data.akshare_fetcher import (
    _get_parquet_root,
    save_1min_data_to_parquet,
    update_market_data_files,
    get_last_datetime_in_parquet,
    fetch_1min_data_range,
)


def test_parquet_root():
    """测试获取 Parquet 根目录"""
    print("=" * 60)
    print("测试 1: 获取 Parquet 根目录")
    print("=" * 60)
    
    parquet_root = _get_parquet_root()
    print(f"Parquet 根目录: {parquet_root}")
    print(f"目录是否存在: {parquet_root.exists()}")
    print()


def test_save_to_parquet():
    """测试保存数据到 Parquet 文件"""
    print("=" * 60)
    print("测试 2: 保存数据到 Parquet 文件")
    print("=" * 60)
    
    # 创建测试数据
    test_data = [
        {
            'symbol': '000001',
            'exchange': 'SZ',
            'datetime': '2026-04-10 09:31:00',
            'interval': '1m',
            'volume': 100000,
            'turnover': 1500000.0,
            'open_interest': 0.0,
            'open_price': 15.0,
            'high_price': 15.2,
            'low_price': 14.9,
            'close_price': 15.1,
        },
        {
            'symbol': '000001',
            'exchange': 'SZ',
            'datetime': '2026-04-10 09:32:00',
            'interval': '1m',
            'volume': 120000,
            'turnover': 1800000.0,
            'open_interest': 0.0,
            'open_price': 15.1,
            'high_price': 15.3,
            'low_price': 15.0,
            'close_price': 15.2,
        },
    ]
    
    symbol = '000001.XSHE'
    count, parquet_path = save_1min_data_to_parquet(test_data, symbol)
    
    print(f"保存记录数: {count}")
    print(f"Parquet 文件路径: {parquet_path}")
    print(f"文件是否存在: {parquet_path.exists()}")
    
    if parquet_path.exists():
        file_size = parquet_path.stat().st_size
        print(f"文件大小: {file_size} bytes")
    
    print()


def test_update_market_data_files():
    """测试更新 market_data_files 表"""
    print("=" * 60)
    print("测试 3: 更新 market_data_files 表")
    print("=" * 60)
    
    parquet_root = _get_parquet_root()
    parquet_path = parquet_root / '1m' / '000001.parquet'
    
    if not parquet_path.exists():
        print("错误: Parquet 文件不存在，请先运行测试 2")
        print()
        return
    
    symbol = '000001.XSHE'
    success = update_market_data_files(parquet_path, symbol, frequency='1m')
    
    print(f"更新结果: {'成功' if success else '失败'}")
    print()


def test_get_last_datetime():
    """测试从 Parquet 文件获取最新时间"""
    print("=" * 60)
    print("测试 4: 从 Parquet 文件获取最新时间")
    print("=" * 60)
    
    symbol = '000001.XSHE'
    last_dt = get_last_datetime_in_parquet(symbol)
    
    print(f"股票代码: {symbol}")
    print(f"最新时间: {last_dt}")
    print()


def test_fetch_1min_data_range():
    """测试获取1分钟数据并保存为 Parquet"""
    print("=" * 60)
    print("测试 5: 获取1分钟数据并保存为 Parquet")
    print("=" * 60)
    
    # 注意：这个测试会实际调用 AKShare API，可能需要较长时间
    # 如果不想实际调用 API，可以跳过这个测试
    
    print("提示: 这个测试会实际调用 AKShare API，可能需要较长时间")
    print("如果要跳过，请按 Ctrl+C")
    
    try:
        symbol = '000001.XSHE'
        start_date = '20260409'
        end_date = '20260410'
        
        print(f"股票代码: {symbol}")
        print(f"开始日期: {start_date}")
        print(f"结束日期: {end_date}")
        print(f"存储类型: parquet")
        print()
        
        unique_dates, count = fetch_1min_data_range(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            incremental=False,
            storage_type='parquet'
        )
        
        print(f"获取交易日数: {unique_dates}")
        print(f"总记录数: {count}")
    except KeyboardInterrupt:
        print("\n测试已跳过")
    except Exception as e:
        print(f"测试失败: {e}")
    
    print()


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("开始测试 Parquet 存储功能")
    print("=" * 60 + "\n")
    
    test_parquet_root()
    test_save_to_parquet()
    test_update_market_data_files()
    test_get_last_datetime()
    
    # 可选：测试实际获取数据
    # test_fetch_1min_data_range()
    
    print("=" * 60)
    print("所有测试完成")
    print("=" * 60)


if __name__ == '__main__':
    main()
