#!/usr/bin/env python3
"""验证AKShare获取的1分钟数据"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.market_data.akshare_fetcher import get_1min_data_stats

print("=" * 60)
print("验证AKShare获取的1分钟数据")
print("=" * 60)

# 获取统计信息
stats = get_1min_data_stats()
symbols = stats.get('symbols', [])

print(f"\n总股票数量: {stats.get('total_symbols', 0)}")

if symbols:
    for s in symbols:
        print(f"\n股票代码: {s['symbol']}")
        print(f"  记录数: {s['count']}")
        print(f"  最早时间: {s['min_datetime']}")
        print(f"  最晚时间: {s['max_datetime']}")
        
        # 计算时间范围
        from datetime import datetime
        if s['min_datetime'] and s['max_datetime']:
            try:
                min_dt = datetime.strptime(s['min_datetime'][:19], '%Y-%m-%d %H:%M:%S')
                max_dt = datetime.strptime(s['max_datetime'][:19], '%Y-%m-%d %H:%M:%S')
                delta = max_dt - min_dt
                print(f"  时间跨度: {delta.days} 天")
            except Exception as e:
                print(f"  计算时间跨度失败: {e}")

print("\n" + "=" * 60)
print("数据验证完成!")
print("=" * 60)
