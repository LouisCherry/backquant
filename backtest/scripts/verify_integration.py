#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证数据整合脚本：检查a_stock.db数据是否成功整合到RQAlpha bundle中
"""

import h5py
import os


def verify_integration(rqalpha_bundle_path):
    """验证数据整合是否成功"""
    # 打开RQAlpha的stocks.h5文件
    stocks_h5_path = os.path.join(rqalpha_bundle_path, 'stocks.h5')
    with h5py.File(stocks_h5_path, 'r') as f:
        # 检查一些常见股票是否存在
        test_codes = [
            '600000.XSHG',  # 浦发银行
            '000001.XSHE',  # 平安银行
            '600519.XSHG',  # 贵州茅台
            '000858.XSHE',  # 五粮液
            '601318.XSHG'   # 中国平安
        ]
        
        print("验证数据整合结果：")
        print("-" * 50)
        
        for code in test_codes:
            if code in f:
                data = f[code]
                print(f"✓ {code} 存在，数据条数: {data.shape[0]}")
                print(f"  数据格式: {data.dtype}")
                print(f"  第一条数据: {data[0]}")
                print(f"  最后一条数据: {data[-1]}")
            else:
                print(f"✗ {code} 不存在")
            print()
        
        # 统计总股票数
        total_stocks = len(f.keys())
        print(f"-" * 50)
        print(f"总股票数: {total_stocks}")
        print("验证完成！")


if __name__ == '__main__':
    # 路径配置
    rqalpha_bundle_path = '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/data/rqalpha/bundle'
    
    # 检查路径是否存在
    if not os.path.exists(rqalpha_bundle_path):
        print(f"错误：RQAlpha bundle路径不存在: {rqalpha_bundle_path}")
        exit(1)
    
    # 执行验证
    verify_integration(rqalpha_bundle_path)
