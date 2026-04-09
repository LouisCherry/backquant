#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import baostock as bs
import pandas as pd

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'baostock_test_result.txt')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(msg + '\n')

log("=" * 50)
log("Baostock 5分钟数据测试")
log("=" * 50)

lg = bs.login()
log(f"登录: {lg.error_code} {lg.error_msg}")

try:
    rs = bs.query_history_k_data_plus(
        "sz.000001",
        "date,time,open,high,low,close,volume,amount",
        start_date="2024-04-01",
        end_date="2024-04-01",
        frequency="5",
        adjustflag="2"
    )
    log(f"查询: {rs.error_code} {rs.error_msg}")

    data_list = []
    while rs.error_code == "0" and rs.next():
        data_list.append(rs.get_row_data())

    log(f"数据条数: {len(data_list)}")
    if data_list:
        df = pd.DataFrame(data_list, columns=rs.fields)
        log(f"列名: {df.columns.tolist()}")
        log(f"前5行:\n{df.head()}")
        log(f"时间示例: {df['time'].iloc[0]}")
    else:
        log("未获取到数据")
except Exception as e:
    log(f"错误: {e}")
    import traceback
    log(traceback.format_exc())
finally:
    bs.logout()
    log("已登出")
