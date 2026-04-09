#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据整合脚本：将a_stock.db中的数据转换为RQAlpha格式并合并到RQAlpha的数据文件中
"""

import sqlite3
import h5py
import numpy as np
import os
from pathlib import Path


def convert_code(code):
    """转换股票代码格式
    sh.600000 -> 600000.XSHG
    sz.000001 -> 000001.XSHE
    """
    if code.startswith('sh.'):
        return f"{code[3:]}.XSHG"
    elif code.startswith('sz.'):
        return f"{code[3:]}.XSHE"
    return code


def convert_date(date_str):
    """转换日期格式
    1999-11-10 -> 19991110000000
    """
    date_str = date_str.replace('-', '')
    return int(date_str + '000000')


def integrate_data(a_stock_db_path, rqalpha_bundle_path):
    """整合a_stock.db数据到RQAlpha bundle中"""
    # 连接a_stock.db数据库
    conn = sqlite3.connect(a_stock_db_path)
    cursor = conn.cursor()
    
    # 获取所有股票代码
    cursor.execute("SELECT DISTINCT code FROM stock_history")
    stock_codes = [row[0] for row in cursor.fetchall()]
    print(f"发现 {len(stock_codes)} 只股票")
    
    # 打开RQAlpha的stocks.h5文件
    stocks_h5_path = os.path.join(rqalpha_bundle_path, 'stocks.h5')
    with h5py.File(stocks_h5_path, 'a') as f:
        for i, code in enumerate(stock_codes):
            # 转换股票代码格式
            rq_code = convert_code(code)
            print(f"处理 {i+1}/{len(stock_codes)}: {code} -> {rq_code}")
            
            # 从a_stock.db中读取数据
            cursor.execute("""
                SELECT date, open, high, low, close, preclose, volume, amount 
                FROM stock_history 
                WHERE code = ? 
                ORDER BY date
            """, (code,))
            data = cursor.fetchall()
            
            if not data:
                print(f"  无数据，跳过")
                continue
            
            # 转换数据格式
            converted_data = []
            for row in data:
                date, open_, high, low, close, preclose, volume, amount = row
                datetime = convert_date(date)
                # RQAlpha格式: (datetime, open, close, high, low, prev_close, limit_up, limit_down, volume, total_turnover)
                # 计算涨跌停价格（简单计算：前收盘价 * 1.1 和 * 0.9）
                limit_up = preclose * 1.1 if preclose else 0
                limit_down = preclose * 0.9 if preclose else 0
                converted_data.append((
                    datetime,  # datetime
                    open_,     # open
                    close,     # close
                    high,      # high
                    low,       # low
                    preclose,  # prev_close
                    limit_up,  # limit_up
                    limit_down, # limit_down
                    volume,    # volume
                    amount     # total_turnover
                ))
            
            # 转换为numpy数组
            dtype = [
                ('datetime', '<i8'),
                ('open', '<f8'),
                ('close', '<f8'),
                ('high', '<f8'),
                ('low', '<f8'),
                ('prev_close', '<f8'),
                ('limit_up', '<f8'),
                ('limit_down', '<f8'),
                ('volume', '<f8'),
                ('total_turnover', '<f8')
            ]
            np_data = np.array(converted_data, dtype=dtype)
            
            # 写入到stocks.h5文件
            if rq_code in f:
                # 如果股票已存在，先删除旧数据
                del f[rq_code]
            f.create_dataset(rq_code, data=np_data)
    
    # 关闭数据库连接
    conn.close()
    print("\n数据整合完成！")


if __name__ == '__main__':
    # 路径配置
    a_stock_db_path = '/Users/panshunxing/eclipse-workspace/BackQuant/a_stock.db'
    rqalpha_bundle_path = '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/data/rqalpha/bundle'
    
    # 检查路径是否存在
    if not os.path.exists(a_stock_db_path):
        print(f"错误：a_stock.db文件不存在: {a_stock_db_path}")
        exit(1)
    
    if not os.path.exists(rqalpha_bundle_path):
        print(f"错误：RQAlpha bundle路径不存在: {rqalpha_bundle_path}")
        exit(1)
    
    # 执行数据整合
    integrate_data(a_stock_db_path, rqalpha_bundle_path)
