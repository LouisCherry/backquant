#!/usr/bin/env python3
"""测试合约信息加载"""
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 设置环境变量
os.environ.setdefault('DB_TYPE', 'sqlite')
os.environ.setdefault('BACKTEST_BASE_DIR', str(project_root / 'data'))
os.environ.setdefault('PARQUET_ROOT_DIR', str(project_root / 'data' / 'parquet'))

from app.backtest.services.parquet_data_source import ParquetDataSource

# 创建模拟的 base_config
class MockBaseConfig:
    def __init__(self):
        self.data_bundle_path = str(project_root / 'data' / 'parquet')

base_config = MockBaseConfig()
data_source = ParquetDataSource(base_config)

print(f'合约数量: {len(data_source._instruments)}')
print(f'合约列表: {list(data_source._instruments.keys())[:5]}')

test_code = '000001.XSHE'
if test_code in data_source._instruments:
    instrument = data_source._instruments[test_code]
    print(f'找到合约: {test_code}')
    print(f'合约名称: {instrument.symbol}')
    print(f'上市日期: {instrument.listed_date}')
    print(f'退市日期: {instrument.de_listed_date}')
    print(f'交易所: {instrument.exchange}')
else:
    print(f'未找到合约: {test_code}')
