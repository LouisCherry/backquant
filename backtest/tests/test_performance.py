#!/usr/bin/env python3
"""性能测试脚本

测试 Parquet 数据源的性能优化效果：
1. 缓存机制的效果
2. 列级读取的效果
3. 元数据优化的效果
"""
import sys
import os
from pathlib import Path
import time
from datetime import datetime

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 设置环境变量
os.environ.setdefault('DB_TYPE', 'sqlite')
os.environ.setdefault('BACKTEST_BASE_DIR', str(project_root / 'data'))
os.environ.setdefault('PARQUET_ROOT_DIR', str(project_root / 'data' / 'parquet'))

from app.backtest.services.parquet_data_source import ParquetDataSource


def test_cache_performance():
    """测试缓存机制的性能"""
    print("=" * 60)
    print("测试 1: 缓存机制的性能")
    print("=" * 60)
    
    # 创建模拟的 base_config
    class MockBaseConfig:
        def __init__(self):
            self.data_bundle_path = str(project_root / 'data' / 'parquet')
    
    base_config = MockBaseConfig()
    data_source = ParquetDataSource(base_config)
    
    # 测试第一次读取（无缓存）
    print("\n第一次读取（无缓存）:")
    start_time = time.time()
    df1 = data_source._load_parquet_data('000001.XSHE', '1m')
    first_read_time = time.time() - start_time
    print(f"  耗时: {first_read_time:.4f} 秒")
    print(f"  记录数: {len(df1) if df1 is not None else 0}")
    
    # 测试第二次读取（有缓存）
    print("\n第二次读取（有缓存）:")
    start_time = time.time()
    df2 = data_source._load_parquet_data('000001.XSHE', '1m')
    cached_read_time = time.time() - start_time
    print(f"  耗时: {cached_read_time:.4f} 秒")
    print(f"  记录数: {len(df2) if df2 is not None else 0}")
    
    # 计算性能提升
    if cached_read_time > 0:
        speedup = first_read_time / cached_read_time
        print(f"\n性能提升: {speedup:.2f}x")
    
    print()


def test_column_pruning_performance():
    """测试列级读取的性能"""
    print("=" * 60)
    print("测试 2: 列级读取的性能")
    print("=" * 60)
    
    # 创建模拟的 base_config
    class MockBaseConfig:
        def __init__(self):
            self.data_bundle_path = str(project_root / 'data' / 'parquet')
    
    base_config = MockBaseConfig()
    data_source = ParquetDataSource(base_config)
    
    # 测试读取所有列
    print("\n读取所有列:")
    start_time = time.time()
    df_all = data_source._load_parquet_data('000001.XSHE', '1m', columns=None)
    all_columns_time = time.time() - start_time
    print(f"  耗时: {all_columns_time:.4f} 秒")
    print(f"  列数: {len(df_all.columns) if df_all is not None else 0}")
    print(f"  列名: {list(df_all.columns) if df_all is not None else []}")
    
    # 测试读取部分列
    print("\n读取部分列 (open, close):")
    start_time = time.time()
    df_partial = data_source._load_parquet_data('000001.XSHE', '1m', columns=['open', 'close'])
    partial_columns_time = time.time() - start_time
    print(f"  耗时: {partial_columns_time:.4f} 秒")
    print(f"  列数: {len(df_partial.columns) if df_partial is not None else 0}")
    print(f"  列名: {list(df_partial.columns) if df_partial is not None else []}")
    
    # 计算性能提升
    if partial_columns_time > 0:
        speedup = all_columns_time / partial_columns_time
        print(f"\n性能提升: {speedup:.2f}x")
    
    print()


def test_metadata_optimization():
    """测试元数据优化的性能"""
    print("=" * 60)
    print("测试 3: 元数据优化的性能")
    print("=" * 60)
    
    # 创建模拟的 base_config
    class MockBaseConfig:
        def __init__(self):
            self.data_bundle_path = str(project_root / 'data' / 'parquet')
    
    base_config = MockBaseConfig()
    data_source = ParquetDataSource(base_config)
    
    # 测试获取时间范围（优化后）
    print("\n获取时间范围（优化后 - 只读取 datetime 列）:")
    start_time = time.time()
    start_date, end_date = data_source.available_data_range('1m')
    optimized_time = time.time() - start_time
    print(f"  耗时: {optimized_time:.4f} 秒")
    print(f"  时间范围: {start_date} ~ {end_date}")
    
    # 测试读取整个文件（对比）
    print("\n读取整个文件（对比）:")
    start_time = time.time()
    df = data_source._load_parquet_data('000001.XSHE', '1m')
    full_read_time = time.time() - start_time
    print(f"  耗时: {full_read_time:.4f} 秒")
    print(f"  记录数: {len(df) if df is not None else 0}")
    
    # 计算性能提升
    if optimized_time > 0:
        speedup = full_read_time / optimized_time
        print(f"\n性能提升: {speedup:.2f}x")
    
    print()


def test_multiple_reads():
    """测试多次读取的性能"""
    print("=" * 60)
    print("测试 4: 多次读取的性能（模拟实际使用场景）")
    print("=" * 60)
    
    # 创建模拟的 base_config
    class MockBaseConfig:
        def __init__(self):
            self.data_bundle_path = str(project_root / 'data' / 'parquet')
    
    base_config = MockBaseConfig()
    data_source = ParquetDataSource(base_config)
    
    # 模拟策略多次请求同一股票的数据
    print("\n模拟策略多次请求同一股票的数据（10次）:")
    total_time = 0
    for i in range(10):
        start_time = time.time()
        df = data_source._load_parquet_data('000001.XSHE', '1m', columns=['open', 'close'])
        elapsed = time.time() - start_time
        total_time += elapsed
        print(f"  第 {i+1} 次读取: {elapsed:.4f} 秒")
    
    avg_time = total_time / 10
    print(f"\n总耗时: {total_time:.4f} 秒")
    print(f"平均耗时: {avg_time:.4f} 秒")
    print()


def test_different_columns():
    """测试不同列组合的性能"""
    print("=" * 60)
    print("测试 5: 不同列组合的性能")
    print("=" * 60)
    
    # 创建模拟的 base_config
    class MockBaseConfig:
        def __init__(self):
            self.data_bundle_path = str(project_root / 'data' / 'parquet')
    
    base_config = MockBaseConfig()
    data_source = ParquetDataSource(base_config)
    
    # 测试不同的列组合
    column_combinations = [
        ['open'],
        ['open', 'close'],
        ['open', 'high', 'low', 'close'],
        ['open', 'high', 'low', 'close', 'volume'],
        None,  # 所有列
    ]
    
    for columns in column_combinations:
        columns_str = ', '.join(columns) if columns else '所有列'
        print(f"\n读取列: {columns_str}")
        
        start_time = time.time()
        df = data_source._load_parquet_data('000001.XSHE', '1m', columns=columns)
        elapsed = time.time() - start_time
        
        print(f"  耗时: {elapsed:.4f} 秒")
        print(f"  列数: {len(df.columns) if df is not None else 0}")
    
    print()


def main():
    """运行所有性能测试"""
    print("\n" + "=" * 60)
    print("Parquet 数据源性能测试")
    print("=" * 60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"数据目录: {os.environ['PARQUET_ROOT_DIR']}")
    print()
    
    try:
        # 运行测试
        test_cache_performance()
        test_column_pruning_performance()
        test_metadata_optimization()
        test_multiple_reads()
        test_different_columns()
        
        print("=" * 60)
        print("所有测试完成")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
