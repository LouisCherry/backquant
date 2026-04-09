"""AKShare 1分钟数据获取模块

用于获取A股1分钟级别K线数据，支持存储到 Parquet 文件或 dbbardata 表中。
支持增量更新和全量获取。
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import time
import logging
import os
from pathlib import Path

from app.database import DatabaseConnection, DatabaseConfig, get_db_connection

logger = logging.getLogger(__name__)

# 请求配置
MAX_RETRIES = 3
REQUEST_INTERVAL = 0.5  # 每次请求间隔0.5秒，避免触发反爬虫


def _get_db_config_dict() -> dict:
    """获取数据库配置字典（用于非Flask环境）"""
    db_type = os.environ.get('DB_TYPE', 'sqlite').lower()

    config = {
        'db_type': db_type,
        'sqlite_path': None,
        'host': os.environ.get('DB_HOST', 'localhost'),
        'port': int(os.environ.get('DB_PORT', '3306')),
        'database': os.environ.get('DB_NAME', 'backquant'),
        'user': os.environ.get('DB_USER', 'root'),
        'password': os.environ.get('DB_PASSWORD', ''),
    }

    if db_type == 'sqlite':
        base_dir = os.environ.get('BACKTEST_BASE_DIR', '')
        if not base_dir:
            project_root = Path(__file__).resolve().parent.parent.parent
            base_dir = str(project_root / 'data')
        config['sqlite_path'] = str(Path(base_dir) / 'market_data.sqlite3')

    return config


def _get_parquet_root() -> Path:
    """获取 Parquet 文件存储根目录
    
    优先级：
    1. 环境变量 PARQUET_ROOT_DIR
    2. 默认路径：data/parquet
    
    Returns:
        Parquet 根目录路径
    """
    parquet_root = os.environ.get('PARQUET_ROOT_DIR', '')
    if parquet_root:
        path = Path(parquet_root).expanduser()
        if not path.is_absolute():
            project_root = Path(__file__).resolve().parent.parent.parent
            path = project_root / path
    else:
        project_root = Path(__file__).resolve().parent.parent.parent
        path = project_root / 'data' / 'parquet'
    
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_dbbardata_table(db: DatabaseConnection):
    """确保 dbbardata 表存在"""
    try:
        if db.config.db_type == 'sqlite':
            ddl = """
                CREATE TABLE IF NOT EXISTS dbbardata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    volume REAL NOT NULL,
                    turnover REAL NOT NULL,
                    open_interest REAL NOT NULL,
                    open_price REAL NOT NULL,
                    high_price REAL NOT NULL,
                    low_price REAL NOT NULL,
                    close_price REAL NOT NULL,
                    UNIQUE(symbol, exchange, interval, datetime)
                )
            """
        else:
            ddl = """
                CREATE TABLE IF NOT EXISTS dbbardata (
                    id int(11) NOT NULL AUTO_INCREMENT,
                    symbol varchar(255) NOT NULL,
                    exchange varchar(255) NOT NULL,
                    datetime datetime NOT NULL,
                    `interval` varchar(255) NOT NULL,
                    volume double NOT NULL,
                    turnover double NOT NULL,
                    open_interest double NOT NULL,
                    open_price double NOT NULL,
                    high_price double NOT NULL,
                    low_price double NOT NULL,
                    close_price double NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY dbbardata_symbol_exchange_interval_datetime (symbol, exchange, `interval`, datetime),
                    KEY idx_dbbardata_exchange (exchange)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
            """
        db.execute(ddl)
    except Exception as e:
        logger.warning(f"创建表失败（可能已存在）: {e}")


def convert_symbol_to_akshare(symbol: str) -> Optional[str]:
    """将股票代码转换为AKShare格式

    Args:
        symbol: 股票代码，如 '000001.XSHE' 或 '000001'

    Returns:
        AKShare格式的代码，如 'sz000001'
    """
    # 处理带后缀的格式，如 000001.XSHE -> 000001
    if '.' in symbol:
        code = symbol.split('.')[0]
        suffix = symbol.split('.')[1].upper()
        if suffix in ('XSHE', 'SZ'):
            return f'sz{code}'
        elif suffix in ('XSHG', 'SH'):
            return f'sh{code}'

    # 纯数字代码，根据首位判断
    code = symbol
    if code.startswith('6'):
        return f'sh{code}'
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        return f'sz{code}'
    elif code.startswith('4') or code.startswith('8'):
        # 北交所
        return f'bj{code}'
    return None


def get_exchange_from_symbol(symbol: str) -> str:
    """根据股票代码获取交易所

    Args:
        symbol: 股票代码

    Returns:
        交易所代码: 'SH', 'SZ', 'BJ'
    """
    if '.' in symbol:
        suffix = symbol.split('.')[1].upper()
        if suffix == 'XSHE':
            return 'SZ'
        elif suffix == 'XSHG':
            return 'SH'

    code = symbol.split('.')[0] if '.' in symbol else symbol
    if code.startswith('6'):
        return 'SH'
    elif code.startswith('4') or code.startswith('8'):
        return 'BJ'
    return 'SZ'


def get_trading_dates(start_date: str, end_date: str) -> List[str]:
    """获取指定范围内的交易日列表

    Args:
        start_date: 开始日期，格式 'YYYYMMDD'
        end_date: 结束日期，格式 'YYYYMMDD'

    Returns:
        交易日列表，格式 ['YYYYMMDD', ...]
    """
    try:
        # AKShare 1.18.x 接口名称为 tool_trade_date_hist_sina
        df = ak.tool_trade_date_hist_sina()
        df['trade_date'] = pd.to_datetime(df['trade_date'])

        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        mask = (df['trade_date'] >= start_dt) & (df['trade_date'] <= end_dt)
        dates = df[mask]['trade_date'].dt.strftime('%Y%m%d').tolist()
        return dates
    except Exception as e:
        logger.error(f"获取交易日历失败: {e}")
        # 降级方案：使用简单的日期列表（假设所有工作日都是交易日）
        try:
            dates = []
            start_dt = datetime.strptime(start_date, '%Y%m%d')
            end_dt = datetime.strptime(end_date, '%Y%m%d')
            current = start_dt
            while current <= end_dt:
                # 跳过周末 (0=周一, 6=周日)
                if current.weekday() < 5:
                    dates.append(current.strftime('%Y%m%d'))
                current += timedelta(days=1)
            logger.warning(f"使用降级方案生成交易日列表，共 {len(dates)} 天")
            return dates
        except Exception:
            return []


def fetch_1min_data_for_date(symbol: str, trade_date: str) -> Optional[pd.DataFrame]:
    """获取指定日期的1分钟K线数据

    Args:
        symbol: AKShare格式的股票代码，如 'sz000001'
        trade_date: 交易日期，格式 'YYYYMMDD'

    Returns:
        DataFrame或None
    """
    for attempt in range(MAX_RETRIES):
        try:
            df = ak.stock_zh_a_minute(
                symbol=symbol,
                period="1",
                adjust="qfq"
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.warning(f"获取 {symbol} {trade_date} 1分钟数据失败(第{attempt+1}次): {e}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)

    return None


def fetch_1min_data_intraday(symbol: str, trade_date: str) -> Optional[pd.DataFrame]:
    """获取指定交易日的日内1分钟数据

    注意：AKShare 的 1分钟数据接口主要提供近期数据，历史数据可能无法获取。
    当前使用 stock_zh_a_minute 接口，该接口返回最近交易日的1分钟数据。

    Args:
        symbol: 股票代码（6位数字）
        trade_date: 交易日期，格式 'YYYYMMDD'

    Returns:
        DataFrame或None
    """
    # 转换为AKShare格式
    ak_symbol = convert_symbol_to_akshare(symbol)
    if not ak_symbol:
        logger.error(f"无法转换股票代码: {symbol}")
        return None

    for attempt in range(MAX_RETRIES):
        try:
            # 使用 stock_zh_a_minute 接口获取1分钟数据
            # 注意：该接口返回最近的数据，不一定是指定日期的数据
            df = ak.stock_zh_a_minute(
                symbol=ak_symbol,
                period="1",
                adjust="qfq"
            )
            if df is not None and not df.empty:
                # 过滤指定日期的数据
                if 'day' in df.columns:
                    df['date'] = pd.to_datetime(df['day']).dt.strftime('%Y%m%d')
                    df = df[df['date'] == trade_date]
                return df if not df.empty else None
        except Exception as e:
            logger.warning(f"获取 {symbol} {trade_date} 日内数据失败(第{attempt+1}次): {e}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)

    return None


def process_akshare_1min_data(df: pd.DataFrame, symbol: str) -> List[Dict]:
    """处理AKShare返回的1分钟数据，转换为标准格式

    Args:
        df: AKShare返回的DataFrame
        symbol: 原始股票代码（如 '000001.XSHE'）

    Returns:
        标准格式的数据记录列表
    """
    processed = []
    exchange = get_exchange_from_symbol(symbol)

    for _, row in df.iterrows():
        # 处理时间列 - stock_zh_a_minute 使用 'day' 列
        time_col = None
        for col in ['day', '时间', 'datetime', 'time', '日期']:
            if col in df.columns:
                time_col = col
                break

        if time_col:
            dt_val = row.get(time_col)
            if isinstance(dt_val, pd.Timestamp):
                dt_str = dt_val.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(dt_val, datetime):
                dt_str = dt_val.strftime('%Y-%m-%d %H:%M:%S')
            else:
                dt_str = str(dt_val)
        else:
            continue

        # 处理价格列 - stock_zh_a_minute 使用英文列名
        open_price = float(row.get('open', 0) or 0)
        high_price = float(row.get('high', 0) or 0)
        low_price = float(row.get('low', 0) or 0)
        close_price = float(row.get('close', 0) or 0)
        volume = float(row.get('volume', 0) or 0)
        turnover = float(row.get('amount', row.get('成交额', 0)) or 0)

        record = {
            'symbol': symbol.split('.')[0] if '.' in symbol else symbol,
            'exchange': exchange,
            'datetime': dt_str,
            'interval': '1m',
            'volume': volume,
            'turnover': turnover,
            'open_interest': 0.0,
            'open_price': open_price,
            'high_price': high_price,
            'low_price': low_price,
            'close_price': close_price,
        }
        processed.append(record)

    return processed


def get_last_datetime_in_db(symbol: str, db: Optional[DatabaseConnection] = None) -> Optional[str]:
    """获取数据库中某股票最新数据时间

    Args:
        symbol: 股票代码
        db: 数据库连接，为None时自动获取

    Returns:
        最新时间字符串或None
    """
    close_db = False
    if db is None:
        config_dict = _get_db_config_dict()
        db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
        db.connect()
        _ensure_dbbardata_table(db)
        close_db = True

    try:
        code = symbol.split('.')[0] if '.' in symbol else symbol
        # 使用 fetchone 方法
        row = db.fetchone(
            "SELECT MAX(datetime) as max_dt FROM dbbardata WHERE symbol = ? AND `interval` = '1m'",
            (code,)
        )
        if row and row.get('max_dt'):
            return row['max_dt']
    except Exception as e:
        logger.error(f"查询数据库最新时间失败: {e}")
    finally:
        if close_db:
            db.close()

    return None


def get_last_datetime_in_parquet(symbol: str, parquet_root: Optional[Path] = None) -> Optional[str]:
    """从 Parquet 文件中获取某股票最新数据时间

    Args:
        symbol: 股票代码
        parquet_root: Parquet 根目录，为None时自动获取

    Returns:
        最新时间字符串或None
    """
    if parquet_root is None:
        parquet_root = _get_parquet_root()
    
    # 提取股票代码（去掉交易所后缀）
    code = symbol.split('.')[0] if '.' in symbol else symbol
    
    # 构建文件路径
    parquet_path = parquet_root / '1m' / f'{code}.parquet'
    
    if not parquet_path.exists():
        return None
    
    try:
        # 读取 Parquet 文件
        df = pd.read_parquet(parquet_path)
        
        if df.empty or 'datetime' not in df.columns:
            return None
        
        # 获取最新的时间
        max_datetime = df['datetime'].max()
        return str(max_datetime)
    except Exception as e:
        logger.error(f"读取 Parquet 文件最新时间失败: {e}")
        return None


def save_1min_data_to_db(data: List[Dict], db: Optional[DatabaseConnection] = None) -> int:
    """将1分钟数据保存到数据库

    Args:
        data: 数据记录列表
        db: 数据库连接，为None时自动获取

    Returns:
        保存的记录数
    """
    if not data:
        return 0

    close_db = False
    if db is None:
        config_dict = _get_db_config_dict()
        db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
        db.connect()
        _ensure_dbbardata_table(db)
        close_db = True

    try:
        count = 0
        for r in data:
            try:
                # 使用 replace_into 方法处理重复数据
                db.replace_into(
                    'dbbardata',
                    ['symbol', 'exchange', 'datetime', 'interval', 'volume', 'turnover',
                     'open_interest', 'open_price', 'high_price', 'low_price', 'close_price'],
                    (r['symbol'], r['exchange'], r['datetime'], r['interval'],
                     r['volume'], r['turnover'], r['open_interest'],
                     r['open_price'], r['high_price'], r['low_price'], r['close_price'])
                )
                count += 1
            except Exception as e:
                logger.warning(f"插入数据失败: {e}")

        logger.info(f"成功存储 {count}/{len(data)} 条1分钟数据到数据库")
        return count
    except Exception as e:
        logger.error(f"保存数据到数据库失败: {e}")
        return 0
    finally:
        if close_db:
            db.close()


def save_1min_data_to_parquet(
    data: List[Dict], 
    symbol: str,
    parquet_root: Optional[Path] = None
) -> Tuple[int, Path]:
    """将1分钟数据保存到 Parquet 文件

    Args:
        data: 数据记录列表
        symbol: 股票代码
        parquet_root: Parquet 根目录，为None时自动获取

    Returns:
        (保存的记录数, Parquet 文件路径)
    """
    if not data:
        return 0, Path('')
    
    if parquet_root is None:
        parquet_root = _get_parquet_root()
    
    # 提取股票代码（去掉交易所后缀）
    code = symbol.split('.')[0] if '.' in symbol else symbol
    
    # 构建文件路径：{root_dir}/1m/{code}.parquet
    parquet_dir = parquet_root / '1m'
    parquet_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = parquet_dir / f'{code}.parquet'
    
    try:
        # 转换为 DataFrame
        df_new = pd.DataFrame(data)
        
        # 确保 datetime 列是字符串格式（Parquet 兼容）
        if 'datetime' in df_new.columns:
            df_new['datetime'] = df_new['datetime'].astype(str)
        
        # 如果文件已存在，读取并合并
        if parquet_path.exists():
            try:
                df_existing = pd.read_parquet(parquet_path)
                # 合并数据
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                # 去重（按 datetime 列）
                df_combined = df_combined.drop_duplicates(subset=['datetime'], keep='last')
                # 按时间排序
                df_combined = df_combined.sort_values('datetime').reset_index(drop=True)
                df_to_save = df_combined
            except Exception as e:
                logger.warning(f"读取已有 Parquet 文件失败，将覆盖: {e}")
                df_to_save = df_new
        else:
            df_to_save = df_new
        
        # 保存为 Parquet 文件
        df_to_save.to_parquet(
            parquet_path, 
            index=False, 
            compression='snappy'
        )
        
        count = len(df_to_save)
        logger.info(f"成功存储 {len(data)} 条新数据到 Parquet 文件: {parquet_path}")
        logger.info(f"文件总记录数: {count}")
        
        return count, parquet_path
    except Exception as e:
        logger.error(f"保存数据到 Parquet 失败: {e}")
        return 0, Path('')


def update_market_data_files(
    parquet_path: Path,
    symbol: str,
    frequency: str = '1m',
    db: Optional[DatabaseConnection] = None
) -> bool:
    """更新 market_data_files 表中的文件元数据

    Args:
        parquet_path: Parquet 文件路径
        symbol: 股票代码
        frequency: 数据频率（如 '1m', '5m', '1d'）
        db: 数据库连接，为None时自动获取

    Returns:
        是否更新成功
    """
    if not parquet_path.exists():
        logger.warning(f"Parquet 文件不存在: {parquet_path}")
        return False
    
    close_db = False
    if db is None:
        config_dict = _get_db_config_dict()
        db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
        db.connect()
        close_db = True
    
    try:
        # 获取文件信息
        file_name = parquet_path.name
        file_path = str(parquet_path.resolve())
        file_size = parquet_path.stat().st_size
        modified_at = datetime.fromtimestamp(parquet_path.stat().st_mtime).isoformat()
        
        # 提取股票代码（去掉交易所后缀）
        code = symbol.split('.')[0] if '.' in symbol else symbol
        
        # 使用 upsert 方法插入或更新记录
        db.upsert(
            table='market_data_files',
            insert_cols=['file_name', 'file_path', 'file_size', 'modified_at'],
            insert_vals=(file_name, file_path, file_size, modified_at),
            conflict_col='file_name',
            update_cols=['file_path', 'file_size', 'modified_at']
        )
        
        logger.info(f"成功更新 market_data_files 表: {file_name}")
        return True
    except Exception as e:
        logger.error(f"更新 market_data_files 表失败: {e}")
        return False
    finally:
        if close_db:
            db.close()


def fetch_1min_data_range(
    symbol: str,
    start_date: str,
    end_date: str,
    incremental: bool = True,
    storage_type: str = 'parquet',
    progress_callback=None
) -> Tuple[int, int]:
    """获取指定时间范围的1分钟数据

    注意：AKShare的stock_zh_a_minute接口只返回近期数据，无法指定历史日期范围。
    该函数会获取所有可用的近期数据，然后过滤出指定日期范围的数据。

    Args:
        symbol: 股票代码，如 '000001.XSHE' 或 '000001'
        start_date: 开始日期，格式 'YYYYMMDD'
        end_date: 结束日期，格式 'YYYYMMDD'
        incremental: 是否增量更新（跳过已存在的数据）
        storage_type: 存储类型，'parquet' 或 'db'，默认 'parquet'
        progress_callback: 进度回调函数，接收 (current, total, message)

    Returns:
        (成功获取的交易日数, 总记录数)
    """
    # 验证 storage_type
    if storage_type not in ('parquet', 'db'):
        logger.warning(f"不支持的存储类型: {storage_type}，使用默认值 'parquet'")
        storage_type = 'parquet'
    
    logger.info(f"存储类型: {storage_type}")
    
    # 确定实际开始日期
    if incremental:
        if storage_type == 'parquet':
            # 从 Parquet 文件中获取最新时间
            last_dt = get_last_datetime_in_parquet(symbol)
        else:
            # 从数据库中获取最新时间
            last_dt = get_last_datetime_in_db(symbol)
        
        if last_dt:
            # 从上次数据的后一天开始
            last_date = datetime.strptime(last_dt[:10], '%Y-%m-%d')
            effective_start = (last_date + timedelta(days=1)).strftime('%Y%m%d')
            logger.info(f"已有数据，最新时间: {last_dt}，从 {effective_start} 开始增量获取")
        else:
            effective_start = start_date
            logger.info(f"无数据，从 {effective_start} 开始全量获取")
    else:
        effective_start = start_date
        logger.info(f"全量获取模式，从 {effective_start} 开始")

    # 转换股票代码
    ak_symbol = convert_symbol_to_akshare(symbol)
    if not ak_symbol:
        logger.error(f"无法转换股票代码: {symbol}")
        return 0, 0

    logger.info(f"正在获取 {symbol} 的1分钟数据...")
    
    # 获取所有可用的近期数据（AKShare只返回近期数据）
    df = None
    for attempt in range(MAX_RETRIES):
        try:
            df = ak.stock_zh_a_minute(
                symbol=ak_symbol,
                period="1",
                adjust="qfq"
            )
            if df is not None and not df.empty:
                break
        except Exception as e:
            logger.warning(f"获取 {symbol} 1分钟数据失败(第{attempt+1}次): {e}")
        
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
    
    if df is None or df.empty:
        logger.warning(f"未获取到 {symbol} 的1分钟数据")
        return 0, 0
    
    logger.info(f"成功获取 {len(df)} 条原始数据")
    
    # 过滤指定日期范围的数据
    if 'day' in df.columns:
        df['date_str'] = pd.to_datetime(df['day']).dt.strftime('%Y%m%d')
        mask = (df['date_str'] >= effective_start) & (df['date_str'] <= end_date)
        df_filtered = df[mask].copy()
        logger.info(f"过滤后剩余 {len(df_filtered)} 条数据（日期范围: {effective_start} ~ {end_date}）")
    else:
        df_filtered = df
        logger.warning(f"未找到日期列，使用全部数据")
    
    if df_filtered.empty:
        logger.warning(f"指定日期范围内无可用数据")
        return 0, 0
    
    # 处理数据
    processed_data = process_akshare_1min_data(df_filtered, symbol)
    
    if not processed_data:
        logger.warning(f"处理数据后为空")
        return 0, 0
    
    # 根据存储类型保存数据
    if storage_type == 'parquet':
        count, parquet_path = save_1min_data_to_parquet(processed_data, symbol)
        if count > 0:
            # 更新数据库元数据
            update_market_data_files(parquet_path, symbol, frequency='1m')
    else:
        count = save_1min_data_to_db(processed_data)
    
    # 计算交易日数
    if 'day' in df_filtered.columns:
        unique_dates = pd.to_datetime(df_filtered['day']).dt.date.nunique()
    else:
        unique_dates = 1
    
    logger.info(f"完成! 成功获取 {unique_dates} 个交易日，共 {count} 条记录")
    return unique_dates, count


def get_1min_data_stats(symbol: Optional[str] = None, db: Optional[DatabaseConnection] = None) -> Dict:
    """获取1分钟数据统计信息

    Args:
        symbol: 股票代码，为None时返回所有统计
        db: 数据库连接

    Returns:
        统计信息字典
    """
    close_db = False
    if db is None:
        config_dict = _get_db_config_dict()
        db = DatabaseConnection(DatabaseConfig.from_dict(config_dict))
        db.connect()
        _ensure_dbbardata_table(db)
        close_db = True

    try:
        if symbol:
            code = symbol.split('.')[0] if '.' in symbol else symbol
            row = db.fetchone(
                """SELECT COUNT(*) as cnt, MIN(datetime) as min_dt, MAX(datetime) as max_dt
                   FROM dbbardata WHERE symbol = ? AND `interval` = '1m'""",
                (code,)
            )
            if row:
                return {
                    'symbol': symbol,
                    'count': row.get('cnt', 0) or 0,
                    'min_datetime': row.get('min_dt'),
                    'max_datetime': row.get('max_dt'),
                }
        else:
            rows = db.fetchall(
                """SELECT symbol, COUNT(*) as cnt, MIN(datetime) as min_dt, MAX(datetime) as max_dt
                   FROM dbbardata WHERE `interval` = '1m'
                   GROUP BY symbol"""
            )
            stats = []
            for row in rows:
                stats.append({
                    'symbol': row.get('symbol'),
                    'count': row.get('cnt', 0) or 0,
                    'min_datetime': row.get('min_dt'),
                    'max_datetime': row.get('max_dt'),
                })
            return {'symbols': stats, 'total_symbols': len(stats)}
    except Exception as e:
        logger.error(f"获取统计数据失败: {e}")
        return {}
    finally:
        if close_db:
            db.close()

    return {}
