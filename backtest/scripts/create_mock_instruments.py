#!/usr/bin/env python3
"""创建模拟的股票基础信息

由于网络问题无法从 AkShare 获取数据，这里创建一些模拟数据用于测试。
"""
import sys
from pathlib import Path
import pandas as pd

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.utils.parquet_utils import read_parquet_safe, write_parquet_safe


def create_mock_instruments():
    """创建模拟的股票基础信息
    
    Returns:
        DataFrame: 模拟的股票信息
    """
    print("=" * 60)
    print("创建模拟的股票基础信息")
    print("=" * 60)
    
    # 创建一些常见的股票
    stocks = [
        {'code': '000001', 'name': '平安银行', 'exchange': 'XSHE'},
        {'code': '000002', 'name': '万科A', 'exchange': 'XSHE'},
        {'code': '000004', 'name': '国华网安', 'exchange': 'XSHE'},
        {'code': '000005', 'name': 'ST星源', 'exchange': 'XSHE'},
        {'code': '000006', 'name': '深振业A', 'exchange': 'XSHE'},
        {'code': '000007', 'name': '全新好', 'exchange': 'XSHE'},
        {'code': '000008', 'name': '神州高铁', 'exchange': 'XSHE'},
        {'code': '000009', 'name': '中国宝安', 'exchange': 'XSHE'},
        {'code': '000010', 'name': '美丽生态', 'exchange': 'XSHE'},
        {'code': '600000', 'name': '浦发银行', 'exchange': 'XSHG'},
        {'code': '600001', 'name': '邯郸钢铁', 'exchange': 'XSHG'},
        {'code': '600002', 'name': '齐鲁石化', 'exchange': 'XSHG'},
        {'code': '600003', 'name': 'ST东北高', 'exchange': 'XSHG'},
        {'code': '600004', 'name': '白云机场', 'exchange': 'XSHG'},
        {'code': '600005', 'name': '武钢股份', 'exchange': 'XSHG'},
    ]
    
    result = []
    for stock in stocks:
        order_book_id = f"{stock['code']}.{stock['exchange']}"
        
        result.append({
            'order_book_id': order_book_id,
            'symbol': stock['name'],
            'board_type': 'CS',  # 股票
            'listed_date': '2000-01-01',  # 默认上市日期
            'de_listed_date': '2200-01-01',  # 默认退市日期
            'tick_size': 0.01,  # 最小跳动单位
            'margin_rate': 1.0,  # 保证金率
            'commission_rate': 0.0008,  # 手续费率
            'frozen_days': 0,  # 冻结天数
        })
    
    df = pd.DataFrame(result)
    
    print(f"创建完成，共 {len(df)} 只股票")
    print(f"列名: {list(df.columns)}")
    
    return df


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
    print(f"\n所有记录:")
    print(df)
    
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
    print("创建模拟的股票基础信息并保存为 Parquet")
    print("=" * 60)
    
    # 输出文件路径
    output_path = Path(__file__).resolve().parent.parent / 'data' / 'parquet' / 'instruments.parquet'
    
    try:
        # 1. 创建模拟数据
        df = create_mock_instruments()
        
        # 2. 保存为 Parquet
        save_to_parquet(df, output_path)
        
        # 3. 验证
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
