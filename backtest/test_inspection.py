#!/usr/bin/env python3
"""测试数据体检功能"""
import sys
import os
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from app.market_data.baostock_fetcher import _get_parquet_root
from scripts.sync_market_data import scan_parquet_data, generate_data_completeness_matrix, generate_data_distribution_histogram, generate_update_timeliness_scatter
from io import BytesIO


def test_scan_parquet_data():
    """测试扫描 Parquet 数据"""
    print("测试扫描 Parquet 数据...")
    try:
        parquet_root = _get_parquet_root()
        print(f"Parquet 根目录: {parquet_root}")
        
        stats = scan_parquet_data()
        print(f"扫描结果: {stats}")
        print(f"总股票数: {stats['total_stocks']}")
        print(f"总记录数: {stats['total_records']}")
        print(f"频率: {list(stats['frequencies'].keys())}")
        
        return stats
    except Exception as e:
        print(f"扫描数据时出错: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_generate_visualizations(stats):
    """测试生成可视化图表"""
    print("\n测试生成可视化图表...")
    try:
        # 测试数据完整性矩阵
        matrix_buffer = BytesIO()
        generate_data_completeness_matrix(stats, sample_size=50, output_path=matrix_buffer)
        matrix_buffer.seek(0)
        matrix_data = matrix_buffer.read()
        print(f"数据完整性矩阵生成成功，大小: {len(matrix_data)} 字节")
        matrix_buffer.close()
        
        # 测试数据量分布直方图
        hist_buffer = BytesIO()
        generate_data_distribution_histogram(stats, output_path=hist_buffer)
        hist_buffer.seek(0)
        hist_data = hist_buffer.read()
        print(f"数据量分布直方图生成成功，大小: {len(hist_data)} 字节")
        hist_buffer.close()
        
        # 测试更新时效性散点图
        scatter_buffer = BytesIO()
        generate_update_timeliness_scatter(stats, output_path=scatter_buffer)
        scatter_buffer.seek(0)
        scatter_data = scatter_buffer.read()
        print(f"更新时效性散点图生成成功，大小: {len(scatter_data)} 字节")
        scatter_buffer.close()
        
        return True
    except Exception as e:
        print(f"生成可视化图表时出错: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("开始测试数据体检功能...")
    stats = test_scan_parquet_data()
    if stats:
        test_generate_visualizations(stats)
    print("测试完成！")
