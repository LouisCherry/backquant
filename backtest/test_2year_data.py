#!/usr/bin/env python3
"""测试AKShare能否获取2年的1分钟数据"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import akshare as ak
import pandas as pd
from datetime import datetime

print("=" * 60)
print("测试AKShare获取2年1分钟数据的能力")
print("=" * 60)

try:
    # 获取平安银行的1分钟数据
    print("\n正在获取平安银行(sz000001)的1分钟数据...")
    df = ak.stock_zh_a_minute(
        symbol="sz000001",
        period="1",
        adjust="qfq"
    )
    
    if df is not None and not df.empty:
        print(f"\n✓ 成功获取数据！")
        print(f"  数据形状: {df.shape}")
        print(f"  数据列: {list(df.columns)}")
        
        # 显示时间范围
        if 'day' in df.columns:
            df['datetime'] = pd.to_datetime(df['day'])
            min_date = df['datetime'].min()
            max_date = df['datetime'].max()
            delta = max_date - min_date
            
            print(f"\n📊 数据时间范围:")
            print(f"  最早时间: {min_date}")
            print(f"  最晚时间: {max_date}")
            print(f"  时间跨度: {delta.days} 天 ({delta.days/365:.1f} 年)")
            
            if delta.days < 30:
                print(f"\n⚠️  重要提醒:")
                print(f"   AKShare只能获取近期数据（约{delta.days}天）")
                print(f"   无法获取完整的2年历史数据")
                
                # 显示前几行和后几行
                print(f"\n📈 前5条数据:")
                print(df.head())
                print(f"\n📉 后5条数据:")
                print(df.tail())
            else:
                print(f"\n✓ 数据时间跨度足够")
    else:
        print("\n✗ 未获取到数据")
        
except Exception as e:
    print(f"\n✗ 错误: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
