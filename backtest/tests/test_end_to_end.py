#!/usr/bin/env python3
"""端到端集成测试

测试从数据下载到回测运行的完整流程：
1. 下载测试数据（1分钟级别）
2. 创建测试策略
3. 运行回测
4. 验证结果
"""
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import tempfile
import shutil

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 设置环境变量
os.environ.setdefault('DB_TYPE', 'sqlite')
os.environ.setdefault('BACKTEST_BASE_DIR', str(project_root / 'data'))
os.environ.setdefault('PARQUET_ROOT_DIR', str(project_root / 'data' / 'parquet'))
os.environ.setdefault('MARKET_DATA_STORAGE_TYPE', 'parquet')

from app.market_data.akshare_fetcher import fetch_1min_data_range
from app.backtest.services.runner import run_backtest


# 测试策略代码
TEST_STRATEGY_CODE = """
from rqalpha.api import *

def init(context):
    # 订阅股票
    context.stock = '000001.XSHE'
    context.bought = False
    logger.info(f"策略初始化，订阅股票: {context.stock}")

def handle_bar(context, bar_dict):
    # 第一天开盘买入 100 股
    if not context.bought:
        order_shares(context.stock, 100)
        context.bought = True
        logger.info(f"买入 100 股 {context.stock}")
"""


