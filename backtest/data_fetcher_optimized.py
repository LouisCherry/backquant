import baostock as bs
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import time
import sqlite3
import os
import concurrent.futures

db_path = os.path.join(os.path.abspath('.'), 'market_data.sqlite3')

MAX_WORKERS = 3
MAX_RETRIES = 3
REQUEST_INTERVAL = 0.3

SUPPORTED_FREQUENCIES = {
    '5m': {'baostock': '5', 'akshare': '5'},
    '15m': {'baostock': '15', 'akshare': '15'},
    '30m': {'baostock': '30', 'akshare': '30'},
    '60m': {'baostock': '60', 'akshare': '60'},
    '1d': {'baostock': 'd', 'akshare': 'daily'},
}


def _symbol_to_baostock(symbol):
    if symbol.startswith('6'):
        return f'sh.{symbol}'
    elif symbol.startswith('0') or symbol.startswith('3'):
        return f'sz.{symbol}'
    return None


def _symbol_to_exchange(symbol):
    if symbol.startswith('6'):
        return 'SH'
    return 'SZ'


def get_baostock_minute_data(symbol, start_date, end_date, frequency='5'):
    bs_symbol = _symbol_to_baostock(symbol)
    if not bs_symbol:
        return pd.DataFrame()

    for attempt in range(MAX_RETRIES):
        try:
            lg = bs.login()
            if lg.error_code != '0':
                print(f"  BaoStock 登录失败: {lg.error_msg}")
                time.sleep(1)
                continue

            rs = bs.query_history_k_data_plus(
                bs_symbol,
                "date,time,code,open,high,low,close,volume,amount,adjustflag",
                start_date=start_date,
                end_date=end_date,
                frequency=frequency,
                adjustflag="2"
            )

            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())

            bs.logout()

            if data_list:
                df = pd.DataFrame(data_list, columns=rs.fields)
                print(f"  BaoStock 成功，共 {len(df)} 条")
                return df
            else:
                print(f"  BaoStock 返回空数据(第{attempt+1}次)")
        except Exception as e:
            print(f"  BaoStock 失败(第{attempt+1}次): {e}")
            try:
                bs.logout()
            except Exception:
                pass

        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)

    return pd.DataFrame()


def get_akshare_minute_data(symbol, start_date, end_date, frequency='5'):
    for attempt in range(MAX_RETRIES):
        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=frequency,
                adjust="qfq"
            )
            if df is not None and not df.empty:
                print(f"  AKShare 成功，共 {len(df)} 条")
                return df
        except Exception as e:
            print(f"  AKShare 失败(第{attempt+1}次): {e}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)

    return pd.DataFrame()


def process_baostock_data(data, symbol, interval):
    processed = []
    for _, row in data.iterrows():
        date_str = row.get('date', '')
        time_str = row.get('time', '')
        if len(time_str) == 17:
            dt_str = f"{date_str} {time_str[8:10]}:{time_str[10:12]}:{time_str[12:14]}"
        elif len(time_str) == 14:
            dt_str = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[8:10]}:{time_str[10:12]}:{time_str[12:14]}"
        else:
            dt_str = f"{date_str} {time_str}"

        record = {
            'symbol': symbol,
            'exchange': _symbol_to_exchange(symbol),
            'datetime': dt_str,
            'interval': interval,
            'volume': float(row.get('volume', 0) or 0),
            'turnover': float(row.get('amount', 0) or 0),
            'open_interest': 0.0,
            'open_price': float(row.get('open', 0) or 0),
            'high_price': float(row.get('high', 0) or 0),
            'low_price': float(row.get('low', 0) or 0),
            'close_price': float(row.get('close', 0) or 0),
        }
        processed.append(record)
    return processed


def process_akshare_data(data, symbol, interval):
    processed = []
    for _, row in data.iterrows():
        dt_val = row.get('时间', row.get('datetime', ''))
        if isinstance(dt_val, pd.Timestamp):
            dt_str = dt_val.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(dt_val, datetime):
            dt_str = dt_val.strftime('%Y-%m-%d %H:%M:%S')
        else:
            dt_str = str(dt_val)

        record = {
            'symbol': symbol,
            'exchange': _symbol_to_exchange(symbol),
            'datetime': dt_str,
            'interval': interval,
            'volume': float(row.get('成交量', row.get('volume', 0))),
            'turnover': float(row.get('成交额', row.get('amount', 0))),
            'open_interest': 0.0,
            'open_price': float(row.get('开盘', row.get('open', 0))),
            'high_price': float(row.get('最高', row.get('high', 0))),
            'low_price': float(row.get('最低', row.get('low', 0))),
            'close_price': float(row.get('收盘', row.get('close', 0))),
        }
        processed.append(record)
    return processed


