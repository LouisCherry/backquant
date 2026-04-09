#!/usr/bin/env python3
"""在项目中测试AKShare"""
import sys
import os

print("Testing AKShare in project...")
print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")

try:
    import akshare
    print(f"\n✓ AKShare imported successfully!")
    print(f"  AKShare version: {akshare.__version__}")
    
    # 测试获取1分钟数据
    print(f"\nTesting AKShare 1-minute data...")
    df = akshare.stock_zh_a_minute(
        symbol="sz000001",
        period="1",
        adjust="qfq"
    )
    
    if df is not None and not df.empty:
        print(f"✓ Successfully fetched 1-minute data!")
        print(f"  Data shape: {df.shape}")
        print(f"  Columns: {list(df.columns)}")
        print(f"  First 5 rows:")
        print(df.head())
    else:
        print("✗ No data returned")
        
except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