def print_section(title):
    """打印分节标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def test_download_data():
    """测试数据下载"""
    print_section("步骤 1: 下载测试数据")
    
    # 测试参数
    symbol = '000001.XSHE'
    
    # 使用最近的日期（AKShare 只返回近期数据）
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
    
    print(f"股票代码: {symbol}")
    print(f"开始日期: {start_date}")
    print(f"结束日期: {end_date}")
    print(f"存储类型: parquet")
    print()
    
    try:
        # 下载数据
        unique_dates, count = fetch_1min_data_range(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            incremental=False,
            storage_type='parquet'  # 使用 Parquet 存储
        )
        
        print(f"\n✓ 下载完成")
        print(f"  交易日数: {unique_dates}")
        print(f"  总记录数: {count}")
        
        # 验证文件是否存在
        parquet_path = Path(os.environ['PARQUET_ROOT_DIR']) / '1m' / f'{symbol.split(".")[0]}.parquet'
        if parquet_path.exists():
            print(f"  文件路径: {parquet_path}")
            print(f"  文件大小: {parquet_path.stat().st_size} bytes")
            return True, parquet_path
        else:
            print(f"  ✗ 文件不存在: {parquet_path}")
            return False, None
            
    except Exception as e:
        print(f"\n✗ 下载失败: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_run_backtest(parquet_path):
    """测试运行回测"""
    print_section("步骤 2: 运行回测")
    
    # 验证合约信息
    print("\n验证合约信息:")
    try:
        from app.backtest.services.parquet_data_source import ParquetDataSource
        
        # 创建模拟的 base_config
        class MockBaseConfig:
            def __init__(self):
                self.data_bundle_path = os.environ.get('PARQUET_ROOT_DIR', 'data/parquet')
        
        base_config = MockBaseConfig()
        data_source = ParquetDataSource(base_config)
        
        # 检查合约信息
        test_code = '000001.XSHE'
        if test_code in data_source._instruments:
            instrument = data_source._instruments[test_code]
            print(f"✓ 找到合约: {test_code}")
            print(f"  合约名称: {instrument.symbol}")
            print(f"  上市日期: {instrument.listed_date}")
            print(f"  退市日期: {instrument.de_listed_date}")
            print(f"  交易所: {instrument.exchange}")
        else:
            print(f"✗ 未找到合约: {test_code}")
    except Exception as e:
        print(f"✗ 验证合约信息失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 创建临时目录
    temp_dir = Path(tempfile.mkdtemp(prefix='backtest_test_'))
    strategy_file = temp_dir / 'test_strategy.py'
    output_root = temp_dir / 'output'
    
    try:
        # 写入策略文件
        strategy_file.write_text(TEST_STRATEGY_CODE, encoding='utf-8')
        print(f"策略文件: {strategy_file}")
        
        # 准备回测参数
        # 使用下载的数据范围（根据实际下载的数据调整）
        # 注意：AKShare 只返回近期数据，所以使用最近的日期
        # 从日志中看到数据范围是 2026-04-03 到 2026-04-09
        start_date = '2026-04-03'
        end_date = '2026-04-09'
        
        params = {
            'strategy_path': str(strategy_file),
            'start_date': start_date,
            'end_date': end_date,
            'frequency': '1m',
            'init_cash': 100000,
            'benchmark': '000001.XSHE',  # 使用我们已有的合约作为基准
            'bundle_path': str(project_root / 'data' / 'rqalpha' / 'bundle'),  # 使用正确的 bundle 路径
            'output_root': str(output_root),
            'data_source_type': 'parquet',
        }
        
        print(f"\n回测参数:")
        print(f"  开始日期: {start_date}")
        print(f"  结束日期: {end_date}")
        print(f"  频率: 1m")
        print(f"  初始资金: 100000")
        print(f"  基准: 000001.XSHE")
        print(f"  数据源: parquet")
        print()
        
        # 运行回测
        print("正在运行回测...")
        result = run_backtest(params)
        
        print(f"\n✓ 回测完成")
        print(f"  运行ID: {result.get('run_id')}")
        print(f"  输出目录: {result.get('output_dir')}")
        print(f"  指标文件: {result.get('metrics_path')}")
        print(f"  净值文件: {result.get('nav_path')}")
        print(f"  交易记录: {result.get('trades_path')}")
        
        # 检查关键文件
        output_dir = Path(result.get('output_dir', ''))
        if output_dir.exists():
            # 检查结果文件
            result_pickle = output_dir / 'result.pkl'
            if result_pickle.exists():
                print(f"\n✓ 结果文件存在: {result_pickle}")
            
            # 检查日志文件
            log_file = output_dir / 'backtest.log'
            if log_file.exists():
                print(f"✓ 日志文件存在: {log_file}")
                # 打印最后几行日志
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        print(f"\n最后 10 行日志:")
                        for line in lines[-10:]:
                            print(f"  {line.rstrip()}")
                except Exception as e:
                    print(f"  读取日志失败: {e}")
        
        return True, result
        
    except Exception as e:
        print(f"\n✗ 回测失败: {e}")
        import traceback
        traceback.print_exc()
        return False, None
    finally:
        # 清理临时目录
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
                print(f"\n清理临时目录: {temp_dir}")
        except Exception as e:
            print(f"清理失败: {e}")


def verify_results(result):
    """验证回测结果"""
    print_section("步骤 3: 验证结果")
    
    if result is None:
        print("✗ 结果为空")
        return False
    
    # 检查关键指标
    success = True
    
    # 检查输出文件
    output_dir = Path(result.get('output_dir', ''))
    if not output_dir.exists():
        print(f"✗ 输出目录不存在: {output_dir}")
        return False
    
    # 检查 metrics.csv
    metrics_path = Path(result.get('metrics_path', ''))
    if metrics_path.exists():
        print(f"✓ 指标文件存在: {metrics_path}")
        try:
            import pandas as pd
            df = pd.read_csv(metrics_path)
            print(f"  指标数量: {len(df)}")
        except Exception as e:
            print(f"  读取指标失败: {e}")
    else:
        print(f"✗ 指标文件不存在: {metrics_path}")
        success = False
    
    # 检查 nav.csv
    nav_path = Path(result.get('nav_path', ''))
    if nav_path.exists():
        print(f"✓ 净值文件存在: {nav_path}")
        try:
            import pandas as pd
            df = pd.read_csv(nav_path)
            print(f"  净值记录数: {len(df)}")
            if len(df) > 0:
                print(f"  最后净值: {df.iloc[-1].to_dict()}")
        except Exception as e:
            print(f"  读取净值失败: {e}")
    else:
        print(f"✗ 净值文件不存在: {nav_path}")
        success = False
    
    # 检查 trades.csv
    trades_path = Path(result.get('trades_path', ''))
    if trades_path.exists():
        print(f"✓ 交易记录文件存在: {trades_path}")
        try:
            import pandas as pd
            df = pd.read_csv(trades_path)
            print(f"  交易记录数: {len(df)}")
            if len(df) > 0:
                print(f"  交易记录:")
                for _, row in df.iterrows():
                    print(f"    {row.to_dict()}")
        except Exception as e:
            print(f"  读取交易记录失败: {e}")
    else:
        print(f"✗ 交易记录文件不存在: {trades_path}")
        success = False
    
    return success


def main():
    """运行端到端测试"""
    print("\n" + "=" * 60)
    print("  端到端集成测试")
    print("  测试从数据下载到回测运行的完整流程")
    print("=" * 60)
    
    # 步骤 1: 下载测试数据
    download_success, parquet_path = test_download_data()
    if not download_success:
        print("\n❌ 测试失败: 数据下载失败")
        return False
    
    # 步骤 2: 运行回测
    backtest_success, result = test_run_backtest(parquet_path)
    if not backtest_success:
        print("\n❌ 测试失败: 回测运行失败")
        return False
    
    # 步骤 3: 验证结果
    verify_success = verify_results(result)
    
    # 最终结果
    print_section("测试结果")
    if verify_success:
        print("✅ 所有测试通过！")
        print("\n端到端集成测试成功完成：")
        print("  1. ✓ 数据下载成功")
        print("  2. ✓ 回测运行成功")
        print("  3. ✓ 结果验证成功")
        return True
    else:
        print("❌ 部分测试失败")
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