def save_data_to_db(data):
    if not data:
        return
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        query = """
            INSERT OR REPLACE INTO dbbardata
            (symbol, exchange, datetime, interval, volume, turnover, open_interest,
             open_price, high_price, low_price, close_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        values = [
            (
                r['symbol'], r['exchange'], r['datetime'], r['interval'],
                r['volume'], r['turnover'], r['open_interest'],
                r['open_price'], r['high_price'], r['low_price'], r['close_price']
            )
            for r in data
        ]
        cursor.executemany(query, values)
        conn.commit()
        print(f"  成功存储 {len(values)} 条数据到数据库")
        conn.close()
    except Exception as e:
        print(f"  存储数据到数据库失败: {e}")


def get_last_datetime(symbol, interval):
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MAX(datetime) FROM dbbardata WHERE symbol = ? AND interval = ?",
            (symbol, interval)
        )
        result = cursor.fetchone()
        conn.close()
        if result and result[0]:
            return result[0]
        return None
    except Exception:
        return None


def backup_database():
    try:
        backup_path = os.path.join(
            os.path.abspath('.'),
            f'market_data_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.sqlite3'
        )
        conn = sqlite3.connect(db_path)
        backup_conn = sqlite3.connect(backup_path)
        with backup_conn:
            conn.backup(backup_conn)
        conn.close()
        backup_conn.close()
        print(f"数据库备份成功: {backup_path}")
    except Exception as e:
        print(f"备份数据库失败: {e}")


def process_single_stock(symbol, name, start_date, end_date, interval='5m'):
    print(f"\n正在处理: {name} ({symbol}) 周期: {interval}")

    freq_config = SUPPORTED_FREQUENCIES.get(interval)
    if not freq_config:
        print(f"  不支持的周期: {interval}")
        return

    last_dt = get_last_datetime(symbol, interval)
    if last_dt:
        effective_start = (datetime.strptime(last_dt, '%Y-%m-%d %H:%M:%S') + timedelta(minutes=1)).strftime('%Y-%m-%d')
        print(f"  数据库中已有数据，最新时间: {last_dt}，从 {effective_start} 开始获取")
    else:
        effective_start = start_date
        print(f"  数据库中无数据，从 {effective_start} 开始获取")

    bs_freq = freq_config['baostock']
    ak_freq = freq_config['akshare']

    minute_data = get_baostock_minute_data(symbol, effective_start, end_date, bs_freq)

    if not minute_data.empty:
        processed_data = process_baostock_data(minute_data, symbol, interval)
        save_data_to_db(processed_data)
    else:
        print("  BaoStock 获取失败，尝试 AKShare...")
        ak_start = effective_start + " 09:30:00" if len(effective_start) == 10 else effective_start
        ak_end = end_date + " 15:00:00" if len(end_date) == 10 else end_date
        minute_data = get_akshare_minute_data(symbol, ak_start, ak_end, ak_freq)

        if not minute_data.empty:
            processed_data = process_akshare_data(minute_data, symbol, interval)
            save_data_to_db(processed_data)
        else:
            print(f"  {name} ({symbol}) 所有数据源获取失败")

    time.sleep(REQUEST_INTERVAL)


def main():
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365 * 2)).strftime('%Y-%m-%d')

    test_stocks = pd.DataFrame({
        '代码': ['600519', '000001', '000858'],
        '名称': ['贵州茅台', '平安银行', '五粮液']
    })

    interval = '5m'

    print(f"开始获取 {len(test_stocks)} 只股票的 {interval} 级别数据")
    print(f"时间范围: {start_date} ~ {end_date}")
    print(f"数据源优先级: BaoStock -> AKShare")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for _, row in test_stocks.iterrows():
            future = executor.submit(
                process_single_stock,
                row['代码'], row['名称'], start_date, end_date, interval
            )
            futures.append(future)

        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"处理股票失败: {e}")

    print("\n备份数据库")
    backup_database()

    print("\n查询数据库统计...")
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, interval, COUNT(*), MIN(datetime), MAX(datetime) FROM dbbardata GROUP BY symbol, interval")
        for row in cursor.fetchall():
            print(f"  {row[0]} [{row[1]}]: {row[2]} 条, {row[3]} ~ {row[4]}")
        conn.close()
    except Exception as e:
        print(f"查询统计失败: {e}")


if __name__ == "__main__":
    main()
