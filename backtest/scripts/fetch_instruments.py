#!/usr/bin/env python3
"""获取 A股基础信息并保存为 Parquet 文件

这个脚本从 AkShare 获取 A股的基础信息，并保存为 Parquet 格式。
"""
import sys
import os
from pathlib import Path
from datetime import datetime
import time

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import akshare as ak
import pandas as pd
from app.utils.parquet_utils import read_parquet_safe, write_parquet_safe


def get_stock_info_from_akshare():
    """从 AkShare 获取 A股基础信息
    
    Returns:
        DataFrame: 包含股票基础信息
    """
    print("=" * 60)
    print("从 AkShare 获取 A股基础信息")
    print("=" * 60)
    
    try:
        # 获取 A股股票列表
        print("\n正在获取 A股股票列表...")
        df = ak.stock_zh_a_spot_em()
        
        print(f"成功获取 {len(df)} 只股票")
        print(f"原始列名: {list(df.columns)}")
        
        return df
        
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        raise


def process_stock_info(df):
    """处理股票信息，转换为 RQAlpha 需要的格式
    
    Args:
        df: 原始股票信息 DataFrame
        
    Returns:
        DataFrame: 处理后的股票信息
    """
    print("\n" + "=" * 60)
    print("处理股票信息")
    print("=" * 60)
    
    # 创建新的 DataFrame
    result = []
    
    for _, row in df.iterrows():
        try:
            # 提取股票代码
            code = str(row.get('代码', ''))
            if not code:
                continue
            
            # 推断交易所
            if code.startswith('6'):
                exchange = 'XSHG'
            else:
                exchange = 'XSHE'
            
            order_book_id = f"{code}.{exchange}"
            
            # 提取股票名称
            symbol = str(row.get('名称', ''))
            
            # 上市日期（AkShare 的实时行情接口不提供上市日期，使用默认值）
            # TODO: 后续可以从其他接口获取上市日期
            listed_date = '2000-01-01'
            
            # 退市日期（默认值）
            de_listed_date = '2200-01-01'
            
            result.append({
                'order_book_id': order_book_id,
                'symbol': symbol,
                'board_type': 'CS',  # 股票
                'listed_date': listed_date,
                'de_listed_date': de_listed_date,
                'tick_size': 0.01,  # 最小跳动单位
                'margin_rate': 1.0,  # 保证金率
                'commission_rate': 0.0008,  # 手续费率
                'frozen_days': 0,  # 冻结天数
            })
            
        except Exception as e:
            print(f"处理股票 {row.get('代码', 'unknown')} 失败: {e}")
            continue
    
    result_df = pd.DataFrame(result)
    
    print(f"处理完成，共 {len(result_df)} 只股票")
    print(f"列名: {list(result_df.columns)}")
    
    return result_df


def save_to_parquet(df, output_path):
    """保存为 Parquet 文件
    
    Args:
        df: 要保存的 DataFrame
        output_path: 输出文件路径
    """
    print("\n" + "=" * 60)
    print("保存为 Parquet 文件")
    print("=" * 60)
    
    # 确保目录存在
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 保存为 Parquet
    if write_parquet_safe(df, output_path, index=False, compression='snappy'):
        print(f"保存成功: {output_path}")
        print(f"文件大小: {output_path.stat().st_size} bytes")
        print(f"记录数: {len(df)}")
    else:
        print(f"保存失败: {output_path}")
        raise Exception("保存 Parquet 文件失败")


def verify_parquet(parquet_path):
    """验证 Parquet 文件
    
    Args:
        parquet_path: Parquet 文件路径
    """
    print("\n" + "=" * 60)
    print("验证 Parquet 文件")
    print("=" * 60)
    
    # 读取 Parquet 文件
    df = read_parquet_safe(parquet_path)
    if df is None:
        print(f"读取失败: {parquet_path}")
        raise Exception("读取 Parquet 文件失败")
    
    print(f"读取成功，共 {len(df)} 条记录")
    print(f"\n列名: {list(df.columns)}")
    print(f"\n前 5 条记录:")
    print(df.head())
    
    # 检查特定股票
    test_code = '000001.XSHE'
    test_stock = df[df['order_book_id'] == test_code]
    if not test_stock.empty:
        print(f"\n测试股票 {test_code}:")
        print(test_stock.iloc[0].to_dict())
    else:
        print(f"\n警告: 未找到测试股票 {test_code}")


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("获取 A股基础信息并保存为 Parquet")
    print("=" * 60)
    
    # 输出文件路径
    output_path = Path(__file__).resolve().parent.parent / 'data' / 'parquet' / 'instruments.parquet'
    
    try:
        # 1. 获取股票信息
        df = get_stock_info_from_akshare()
        
        # 2. 处理股票信息
        processed_df = process_stock_info(df)
        
        # 3. 保存为 Parquet
        save_to_parquet(processed_df, output_path)
        
        # 4. 验证
        verify_parquet(output_path)
        
        print("\n" + "=" * 60)
        print("完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
