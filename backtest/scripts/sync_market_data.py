#!/usr/bin/env python3
"""智能数据下载与迁移脚本（Baostock 版本）

功能：
1. 日线数据迁移：从 CSV/HDF5 转换为 Parquet 格式
2. 分钟数据增量更新：使用 Baostock 获取历史数据（支持5分钟）
3. 多线程并发下载，大幅提升速度
4. 支持进度条、断点续传、日志记录
5. 数据体检功能：扫描数据完整性、生成可视化报告

用法示例：
    # 仅迁移日线数据
    python scripts/sync_market_data.py
    
    # 迁移日线 + 下载最近 30 天的 5 分钟数据
    python scripts/sync_market_data.py --intraday_days 30
    
    # 指定线程数
    python scripts/sync_market_data.py --intraday_days 30 --workers 10
    
    # 查看统计信息
    python scripts/sync_market_data.py --stats
    
    # 数据体检模式
    python scripts/sync_market_data.py --inspect
    
    # 数据体检模式（指定频率）
    python scripts/sync_market_data.py --inspect --frequency 5m
"""
import sys
import os
import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import pandas as pd
import numpy as np
from tqdm import tqdm
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
from matplotlib.font_manager import FontProperties
import random
import platform


def setup_chinese_font():
    """配置中文字体"""
    system = platform.system()
    
    if system == 'Darwin':  # macOS
        font_path = '/System/Library/Fonts/PingFang.ttc'
        if os.path.exists(font_path):
            plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Arial Unicode MS']
            return
    
    if system == 'Linux':
        font_paths = [
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                from matplotlib.font_manager import FontProperties
                font_prop = FontProperties(fname=fp)
                plt.rcParams['font.family'] = font_prop.get_name()
                return
    
    if system == 'Windows':
        font_path = 'C:/Windows/Fonts/simhei.ttf'
        if os.path.exists(font_path):
            from matplotlib.font_manager import FontProperties
            font_prop = FontProperties(fname=font_path)
            plt.rcParams['font.family'] = font_prop.get_name()
            return
    
    # 如果找不到合适的中文字体，使用通用设置
    plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
    
    # 解决负号显示问题
    plt.rcParams['axes.unicode_minus'] = False


setup_chinese_font()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.market_data.baostock_fetcher import (
    get_all_stocks_baostock,
    fetch_minute_data_range_baostock,
    _get_parquet_root
)
from app.market_data.akshare_fetcher import get_last_datetime_in_parquet
from app.utils.parquet_utils import read_parquet_safe, write_parquet_safe
from app.market_data.data_writer import DataWriterFactory, get_data_writer
from app.config import CONFIG

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

PROGRESS_FILE = Path(__file__).parent.parent / 'data' / 'sync_progress.json'
LOG_FILE = Path(__file__).parent.parent / 'data' / 'sync_market_data.log'

progress_lock = threading.Lock()


def setup_file_logger():
    """设置文件日志处理器"""
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(file_handler)


def load_progress() -> Dict:
    """加载进度文件"""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载进度文件失败: {e}")
    return {
        'migrated_symbols': [],
        'intraday_updated_symbols': [],
        'failed_symbols': [],
        'last_run': None
    }


def save_progress(progress: Dict):
    """保存进度文件（线程安全）"""
    with progress_lock:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(progress, f, indent=2, ensure_ascii=False)


def migrate_daily_data_from_csv(
    old_data_path: Path,
    parquet_root: Path,
    progress: Dict
) -> Tuple[int, int]:
    """从 CSV 迁移日线数据到 Parquet
    
    Args:
        old_data_path: 旧数据目录路径
        parquet_root: Parquet 根目录
        progress: 进度字典
        
    Returns:
        (成功数量, 失败数量)
    """
    logger.info("=" * 60)
    logger.info("开始日线数据迁移")
    logger.info(f"源数据路径: {old_data_path}")
    logger.info(f"目标路径: {parquet_root / '1d'}")
    logger.info("=" * 60)
    
    if not old_data_path.exists():
        logger.warning(f"旧数据路径不存在: {old_data_path}")
        return 0, 0
    
    csv_files = list(old_data_path.glob('*.csv'))
    if not csv_files:
        logger.warning(f"未找到 CSV 文件: {old_data_path}")
        return 0, 0
    
    logger.info(f"找到 {len(csv_files)} 个 CSV 文件")
    
    success_count = 0
    failed_count = 0
    
    # Use data writer factory for flexible output format
    writer = get_data_writer()
    
    for csv_file in tqdm(csv_files, desc="迁移日线数据"):
        try:
            code = csv_file.stem
            symbol = f"{code}.XSHE" if not code.startswith('6') else f"{code}.XSHG"
            
            if symbol in progress['migrated_symbols']:
                logger.info(f"跳过已迁移: {symbol}")
                continue
            
            df = pd.read_csv(csv_file)
            
            required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                logger.warning(f"{csv_file.name} 缺少列: {missing_cols}")
                failed_count += 1
                continue
            
            df = df[required_cols].copy()
            df['datetime'] = pd.to_datetime(df['date'])
            df = df.drop(columns=['date'])
            df = df.rename(columns={
                'open': 'open_price',
                'high': 'high_price',
                'low': 'low_price',
                'close': 'close_price',
                'volume': 'volume'
            })
            
            df = df.sort_values('datetime').reset_index(drop=True)
            
            # Use data writer instead of direct parquet write
            if writer.write_daily_data(df, symbol):
                success_count += 1
                progress['migrated_symbols'].append(symbol)
                save_progress(progress)
                logger.info(f"成功迁移: {symbol} ({len(df)} 条记录)")
            else:
                failed_count += 1
                if symbol not in progress['failed_symbols']:
                    progress['failed_symbols'].append(symbol)
                save_progress(progress)
            
        except Exception as e:
            logger.error(f"迁移失败: {csv_file.name} - {e}")
            failed_count += 1
            if 'symbol' in locals() and symbol not in progress['failed_symbols']:
                progress['failed_symbols'].append(symbol)
            save_progress(progress)
    
    logger.info("=" * 60)
    logger.info(f"日线数据迁移完成: 成功 {success_count}, 失败 {failed_count}")
    logger.info("=" * 60)
    
    return success_count, failed_count


def migrate_daily_data_from_hdf5(
    old_data_path: Path,
    parquet_root: Path,
    progress: Dict
) -> Tuple[int, int]:
    """从 HDF5 迁移日线数据到 Parquet
    
    Args:
        old_data_path: 旧数据目录路径
        parquet_root: Parquet 根目录
        progress: 进度字典
        
    Returns:
        (成功数量, 失败数量)
    """
    logger.info("=" * 60)
    logger.info("开始日线数据迁移（HDF5）")
    logger.info(f"源数据路径: {old_data_path}")
    logger.info("=" * 60)
    
    if not old_data_path.exists():
        logger.warning(f"旧数据路径不存在: {old_data_path}")
        return 0, 0
    
    h5_files = list(old_data_path.glob('*.h5'))
    if not h5_files:
        logger.warning(f"未找到 HDF5 文件: {old_data_path}")
        return 0, 0
    
    logger.info(f"找到 {len(h5_files)} 个 HDF5 文件")
    
    success_count = 0
    failed_count = 0
    
    # Use data writer factory for flexible output format
    writer = get_data_writer()
    
    for h5_file in tqdm(h5_files, desc="迁移日线数据（HDF5）"):
        try:
            code = h5_file.stem
            symbol = f"{code}.XSHE" if not code.startswith('6') else f"{code}.XSHG"
            
            if symbol in progress['migrated_symbols']:
                logger.info(f"跳过已迁移: {symbol}")
                continue
            
            df = pd.read_hdf(h5_file, key='data')
            
            df = df.reset_index()
            
            if 'datetime' not in df.columns and 'date' in df.columns:
                df['datetime'] = pd.to_datetime(df['date'])
                df = df.drop(columns=['date'])
            elif 'datetime' not in df.columns:
                if 'index' in df.columns:
                    df['datetime'] = pd.to_datetime(df['index'])
                    df = df.drop(columns=['index'])
            
            column_mapping = {
                'open': 'open_price',
                'high': 'high_price',
                'low': 'low_price',
                'close': 'close_price',
            }
            df = df.rename(columns=column_mapping)
            
            required_cols = ['datetime', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']
            for col in required_cols:
                if col not in df.columns:
                    logger.warning(f"{h5_file.name} 缺少列: {col}")
                    failed_count += 1
                    continue
            
            df = df.sort_values('datetime').reset_index(drop=True)
            
            # Use data writer instead of direct parquet write
            if writer.write_daily_data(df, symbol):
                success_count += 1
                progress['migrated_symbols'].append(symbol)
                save_progress(progress)
                logger.info(f"成功迁移: {symbol} ({len(df)} 条记录)")
            else:
                failed_count += 1
                if symbol not in progress['failed_symbols']:
                    progress['failed_symbols'].append(symbol)
                save_progress(progress)
            
        except Exception as e:
            logger.error(f"迁移失败: {h5_file.name} - {e}")
            failed_count += 1
            if 'symbol' in locals() and symbol not in progress['failed_symbols']:
                progress['failed_symbols'].append(symbol)
            save_progress(progress)
    
    logger.info("=" * 60)
    logger.info(f"日线数据迁移完成: 成功 {success_count}, 失败 {failed_count}")
    logger.info("=" * 60)
    
    return success_count, failed_count


def download_single_stock(
    symbol: str,
    start_date: str,
    end_date: str,
    frequency: str,
    progress: Dict
) -> Tuple[str, bool, int, str]:
    """下载单只股票的数据（线程函数）
    
    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        frequency: 数据频率
        progress: 进度字典
        
    Returns:
        (股票代码, 是否成功, 记录数, 消息)
    """
    try:
        if symbol in progress['intraday_updated_symbols']:
            return symbol, True, 0, "已跳过（已完成）"
        
        success_days, total_records = fetch_minute_data_range_baostock(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            incremental=True,
            storage_type='parquet'
        )
        
        if success_days > 0 or total_records > 0:
            with progress_lock:
                if symbol not in progress['intraday_updated_symbols']:
                    progress['intraday_updated_symbols'].append(symbol)
                save_progress(progress)
            
            return symbol, True, total_records, f"成功获取 {success_days} 天，{total_records} 条记录"
        else:
            return symbol, True, 0, "无新数据"
            
    except Exception as e:
        with progress_lock:
            if symbol not in progress['failed_symbols']:
                progress['failed_symbols'].append(symbol)
            save_progress(progress)
        
        return symbol, False, 0, f"异常: {e}"


def update_intraday_data_multithread(
    stock_list: List[str],
    intraday_days: int,
    frequency: str,
    num_workers: int,
    progress: Dict
) -> Tuple[int, int]:
    """多线程更新分钟数据
    
    Args:
        stock_list: 股票列表
        intraday_days: 下载最近 N 天的数据
        frequency: 数据频率
        num_workers: 线程数
        progress: 进度字典
        
    Returns:
        (成功数量, 失败数量)
    """
    if intraday_days <= 0:
        logger.info("跳过分钟数据更新（intraday_days=0）")
        return 0, 0
    
    logger.info("=" * 60)
    logger.info(f"开始分钟数据增量更新（最近 {intraday_days} 天）")
    logger.info(f"股票数量: {len(stock_list)}")
    logger.info(f"数据频率: {frequency} 分钟")
    logger.info(f"线程数: {num_workers}")
    logger.info("=" * 60)
    
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=intraday_days)).strftime('%Y%m%d')
    
    logger.info(f"日期范围: {start_date} ~ {end_date}")
    
    success_count = 0
    failed_count = 0
    total_records = 0
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(
                download_single_stock,
                symbol,
                start_date,
                end_date,
                frequency,
                progress
            ): symbol
            for symbol in stock_list
        }
        
        with tqdm(total=len(stock_list), desc="更新分钟数据") as pbar:
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    sym, success, records, msg = future.result()
                    if success:
                        success_count += 1
                        total_records += records
                        if records > 0:
                            logger.info(f"成功: {sym} - {msg}")
                    else:
                        failed_count += 1
                        logger.warning(f"失败: {sym} - {msg}")
                except Exception as e:
                    failed_count += 1
                    logger.error(f"异常: {symbol} - {e}")
                
                pbar.update(1)
    
    logger.info("=" * 60)
    logger.info(f"分钟数据更新完成: 成功 {success_count}, 失败 {failed_count}")
    logger.info(f"总记录数: {total_records}")
    logger.info("=" * 60)
    
    return success_count, failed_count


