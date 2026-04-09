"""Parquet 数据源模块

提供从 Parquet 文件读取数据的功能，替代 RQAlpha 默认的 HDF5 Bundle。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Union, Tuple
from functools import lru_cache

import numpy as np
import pandas as pd

from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.const import INSTRUMENT_TYPE, MARKET
from rqalpha.model.instrument import Instrument
from rqalpha.utils.datetime_func import convert_date_to_int

from app.database import get_db_connection

logger = logging.getLogger(__name__)


class ParquetDataSource(BaseDataSource):
    """基于 Parquet 文件的数据源
    
    从 Parquet 文件读取行情数据，替代 RQAlpha 默认的 HDF5 Bundle。
    """
    
    def __init__(self, base_config):
        """初始化 Parquet 数据源
        
        Args:
            base_config: RQAlpha 基础配置对象
        """
        logger.info("=" * 60)
        logger.info("ParquetDataSource.__init__ 被调用")
        logger.info(f"base_config: {base_config}")
        logger.info(f"data_bundle_path: {getattr(base_config, 'data_bundle_path', None)}")
        logger.info("=" * 60)
        
        # 不调用父类初始化，因为我们需要完全自定义数据加载
        # 直接初始化我们需要的属性
        self._init_parquet_data_source(base_config)
    
    def _init_parquet_data_source(self, base_config):
        """初始化 Parquet 数据源（不加载 HDF5 bundle）"""
        # 获取配置
        self._parquet_root = Path(os.environ.get(
            'PARQUET_ROOT_DIR',
            base_config.data_bundle_path
        ))
        
        if not self._parquet_root.exists():
            raise RuntimeError(f'Parquet root path {self._parquet_root} does not exist')
        
        logger.info(f"初始化 Parquet 数据源: {self._parquet_root}")
        
        # 数据缓存：{order_book_id: DataFrame}
        self._data_cache: Dict[str, pd.DataFrame] = {}
        
        # 合约信息缓存：{order_book_id: Instrument}
        self._instruments: Dict[str, Instrument] = {}
        
        # 交易日历缓存
        self._trading_dates: Optional[pd.DatetimeIndex] = None
        
        # 初始化合约信息（从数据库或文件加载）
        self._init_instruments()
        
        # 初始化交易日历
        self._init_trading_calendar()
        
        # 初始化父类需要的属性（避免 RQAlpha 内部错误）
        # 这些属性是 BaseDataSource 需要的，但我们不使用它们
        self._future_info_store = None
        self._yield_curve = None
        self._share_transformation = None
        self._suspend_days = []
        self._st_stock_days = None
        self._ins_id_or_sym_type_map = {}
        self._day_bar_stores = {}
        self._dividend_stores = {}
        self._split_stores = {}
        self._calendar_stores = {}
        self._ex_factor_stores = {}
        self._id_instrument_map = {}
        self._sym_instrument_map = {}
        self._id_or_sym_instrument_map = {}
        self._grouped_instruments = {}
    
    def _init_instruments(self):
        """初始化合约信息"""
        logger.info("初始化合约信息...")
        
        # 从 instruments.parquet 文件加载合约信息
        try:
            instruments_path = self._parquet_root / 'instruments.parquet'
            
            if instruments_path.exists():
                logger.info(f"从文件加载合约信息: {instruments_path}")
                self._load_instruments_from_parquet(instruments_path)
            else:
                logger.warning(f"合约信息文件不存在: {instruments_path}")
                logger.info("从 Parquet 文件推断合约信息")
                self._infer_instruments_from_parquet()
                
        except Exception as e:
            logger.error(f"加载合约信息失败: {e}", exc_info=True)
            logger.info("回退到从 Parquet 文件推断合约信息")
            self._infer_instruments_from_parquet()
    
    def _load_instruments_from_parquet(self, instruments_path: Path):
        """从 Parquet 文件加载合约信息
        
        Args:
            instruments_path: instruments.parquet 文件路径
        """
        logger.info(f"加载合约信息文件: {instruments_path}")
        
        # 读取 Parquet 文件
        df = pd.read_parquet(instruments_path)
        logger.info(f"读取到 {len(df)} 条合约记录")
        
        # 转换为 Instrument 对象
        for _, row in df.iterrows():
            try:
                order_book_id = row['order_book_id']
                
                # 创建 Instrument 对象
                # Instrument 需要一个字典作为参数，日期需要是字符串格式
                instrument_dict = {
                    'order_book_id': order_book_id,
                    'symbol': row['symbol'],
                    'instrument_type': row['board_type'],
                    'exchange': order_book_id.split('.')[1],  # 'XSHG' or 'XSHE'
                    'listed_date': str(row['listed_date']),  # 字符串格式
                    'de_listed_date': str(row['de_listed_date']),  # 字符串格式
                    'tick_size': row['tick_size'],
                    'margin_rate': row['margin_rate'],
                    'commission_rate': row['commission_rate'],
                    'frozen_days': row['frozen_days'],
                }
                
                instrument = Instrument(instrument_dict, market=MARKET.CN)
                
                self._instruments[order_book_id] = instrument
                
            except Exception as e:
                logger.warning(f"创建合约 {row.get('order_book_id', 'unknown')} 失败: {e}")
                continue
        
        logger.info(f"成功创建 {len(self._instruments)} 个合约")
    
    def _infer_instruments_from_parquet(self):
        """从 Parquet 文件推断合约信息（备用方案）"""
        try:
            parquet_1m_dir = self._parquet_root / '1m'
            logger.info(f"扫描 Parquet 目录: {parquet_1m_dir}")
            
            if parquet_1m_dir.exists():
                parquet_files = list(parquet_1m_dir.glob('*.parquet'))
                logger.info(f"找到 {len(parquet_files)} 个 Parquet 文件")
                
                for parquet_file in parquet_files:
                    code = parquet_file.stem
                    logger.info(f"处理文件: {parquet_file.name}, 代码: {code}")
                    
                    # 推断交易所
                    if code.startswith('6'):
                        exchange = MARKET.SH
                        order_book_id = f"{code}.XSHG"
                    else:
                        exchange = MARKET.SZ
                        order_book_id = f"{code}.XSHE"
                    
                    # 创建 Instrument 对象
                    instrument = Instrument(
                        order_book_id=order_book_id,
                        symbol=code,
                        instrument_type=INSTRUMENT_TYPE.CS,
                        exchange=exchange,
                        listed_date=date(2000, 1, 1),  # 临时使用一个默认日期
                        de_listed_date=date(2030, 12, 31),  # 临时使用一个默认日期
                    )
                    
                    self._instruments[order_book_id] = instrument
                    logger.info(f"创建合约: {order_book_id}")
                
                logger.info(f"共创建 {len(self._instruments)} 个合约")
            else:
                logger.warning(f"Parquet 目录不存在: {parquet_1m_dir}")
        except Exception as e:
            logger.error(f"从 Parquet 文件推断合约信息失败: {e}", exc_info=True)
    
    def _init_trading_calendar(self):
        """初始化交易日历"""
        # TODO: 从文件或数据库加载交易日历
        # 这里暂时使用简化版本
        logger.info("初始化交易日历...")
        
        # 尝试从 Parquet 文件推断交易日历
        try:
            # 读取一个股票的数据来推断交易日
            sample_file = self._parquet_root / '1m' / '000001.parquet'
            if sample_file.exists():
                df = pd.read_parquet(sample_file)
                if 'datetime' in df.columns:
                    dates = pd.to_datetime(df['datetime']).dt.date.unique()
                    self._trading_dates = pd.DatetimeIndex(dates)
                    logger.info(f"从样本文件推断交易日历: {len(self._trading_dates)} 个交易日")
        except Exception as e:
            logger.warning(f"推断交易日历失败: {e}")
    
    def _load_parquet_data(
        self, 
        order_book_id: str, 
        frequency: str = '1m',
        columns: Optional[List[str]] = None
    ) -> Optional[pd.DataFrame]:
        """加载 Parquet 数据文件
        
        Args:
            order_book_id: 合约代码（如 '000001.XSHE'）
            frequency: 数据频率（如 '1m', '1d'）
            columns: 需要读取的列名列表（标准化后的列名），为 None 时读取所有列
            
        Returns:
            DataFrame 或 None
        """
        # 构建缓存键（包含列信息）
        columns_key = ','.join(sorted(columns)) if columns else 'all'
        cache_key = f"{order_book_id}_{frequency}_{columns_key}"
        
        # 检查缓存
        if cache_key in self._data_cache:
            logger.debug(f"从缓存返回数据: {cache_key}")
            return self._data_cache[cache_key]
        
        # 提取股票代码（去掉交易所后缀）
        code = order_book_id.split('.')[0] if '.' in order_book_id else order_book_id
        
        # 构建文件路径
        # 对于日线数据，如果没有日线文件，则从 1 分钟数据聚合
        if frequency == '1d':
            parquet_path = self._parquet_root / '1d' / f'{code}.parquet'
            if not parquet_path.exists():
                # 从 1 分钟数据聚合日线
                logger.info(f"日线数据不存在，从 1 分钟数据聚合: {order_book_id}")
                df_1m = self._load_parquet_data(order_book_id, '1m', columns=None)  # 聚合需要所有列
                if df_1m is not None:
                    df = self._aggregate_to_daily(df_1m)
                    if df is not None:
                        # 如果只需要部分列，进行筛选
                        if columns:
                            available_columns = [col for col in columns if col in df.columns]
                            if available_columns:
                                df = df[available_columns]
                        
                        # 缓存日线数据
                        self._data_cache[cache_key] = df
                        return df
                return None
        else:
            parquet_path = self._parquet_root / frequency / f'{code}.parquet'
        
        if not parquet_path.exists():
            logger.warning(f"Parquet 文件不存在: {parquet_path}")
            return None
        
        try:
            # 读取 Parquet 文件
            # 注意：由于列名在文件中可能未标准化，我们需要先读取所有列，然后筛选
            df = pd.read_parquet(parquet_path)
            
            # 标准化列名
            df = self._normalize_columns(df)
            
            # 如果只需要部分列，进行筛选
            if columns:
                available_columns = [col for col in columns if col in df.columns]
                if available_columns:
                    df = df[available_columns]
                    logger.debug(f"筛选列: {available_columns}")
            
            # 确保 datetime 列是 datetime 类型
            if 'datetime' in df.columns:
                df['datetime'] = pd.to_datetime(df['datetime'])
                df = df.sort_values('datetime').reset_index(drop=True)
            
            # 缓存数据
            self._data_cache[cache_key] = df
            
            logger.info(f"加载 Parquet 数据: {parquet_path}, {len(df)} 条记录, 列: {list(df.columns)}")
            return df
            
        except Exception as e:
            logger.error(f"读取 Parquet 文件失败: {e}")
            return None
    
    def _aggregate_to_daily(self, df_1m: pd.DataFrame) -> Optional[pd.DataFrame]:
        """将 1 分钟数据聚合为日线数据
        
        Args:
            df_1m: 1 分钟数据 DataFrame
            
        Returns:
            日线数据 DataFrame
        """
        try:
            if df_1m.empty or 'datetime' not in df_1m.columns:
                return None
            
            # 提取日期
            df_1m['date'] = pd.to_datetime(df_1m['datetime']).dt.date
            
            # 按日期聚合
            daily_data = []
            for date, group in df_1m.groupby('date'):
                daily_bar = {
                    'datetime': pd.Timestamp(date),
                    'open': group['open'].iloc[0],
                    'high': group['high'].max(),
                    'low': group['low'].min(),
                    'close': group['close'].iloc[-1],
                    'volume': group['volume'].sum(),
                }
                if 'total_turnover' in group.columns:
                    daily_bar['total_turnover'] = group['total_turnover'].sum()
                daily_data.append(daily_bar)
            
            df_daily = pd.DataFrame(daily_data)
            df_daily = df_daily.sort_values('datetime').reset_index(drop=True)
            
            logger.info(f"聚合日线数据: {len(df_daily)} 条记录")
            return df_daily
            
        except Exception as e:
            logger.error(f"聚合日线数据失败: {e}")
            return None
    
    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化列名，使其符合 RQAlpha 标准
        
        RQAlpha 标准列名：datetime, open, high, low, close, volume
        """
        column_mapping = {
            'open_price': 'open',
            'high_price': 'high',
            'low_price': 'low',
            'close_price': 'close',
            'turnover': 'total_turnover',
        }
        
        df = df.rename(columns=column_mapping)
        
        # 确保必需的列存在
        required_columns = ['datetime', 'open', 'high', 'low', 'close', 'volume']
        for col in required_columns:
            if col not in df.columns:
                logger.warning(f"缺少必需的列: {col}")
                if col != 'datetime':
                    df[col] = 0.0
        
        return df
    
    def history_bars(
        self,
        instrument: Instrument,
        bar_count: Optional[int],
        frequency: str,
        fields: Union[str, List[str], None],
        dt: datetime,
        skip_suspended: bool = True,
        include_now: bool = False,
        adjust_type: str = 'pre',
        adjust_orig: Optional[datetime] = None
    ) -> Optional[np.ndarray]:
        """获取历史K线数据
        
        Args:
            instrument: 合约对象
            bar_count: K线数量
            frequency: 数据频率（如 '1m', '1d'）
            fields: 需要的字段
            dt: 当前时间
            skip_suspended: 是否跳过停牌
            include_now: 是否包含当前时间
            adjust_type: 复权类型
            adjust_orig: 复权基准时间
            
        Returns:
            numpy.ndarray 或 None
        """
        try:
            # 加载数据
            df = self._load_parquet_data(instrument.order_book_id, frequency)
            
            if df is None or df.empty:
                logger.warning(f"无法加载合约 {instrument.order_book_id} 的数据")
                return None
            
            # 过滤时间范围
            if 'datetime' in df.columns:
                # 转换 dt 为 datetime 类型
                if isinstance(dt, datetime):
                    dt_pd = pd.Timestamp(dt)
                else:
                    dt_pd = pd.Timestamp(dt)
                
                # 过滤数据
                if include_now:
                    mask = df['datetime'] <= dt_pd
                else:
                    mask = df['datetime'] < dt_pd
                
                df_filtered = df[mask].copy()
                
                # 获取最近的 bar_count 条数据
                if bar_count is not None and bar_count > 0:
                    df_filtered = df_filtered.tail(bar_count)
                
                if df_filtered.empty:
                    logger.warning(f"过滤后无数据: {instrument.order_book_id}, dt={dt}")
                    return None
                
                # 选择字段
                if fields is None:
                    fields = ['datetime', 'open', 'high', 'low', 'close', 'volume', 'total_turnover']
                elif isinstance(fields, str):
                    fields = [fields]
                
                # 确保字段存在
                available_fields = [f for f in fields if f in df_filtered.columns]
                if not available_fields:
                    logger.warning(f"没有可用的字段: {fields}")
                    return None
                
                df_result = df_filtered[available_fields].copy()
                
                # 转换 datetime 列为整数格式（RQAlpha 要求）
                if 'datetime' in df_result.columns:
                    df_result['datetime'] = df_result['datetime'].apply(
                        lambda x: convert_date_to_int(x.to_pydatetime())
                    )
                
                # 转换为 numpy 数组
                # RQAlpha 需要结构化数组
                dtype = [(f, 'f8') if f != 'datetime' else (f, 'i8') for f in available_fields]
                result = np.array([tuple(row) for row in df_result.values], dtype=dtype)
                
                return result
                
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return None
    
    def get_bar(self, instrument, dt, frequency):
        """获取单根K线
        
        Args:
            instrument: 合约对象
            dt: 时间
            frequency: 数据频率
            
        Returns:
            BarObject 或 None
        """
        # 简化实现：调用 history_bars 获取最后一根K线
        bars = self.history_bars(instrument, 1, frequency, None, dt, include_now=True)
        if bars is not None and len(bars) > 0:
            # TODO: 转换为 BarObject
            return bars[-1]
        return None
    
    def get_instruments(self, id_or_syms: Optional[List[str]] = None) -> List[Instrument]:
        """获取合约列表
        
        Args:
            id_or_syms: 合约代码列表，为 None 时返回所有合约
            
        Returns:
            合约列表
        """
        # TODO: 实现从数据库或文件加载合约信息
        # 这里暂时返回空列表
        if id_or_syms is None:
            return list(self._instruments.values())
        
        # 返回指定的合约
        result = []
        for id_or_sym in id_or_syms:
            if id_or_sym in self._instruments:
                result.append(self._instruments[id_or_sym])
        
        return result
    
    def is_suspended(self, instrument: Instrument, dt: datetime) -> bool:
        """判断合约是否停牌
        
        Args:
            instrument: 合约对象
            dt: 时间
            
        Returns:
            是否停牌
        """
        # 简化实现：检查是否有数据
        df = self._load_parquet_data(instrument.order_book_id, '1d')
        if df is None:
            return True
        
        # 检查是否有当天的数据
        if 'datetime' in df.columns:
            dt_date = dt.date() if isinstance(dt, datetime) else dt
            has_data = any(
                pd.to_datetime(d).date() == dt_date 
                for d in df['datetime']
            )
            return not has_data
        
        return False
    
    def get_trading_calendars(self) -> Dict:
        """获取交易日历
        
        Returns:
            交易日历字典
        """
        from rqalpha.const import TRADING_CALENDAR_TYPE
        
        if self._trading_dates is not None:
            return {TRADING_CALENDAR_TYPE.CN_STOCK: self._trading_dates}
        
        return {}
    
    def available_data_range(self, frequency: str) -> Tuple[date, date]:
        """获取可用数据的时间范围
        
        优化：使用 Parquet 元数据快速获取时间范围，避免读取整个文件。
        
        Args:
            frequency: 数据频率
            
        Returns:
            (开始日期, 结束日期)
        """
        logger.info(f"available_data_range 被调用，frequency={frequency}")
        
        # 如果有交易日历，返回第一个和最后一个交易日
        if self._trading_dates is not None and len(self._trading_dates) > 0:
            start_date = self._trading_dates[0].date()
            end_date = self._trading_dates[-1].date()
            logger.info(f"返回交易日历范围: {start_date} ~ {end_date}")
            return start_date, end_date
        
        # 否则从 Parquet 文件推断时间范围
        try:
            # 尝试从第一个可用的 Parquet 文件获取时间范围
            parquet_dir = self._parquet_root / frequency
            if parquet_dir.exists():
                parquet_files = list(parquet_dir.glob('*.parquet'))
                if parquet_files:
                    # 使用第一个文件推断时间范围
                    sample_file = parquet_files[0]
                    start_date, end_date = self._get_parquet_time_range(sample_file)
                    if start_date and end_date:
                        logger.info(f"从 Parquet 文件推断时间范围: {start_date} ~ {end_date}")
                        return start_date, end_date
        except Exception as e:
            logger.warning(f"从 Parquet 文件推断时间范围失败: {e}")
        
        # 返回默认范围
        from datetime import date as date_type
        today = date_type.today()
        default_start = today - timedelta(days=30)
        logger.info(f"返回默认范围: {default_start} ~ {today}")
        return default_start, today
    
    def _get_parquet_time_range(self, parquet_path: Path) -> Tuple[Optional[date], Optional[date]]:
        """从 Parquet 文件快速获取时间范围
        
        使用 Parquet 元数据或只读取 datetime 列，避免读取整个文件。
        
        Args:
            parquet_path: Parquet 文件路径
            
        Returns:
            (开始日期, 结束日期) 或 (None, None)
        """
        try:
            # 方法1: 尝试只读取 datetime 列（最快的元数据读取方式）
            df = pd.read_parquet(parquet_path, columns=['datetime'])
            
            if df.empty or 'datetime' not in df.columns:
                return None, None
            
            # 转换为 datetime 类型
            df['datetime'] = pd.to_datetime(df['datetime'])
            
            # 获取最小和最大日期
            min_datetime = df['datetime'].min()
            max_datetime = df['datetime'].max()
            
            start_date = min_datetime.date()
            end_date = max_datetime.date()
            
            return start_date, end_date
            
        except Exception as e:
            logger.warning(f"读取 Parquet 文件时间范围失败: {e}")
            return None, None
