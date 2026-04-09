import baostock as bs
import efinance as ef
import pandas as pd
from datetime import datetime, timedelta

symbol = "000001"
start_date_2y = (datetime.now() - timedelta(days=365*2)).strftime('%Y-%m-%d')
end_date = datetime.now().strftime('%Y-%m-%d')

print(f"测试股票: {symbol}, 时间范围: {start_date_2y} ~ {end_date}")
print("=" * 60)

# 1. 测试 BaoStock 5分钟数据（BaoStock没有1分钟，最小是5分钟）
print("\n【1】BaoStock - 5分钟K线数据（无1分钟）")
try:
    lg = bs.login()
    print(f"  登录: error_code={lg.error_code}, error_msg={lg.error_msg}")
    
    rs = bs.query_history_k_data_plus(
        "sz.000001",
        "date,time,code,open,high,low,close,volume,amount,adjustflag",
        start_date=start_date_2y,
        end_date=end_date,
        frequency="5",
        adjustflag="2"
    )
    
    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())
    
    df_baostock = pd.DataFrame(data_list, columns=rs.fields)
    print(f"  BaoStock 5分钟数据: {len(df_baostock)} 条")
    if not df_baostock.empty:
        print(f"  时间范围: {df_baostock['date'].iloc[0]} ~ {df_baostock['date'].iloc[-1]}")
        print(f"  前3行:\n{df_baostock.head(3)}")
    bs.logout()
except Exception as e:
    print(f"  BaoStock 失败: {e}")

# 2. 测试 BaoStock 60分钟数据
print("\n【2】BaoStock - 60分钟K线数据")
try:
    lg = bs.login()
    rs = bs.query_history_k_data_plus(
        "sz.000001",
        "date,time,code,open,high,low,close,volume,amount,adjustflag",
        start_date=start_date_2y,
        end_date=end_date,
        frequency="60",
        adjustflag="2"
    )
    
    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())
    
    df_bs_60 = pd.DataFrame(data_list, columns=rs.fields)
    print(f"  BaoStock 60分钟数据: {len(df_bs_60)} 条")
    if not df_bs_60.empty:
        print(f"  时间范围: {df_bs_60['date'].iloc[0]} ~ {df_bs_60['date'].iloc[-1]}")
        print(f"  前3行:\n{df_bs_60.head(3)}")
    bs.logout()
except Exception as e:
    print(f"  BaoStock 60分钟失败: {e}")

# 3. 测试 efinance
print("\n【3】efinance - 1分钟K线数据")
try:
    df_ef = ef.stock.get_quote_history(symbol, klt=1, fqt=1)
    print(f"  efinance 1分钟数据: {len(df_ef)} 条")
    if df_ef is not None and not df_ef.empty:
        print(f"  列名: {df_ef.columns.tolist()}")
        print(f"  前3行:\n{df_ef.head(3)}")
except Exception as e:
    print(f"  efinance 1分钟失败: {e}")

# 4. 测试 efinance 5分钟
print("\n【4】efinance - 5分钟K线数据")
try:
    df_ef5 = ef.stock.get_quote_history(symbol, klt=5, fqt=1)
    print(f"  efinance 5分钟数据: {len(df_ef5)} 条")
    if df_ef5 is not None and not df_ef5.empty:
        print(f"  列名: {df_ef5.columns.tolist()}")
        print(f"  前3行:\n{df_ef5.head(3)}")
except Exception as e:
    print(f"  efinance 5分钟失败: {e}")

# 5. 测试 AKShare stock_zh_a_hist_min_em 5分钟（可获取全量历史）
print("\n【5】AKShare - stock_zh_a_hist_min_em 5分钟数据")
try:
    import akshare as ak
    df_ak5 = ak.stock_zh_a_hist_min_em(
        symbol=symbol,
        start_date=start_date_2y + " 09:30:00",
        end_date=end_date + " 15:00:00",
        period="5",
        adjust="qfq"
    )
    print(f"  AKShare 5分钟数据: {len(df_ak5)} 条")
    if df_ak5 is not None and not df_ak5.empty:
        print(f"  列名: {df_ak5.columns.tolist()}")
        print(f"  前3行:\n{df_ak5.head(3)}")
        print(f"  后3行:\n{df_ak5.tail(3)}")
except Exception as e:
    print(f"  AKShare 5分钟失败: {e}")

# 6. 测试 AKShare stock_zh_a_hist_min_em 60分钟
print("\n【6】AKShare - stock_zh_a_hist_min_em 60分钟数据")
try:
    df_ak60 = ak.stock_zh_a_hist_min_em(
        symbol=symbol,
        start_date=start_date_2y + " 09:30:00",
        end_date=end_date + " 15:00:00",
        period="60",
        adjust="qfq"
    )
    print(f"  AKShare 60分钟数据: {len(df_ak60)} 条")
    if df_ak60 is not None and not df_ak60.empty:
        print(f"  列名: {df_ak60.columns.tolist()}")
        print(f"  前3行:\n{df_ak60.head(3)}")
        print(f"  后3行:\n{df_ak60.tail(3)}")
except Exception as e:
    print(f"  AKShare 60分钟失败: {e}")

print("\n" + "=" * 60)
print("测试完成！")