def show_stats():
    """显示统计信息"""
    parquet_root = _get_parquet_root()
    
    logger.info("=" * 60)
    logger.info("数据统计信息")
    logger.info("=" * 60)
    
    for freq in ['1d', '5m', '15m', '30m', '60m']:
        parquet_dir = parquet_root / freq
        if parquet_dir.exists():
            parquet_files = list(parquet_dir.glob('*.parquet'))
            logger.info(f"\n{freq} 数据:")
            logger.info(f"  股票数量: {len(parquet_files)}")
            
            if parquet_files:
                sample_file = parquet_files[0]
                try:
                    df = read_parquet_safe(sample_file)
                    if df is not None and 'datetime' in df.columns:
                        min_dt = df['datetime'].min()
                        max_dt = df['datetime'].max()
                        logger.info(f"  时间范围: {min_dt.date()} ~ {max_dt.date()}")
                        logger.info(f"  记录数: {len(df)}")
                except Exception as e:
                    logger.warning(f"  读取样本文件失败: {e}")
    
    if PROGRESS_FILE.exists():
        progress = load_progress()
        logger.info(f"\n迁移进度:")
        logger.info(f"  已迁移日线: {len(progress.get('migrated_symbols', []))}")
        logger.info(f"  已更新分钟: {len(progress.get('intraday_updated_symbols', []))}")
        logger.info(f"  失败数量: {len(progress.get('failed_symbols', []))}")
        if progress.get('last_run'):
            logger.info(f"  上次运行: {progress['last_run']}")


