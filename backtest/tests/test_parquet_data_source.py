#!/usr/bin/env python3
"""测试 Parquet 数据源

这个脚本用于测试 ParquetDataSource 类的基本功能。
"""
import sys
import os
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 设置环境变量
os.environ.setdefault('DB_TYPE', 'sqlite')
os.environ.setdefault('BACKTEST_BASE_DIR', str(project_root / 'data'))
os.environ.setdefault('PARQUET_ROOT_DIR', str(project_root / 'data' / 'parquet'))

from app.backtest.services.parquet_data_source import ParquetDataSource


def test_parquet_data_source():
    """测试 ParquetDataSource 类"""
    print("=" * 60)
    print("测试 ParquetDataSource 类")
    print("=" * 60)
    
    # 创建模拟的 base_config
    class MockBaseConfig:
        def __init__(self):
            self.data_bundle_path = os.environ.get('PARQUET_ROOT_DIR', 'data/parquet')
    
    base_config = MockBaseConfig()
    
    try:
        # 初始化数据源
        print("\n1. 初始化 ParquetDataSource...")
        data_source = ParquetDataSource(base_config)
        print("   ✓ 初始化成功")
        
        # 测试加载 Parquet 数据
        print("\n2. 测试加载 Parquet 数据...")
        df = data_source._load_parquet_data('000001.XSHE', '1m')
        if df is not None:
            print(f"   ✓ 成功加载数据: {len(df)} 条记录")
            print(f"   列名: {list(df.columns)}")
            if len(df) > 0:
                print(f"   第一条数据: {df.iloc[0].to_dict()}")
        else:
            print("   ✗ 加载数据失败")
        
        # 测试获取交易日历
        print("\n3. 测试获取交易日历...")
        calendars = data_source.get_trading_calendars()
        if calendars:
            print(f"   ✓ 获取到 {len(calendars)} 个交易日历")
            for cal_type, dates in calendars.items():
                print(f"   - {cal_type}: {len(dates)} 个交易日")
        else:
            print("   ✗ 未获取到交易日历")
        
        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    test_parquet_data_source()