def scan_parquet_data(frequency: Optional[str] = None) -> Dict:
    """扫描 Parquet 数据目录，收集统计信息
    
    Args:
        frequency: 指定频率，如 '5m'、'1d'，None 表示扫描所有频率
        
    Returns:
        统计信息字典
    """
    parquet_root = _get_parquet_root()
    
    stats = {
        'frequencies': {},
        'total_stocks': 0,
        'total_records': 0
    }
    
    freq_list = [frequency] if frequency else ['1d', '5m', '15m', '30m', '60m']
    
    for freq in freq_list:
        parquet_dir = parquet_root / freq
        if not parquet_dir.exists():
            continue
            
        parquet_files = list(parquet_dir.glob('*.parquet'))
        if not parquet_files:
            continue
        
        freq_stats = {
            'stock_count': len(parquet_files),
            'stocks': [],
            'total_records': 0,
            'min_date': None,
            'max_date': None
        }
        
        for parquet_file in parquet_files:
            try:
                df = read_parquet_safe(parquet_file)
                if df is not None and 'datetime' in df.columns:
                    stock_code = parquet_file.stem
                    
                    stock_info = {
                        'code': stock_code,
                        'record_count': len(df),
                        'min_date': df['datetime'].min(),
                        'max_date': df['datetime'].max()
                    }
                    
                    freq_stats['stocks'].append(stock_info)
                    freq_stats['total_records'] += len(df)
                    
                    if freq_stats['min_date'] is None or stock_info['min_date'] < freq_stats['min_date']:
                        freq_stats['min_date'] = stock_info['min_date']
                    if freq_stats['max_date'] is None or stock_info['max_date'] > freq_stats['max_date']:
                        freq_stats['max_date'] = stock_info['max_date']
                    
            except Exception as e:
                logger.warning(f"读取文件失败 {parquet_file.name}: {e}")
        
        stats['frequencies'][freq] = freq_stats
        stats['total_stocks'] += freq_stats['stock_count']
        stats['total_records'] += freq_stats['total_records']
    
    return stats


def scan_database_data(frequency: Optional[str] = None) -> Dict:
    """扫描数据库中的数据，收集统计信息
    
    Args:
        frequency: 指定频率，如 '5m'、'1d'，None 表示扫描所有频率
        
    Returns:
        统计信息字典
    """
    import sqlite3
    from app.database import get_db_connection, DatabaseConnection, DatabaseConfig
    
    stats = {
        'frequencies': {},
        'total_stocks': 0,
        'total_records': 0
    }
    
    try:
        db_config = DatabaseConfig()
        
        # 直接使用 sqlite3 连接
        # 直接指定数据库路径
        db_path = '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/data/market_data.sqlite3'
        
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            query = ""
            if frequency:
                query = f"WHERE `interval` = '{frequency}'"
            
            # 获取所有频率
            interval_query = f"SELECT DISTINCT `interval` FROM dbbardata {query}"
            cursor.execute(interval_query)
            intervals = [row[0] for row in cursor.fetchall()]
            
            if not intervals:
                return stats
            
            for interval in intervals:
                # 获取该频率下的所有股票
                symbol_query = f"SELECT DISTINCT symbol FROM dbbardata WHERE `interval` = '{interval}'"
                cursor.execute(symbol_query)
                symbols = [row[0] for row in cursor.fetchall()]
                
                if not symbols:
                    continue
                
                freq_stats = {
                    'stock_count': len(symbols),
                    'stocks': [],
                    'total_records': 0,
                    'min_date': None,
                    'max_date': None
                }
                
                # 对每只股票进行统计
                for symbol in symbols:
                    stock_query = f"""
                    SELECT 
                        COUNT(*) as record_count,
                        MIN(datetime) as min_date,
                        MAX(datetime) as max_date
                    FROM dbbardata 
                    WHERE symbol = '{symbol}' AND `interval` = '{interval}'
                    """
                    
                    cursor.execute(stock_query)
                    result = cursor.fetchone()
                    if result:
                        record_count, min_date, max_date = result
                        
                        if min_date and max_date:
                            # 处理混合格式的 datetime
                            try:
                                # 尝试解析不同格式
                                if ' ' in min_date:
                                    # 格式: '2024-04-10 20240410093500000'
                                    date_part, time_part = min_date.split(' ')
                                    # 只取日期部分
                                    min_date = pd.to_datetime(date_part)
                                    max_date = pd.to_datetime(max_date.split(' ')[0])
                                else:
                                    # 其他格式
                                    min_date = pd.to_datetime(min_date)
                                    max_date = pd.to_datetime(max_date)
                            except Exception as e:
                                logger.warning(f"解析日期失败 {symbol}: {e}")
                                continue
                            
                            stock_info = {
                                'code': symbol,
                                'record_count': record_count,
                                'min_date': min_date,
                                'max_date': max_date
                            }
                            
                            freq_stats['stocks'].append(stock_info)
                            freq_stats['total_records'] += record_count
                            
                            if freq_stats['min_date'] is None or min_date < freq_stats['min_date']:
                                freq_stats['min_date'] = min_date
                            if freq_stats['max_date'] is None or max_date > freq_stats['max_date']:
                                freq_stats['max_date'] = max_date
                
                if freq_stats['stocks']:
                    stats['frequencies'][interval] = freq_stats
                    stats['total_stocks'] += freq_stats['stock_count']
                    stats['total_records'] += freq_stats['total_records']
                        
    except Exception as e:
        logger.warning(f"扫描数据库失败: {e}")
    
    return stats


def generate_data_completeness_matrix(stats: Dict, sample_size: int = 50, output_path: Optional[Path] = None):
    """生成数据完整性矩阵可视化
    
    Args:
        stats: 统计信息字典
        sample_size: 随机抽样股票数量
        output_path: 输出文件路径或 BytesIO 对象
    """
    if not stats['frequencies']:
        logger.warning("没有数据可供可视化")
        return
    
    # 过滤有效的频率数据
    valid_frequencies = {freq: freq_stats for freq, freq_stats in stats['frequencies'].items() 
                        if freq_stats.get('stocks')}
    
    if not valid_frequencies:
        logger.warning("没有有效的股票数据可供可视化")
        return
    
    num_freqs = len(valid_frequencies)
    fig = plt.figure(figsize=(16, 6 * num_freqs))
    gs = GridSpec(num_freqs, 1, figure=fig)
    
    for idx, (freq, freq_stats) in enumerate(valid_frequencies.items()):
        if not freq_stats['stocks']:
            continue
        
        stocks = freq_stats['stocks']
        
        # 如果股票数超过 sample_size，则随机抽样
        if len(stocks) > sample_size:
            sampled_stocks = random.sample(stocks, sample_size)
            logger.info(f"频率 {freq}: 从 {len(stocks)} 只股票中随机抽取 {sample_size} 只")
        else:
            sampled_stocks = stocks
        
        sampled_stocks.sort(key=lambda x: x['code'])
        
        if not sampled_stocks:
            logger.warning(f"频率 {freq}: 没有有效的采样股票")
            continue
        
        min_date = min(s['min_date'] for s in sampled_stocks)
        max_date = max(s['max_date'] for s in sampled_stocks)
        
        # 创建日期范围（按天）
        date_range = pd.date_range(start=min_date, end=max_date, freq='D')
        
        # 构建矩阵：行=股票，列=日期，值=1表示有数据
        matrix = np.zeros((len(sampled_stocks), len(date_range)))
        
        for i, stock in enumerate(sampled_stocks):
            stock_dates = set(pd.date_range(start=stock['min_date'], end=stock['max_date'], freq='D'))
            for j, date in enumerate(date_range):
                if date in stock_dates:
                    matrix[i, j] = 1
        
        ax = fig.add_subplot(gs[idx])
        im = ax.imshow(matrix, aspect='auto', cmap='RdYlGn', interpolation='nearest')
        
        # 设置Y轴标签（股票代码）
        y_step = max(1, len(sampled_stocks) // 10)
        y_positions = range(0, len(sampled_stocks), y_step)
        ax.set_yticks(list(y_positions))
        ax.set_yticklabels([sampled_stocks[i]['code'] for i in y_positions], fontsize=8)
        
        # 设置X轴标签（日期）
        x_step = max(1, len(date_range) // 15)  # 显示15个刻度
        x_positions = range(0, len(date_range), x_step)
        ax.set_xticks(list(x_positions))
        ax.set_xticklabels([date_range[i].strftime('%m-%d') for i in x_positions], 
                          rotation=45, ha='right', fontsize=8)
        
        ax.set_title(f'{freq} Data Completeness Matrix ({len(sampled_stocks)} stocks sampled)', 
                    fontsize=12, fontweight='bold')
        ax.set_xlabel('Date', fontsize=10)
        ax.set_ylabel('Stock Code', fontsize=10)
        
        plt.colorbar(im, ax=ax, label='Data (1=Present, 0=Missing)')
    
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Data completeness matrix saved to: {output_path}")
    else:
        plt.show()
    
    plt.close()


def generate_data_distribution_histogram(stats: Dict, output_path: Optional[Path] = None):
    """生成数据量分布直方图
    
    Args:
        stats: 统计信息字典
        output_path: 输出文件路径或 BytesIO 对象
    """
    if not stats['frequencies']:
        logger.warning("没有数据可供可视化")
        return
    
    # 过滤有效的频率数据
    valid_frequencies = []
    for freq, freq_stats in stats['frequencies'].items():
        if isinstance(freq_stats, dict) and freq_stats.get('stocks'):
            valid_frequencies.append((freq, freq_stats))
    
    if not valid_frequencies:
        logger.warning("没有有效的数据可供可视化")
        fig, ax = plt.subplots(1, 1, figsize=(12, 4))
        ax.text(0.5, 0.5, 'No data available for visualization', ha='center', va='center', fontsize=14)
        ax.set_title('Data Distribution', fontsize=12, fontweight='bold')
    else:
        num_freqs = len(valid_frequencies)
        fig, axes = plt.subplots(num_freqs, 1, figsize=(12, 4 * max(num_freqs, 1)))
        
        if num_freqs == 1:
            axes = [axes]
        
        for idx, (freq, freq_stats) in enumerate(valid_frequencies):
            if not isinstance(freq_stats, dict) or not freq_stats.get('stocks'):
                continue
            
            record_counts = [s['record_count'] for s in freq_stats['stocks']]
            
            n, bins, patches = axes[idx].hist(record_counts, bins=50, color='steelblue', 
                                              edgecolor='black', alpha=0.7)
            
            axes[idx].set_title(f'{freq} Data Distribution (Total: {len(record_counts)} stocks)', 
                               fontsize=12, fontweight='bold')
            axes[idx].set_xlabel('Record Count', fontsize=10)
            axes[idx].set_ylabel('Number of Stocks', fontsize=10)
            axes[idx].grid(axis='y', alpha=0.3)
            
            mean_count = np.mean(record_counts)
            median_count = np.median(record_counts)
            axes[idx].axvline(mean_count, color='red', linestyle='--', linewidth=2, 
                            label=f'Mean: {mean_count:.0f}')
            axes[idx].axvline(median_count, color='orange', linestyle='--', linewidth=2, 
                            label=f'Median: {median_count:.0f}')
            axes[idx].legend(fontsize=9)
    
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Data distribution histogram saved to: {output_path}")
    else:
        plt.show()
    
    plt.close()


def generate_update_timeliness_scatter(stats: Dict, output_path: Optional[Path] = None):
    """生成更新时效性散点图
    
    Args:
        stats: 统计信息字典
        output_path: 输出文件路径或 BytesIO 对象
    """
    if not stats['frequencies']:
        logger.warning("没有数据可供可视化")
        return
    
    # 过滤有效的频率数据
    valid_frequencies = []
    for freq, freq_stats in stats['frequencies'].items():
        if isinstance(freq_stats, dict) and freq_stats.get('stocks'):
            valid_frequencies.append((freq, freq_stats))
    
    if not valid_frequencies:
        logger.warning("没有有效的数据可供可视化")
        fig, ax = plt.subplots(1, 1, figsize=(14, 5))
        ax.text(0.5, 0.5, 'No data available for visualization', ha='center', va='center', fontsize=14)
        ax.set_title('Data Update Timeliness', fontsize=12, fontweight='bold')
    else:
        num_freqs = len(valid_frequencies)
        fig, axes = plt.subplots(num_freqs, 1, figsize=(14, 5 * max(num_freqs, 1)))
        
        if num_freqs == 1:
            axes = [axes]
        
        now = datetime.now()
        
        for idx, (freq, freq_stats) in enumerate(valid_frequencies):
            if not isinstance(freq_stats, dict) or not freq_stats.get('stocks'):
                continue
            
            stocks = freq_stats['stocks']
            
            # 准备数据
            stock_indices = list(range(len(stocks)))
            max_dates = [s['max_date'] for s in stocks]
            record_counts = [s['record_count'] for s in stocks]
            
            # 计算距今天数
            days_ago = [(now - md).days for md in max_dates]
            
            # 绘制散点图，颜色表示记录数
            scatter = axes[idx].scatter(stock_indices, days_ago, 
                                       c=record_counts, cmap='viridis', 
                                       alpha=0.6, s=50, edgecolors='black', linewidth=0.5)
            
            # 添加参考线
            axes[idx].axhline(y=7, color='red', linestyle='--', linewidth=1.5, alpha=0.8, label='7 days ago')
            axes[idx].axhline(y=30, color='orange', linestyle='--', linewidth=1.5, alpha=0.8, label='30 days ago')
            
            axes[idx].set_title(f'{freq} Data Update Timeliness (Total: {len(stocks)} stocks)', 
                               fontsize=12, fontweight='bold')
            axes[idx].set_xlabel('Stock Index', fontsize=10)
            axes[idx].set_ylabel('Days Ago', fontsize=10)
            axes[idx].grid(alpha=0.3)
            axes[idx].legend(fontsize=9)
            
            # 添加颜色条
            cbar = plt.colorbar(scatter, ax=axes[idx])
            cbar.set_label('Record Count', fontsize=10)
    
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Update timeliness scatter plot saved to: {output_path}")
    else:
        plt.show()
    
    plt.close()


def print_inspection_report(stats: Dict):
    """打印数据体检报告
    
    Args:
        stats: 统计信息字典
    """
    logger.info("=" * 80)
    logger.info("数据体检报告")
    logger.info("=" * 80)
    
    logger.info(f"\n总体统计:")
    logger.info(f"  总股票数: {stats['total_stocks']}")
    logger.info(f"  总记录数: {stats['total_records']:,}")
    
    for freq, freq_stats in stats['frequencies'].items():
        logger.info(f"\n{freq} 数据详情:")
        logger.info(f"  股票数量: {freq_stats['stock_count']}")
        logger.info(f"  总记录数: {freq_stats['total_records']:,}")
        
        if freq_stats['min_date'] and freq_stats['max_date']:
            logger.info(f"  时间范围: {freq_stats['min_date'].date()} ~ {freq_stats['max_date'].date()}")
        
        if freq_stats['stocks']:
            record_counts = [s['record_count'] for s in freq_stats['stocks']]
            logger.info(f"  平均记录数: {np.mean(record_counts):.0f}")
            logger.info(f"  中位数记录数: {np.median(record_counts):.0f}")
            logger.info(f"  最小记录数: {np.min(record_counts)}")
            logger.info(f"  最大记录数: {np.max(record_counts)}")
            
            now = datetime.now()
            days_ago = [(now - s['max_date']).days for s in freq_stats['stocks']]
            
            outdated_7 = sum(1 for d in days_ago if d > 7)
            outdated_30 = sum(1 for d in days_ago if d > 30)
            
            logger.info(f"  超过7天未更新: {outdated_7} 只股票")
            logger.info(f"  超过30天未更新: {outdated_30} 只股票")
            
            latest_stocks = sorted(freq_stats['stocks'], key=lambda x: x['max_date'], reverse=True)[:5]
            logger.info(f"\n  最新更新的5只股票:")
            for stock in latest_stocks:
                logger.info(f"    {stock['code']}: {stock['max_date'].date()} ({stock['record_count']} 条记录)")
    
    logger.info("\n" + "=" * 80)


def run_data_inspection(frequency: Optional[str] = None):
    """运行数据体检
    
    Args:
        frequency: 指定频率，如 '5m'、'1d'，None 表示检查所有频率
    """
    logger.info("=" * 80)
    logger.info("开始数据体检")
    logger.info("=" * 80)
    
    parquet_root = _get_parquet_root()
    logger.info(f"数据目录: {parquet_root}")
    
    if frequency:
        logger.info(f"检查频率: {frequency}")
    else:
        logger.info("检查频率: 所有频率")
    
    logger.info("\n正在扫描数据...")
    
    # 只扫描 Parquet 目录
    stats = scan_parquet_data(frequency)
    
    if not stats['frequencies']:
        logger.warning("Parquet 目录为空，请先运行数据转换或下载任务")
        logger.warning(f"当前 Parquet 目录: {parquet_root}")
        return
    
    print_inspection_report(stats)
    
    output_dir = Path(__file__).parent.parent / 'data'
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("\n正在生成可视化图表...")
    
    matrix_path = output_dir / 'data_completeness_matrix.png'
    generate_data_completeness_matrix(stats, sample_size=50, output_path=matrix_path)
    
    hist_path = output_dir / 'data_distribution_histogram.png'
    generate_data_distribution_histogram(stats, output_path=hist_path)
    
    scatter_path = output_dir / 'data_timeliness_scatter.png'
    generate_update_timeliness_scatter(stats, output_path=scatter_path)
    
    combined_path = output_dir / 'data_report.png'
    logger.info(f"\n所有图表已生成:")
    logger.info(f"  1. 数据完整性矩阵: {matrix_path}")
    logger.info(f"  2. 数据量分布直方图: {hist_path}")
    logger.info(f"  3. 更新时效性散点图: {scatter_path}")
    
    logger.info("\n" + "=" * 80)
    logger.info("数据体检完成")
    logger.info("=" * 80)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='智能数据下载与迁移脚本（Baostock 版本）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 仅迁移日线数据
  python scripts/sync_market_data.py
  
  # 迁移日线 + 下载最近 30 天的 5 分钟数据
  python scripts/sync_market_data.py --intraday_days 30
  
  # 指定线程数和数据频率
  python scripts/sync_market_data.py --intraday_days 30 --workers 10 --frequency 5
  
  # 查看统计信息
  python scripts/sync_market_data.py --stats
  
  # 重置进度
  python scripts/sync_market_data.py --reset
  
  # 数据体检模式
  python scripts/sync_market_data.py --inspect
  
  # 数据体检模式（指定频率）
  python scripts/sync_market_data.py --inspect --frequency 5m
        """
    )
    
    parser.add_argument('--intraday_days', type=int, default=0,
                        help='下载最近 N 天的分钟数据（默认 0，即跳过）')
    parser.add_argument('--old_data_path', type=str,
                        default='data/old_history',
                        help='旧日线数据路径（默认 data/old_history）')
    parser.add_argument('--old_data_format', choices=['csv', 'hdf5'], default='csv',
                        help='旧数据格式（默认 csv）')
    parser.add_argument('--workers', type=int, default=5,
                        help='并发线程数（默认 5，建议 5-10）')
    parser.add_argument('--frequency', type=str, default='5',
                        help='数据频率。下载模式: 5/15/30/60；体检模式: 1d/5m/15m/30m/60m 或 all（默认 5）')
    parser.add_argument('--filter_st', action='store_true', default=True,
                        help='过滤ST股票（默认启用）')
    parser.add_argument('--filter_b_stock', action='store_true', default=True,
                        help='过滤B股（默认启用）')
    parser.add_argument('--stats', action='store_true',
                        help='查看统计信息')
    parser.add_argument('--reset', action='store_true',
                        help='重置进度文件')
    parser.add_argument('--inspect', action='store_true',
                        help='启动数据体检模式，扫描数据完整性并生成可视化报告')
    
    args = parser.parse_args()
    
    setup_file_logger()
    
    # Log current data source configuration
    config = CONFIG['default']
    logger.info("=" * 60)
    logger.info("数据同步配置信息")
    logger.info("=" * 60)
    logger.info(f"数据源类型 (DATA_SOURCE): {config.DATA_SOURCE}")
    logger.info(f"双重写入模式 (DUAL_WRITE_ENABLED): {config.DUAL_WRITE_ENABLED}")
    logger.info(f"Parquet 数据路径: {config.DATA_PATH_PARQUET}")
    logger.info(f"HDF5 数据路径: {config.DATA_PATH_HDF5}")
    logger.info("=" * 60)
    
    if args.inspect:
        freq = None if args.frequency == 'all' else args.frequency
        if freq and freq in ['5', '15', '30', '60']:
            freq = freq + 'm'
        run_data_inspection(frequency=freq)
        return
    
    if args.stats:
        show_stats()
        return
    
    if args.reset:
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
            logger.info("已重置进度文件")
        return
    
    progress = load_progress()
    progress['last_run'] = datetime.now().isoformat()
    save_progress(progress)
    
    parquet_root = _get_parquet_root()
    old_data_path = Path(args.old_data_path)
    if not old_data_path.is_absolute():
        old_data_path = Path(__file__).parent.parent / old_data_path
    
    migrate_success, migrate_failed = 0, 0
    if old_data_path.exists():
        if args.old_data_format == 'csv':
            migrate_success, migrate_failed = migrate_daily_data_from_csv(
                old_data_path, parquet_root, progress
            )
        else:
            migrate_success, migrate_failed = migrate_daily_data_from_hdf5(
                old_data_path, parquet_root, progress
            )
    else:
        logger.info(f"旧数据路径不存在，跳过日线迁移: {old_data_path}")
    
    intraday_success, intraday_failed = 0, 0
    if args.intraday_days > 0:
        logger.info("正在从 Baostock 获取股票列表...")
        stocks = get_all_stocks_baostock(
            filter_st=args.filter_st,
            filter_b_stock=args.filter_b_stock
        )
        
        if stocks:
            stock_list = [s['symbol'] for s in stocks]
            logger.info(f"获取到 {len(stock_list)} 只股票")
            
            intraday_success, intraday_failed = update_intraday_data_multithread(
                stock_list,
                args.intraday_days,
                args.frequency,
                args.workers,
                progress
            )
        else:
            logger.error("无法获取股票列表")
    
    logger.info("=" * 60)
    logger.info("全部任务完成！")
    logger.info(f"日线迁移: 成功 {migrate_success}, 失败 {migrate_failed}")
    logger.info(f"分钟更新: 成功 {intraday_success}, 失败 {intraday_failed}")
    logger.info("=" * 60)
    
    show_stats()


if __name__ == "__main__":
    main()
