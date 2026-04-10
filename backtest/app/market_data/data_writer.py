"""Data writer with factory pattern for multi-format support.

This module provides a unified interface for writing market data
to different storage formats (Parquet, HDF5, or both).
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any
import logging

import pandas as pd

from app.config import CONFIG
from app.utils.path_manager import path_manager

logger = logging.getLogger(__name__)


# ==================== 数据写入器抽象接口 ====================
class DataWriter(ABC):
    """Abstract base class for data writers."""
    
    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        # Ensure all necessary directories exist
        path_manager.ensure_directories()
    
    @abstractmethod
    def write_daily_data(self, df: pd.DataFrame, symbol: str) -> bool:
        """Write daily data for a symbol.
        
        Args:
            df: DataFrame with daily data
            symbol: Stock symbol
            
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def write_minute_data(self, df: pd.DataFrame, symbol: str, frequency: str) -> bool:
        """Write minute data for a symbol.
        
        Args:
            df: DataFrame with minute data
            symbol: Stock symbol
            frequency: Data frequency (e.g., '5m', '15m')
            
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def read_daily_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Read daily data for a symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            DataFrame if exists, None otherwise
        """
        pass
    
    @abstractmethod
    def read_minute_data(self, symbol: str, frequency: str) -> Optional[pd.DataFrame]:
        """Read minute data for a symbol.
        
        Args:
            symbol: Stock symbol
            frequency: Data frequency
            
        Returns:
            DataFrame if exists, None otherwise
        """
        pass


# ==================== Parquet 写入器实现 ====================
class ParquetWriter(DataWriter):
    """Parquet data writer implementation."""
    
    def write_daily_data(self, df: pd.DataFrame, symbol: str) -> bool:
        """Write daily data to Parquet."""
        try:
            code = symbol.split('.')[0] if '.' in symbol else symbol
            parquet_path = path_manager.get_parquet_path(code, frequency='1d')
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            
            df.to_parquet(parquet_path, index=False, compression='snappy')
            logger.debug(f"成功写入 Parquet 日线数据: {symbol}")
            return True
        except Exception as e:
            logger.error(f"写入 Parquet 日线数据失败 {symbol}: {e}")
            return False
    
    def write_minute_data(self, df: pd.DataFrame, symbol: str, frequency: str) -> bool:
        """Write minute data to Parquet."""
        try:
            code = symbol.split('.')[0] if '.' in symbol else symbol
            parquet_path = path_manager.get_parquet_path(code, frequency=frequency)
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            
            df.to_parquet(parquet_path, index=False, compression='snappy')
            logger.debug(f"成功写入 Parquet 分钟数据: {symbol} ({frequency})")
            return True
        except Exception as e:
            logger.error(f"写入 Parquet 分钟数据失败 {symbol}: {e}")
            return False
    
    def read_daily_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Read daily data from Parquet."""
        try:
            code = symbol.split('.')[0] if '.' in symbol else symbol
            parquet_path = path_manager.get_parquet_path(code, frequency='1d')
            
            if parquet_path.exists():
                return pd.read_parquet(parquet_path)
            return None
        except Exception as e:
            logger.warning(f"读取 Parquet 日线数据失败 {symbol}: {e}")
            return None
    
    def read_minute_data(self, symbol: str, frequency: str) -> Optional[pd.DataFrame]:
        """Read minute data from Parquet."""
        try:
            code = symbol.split('.')[0] if '.' in symbol else symbol
            parquet_path = path_manager.get_parquet_path(code, frequency=frequency)
            
            if parquet_path.exists():
                return pd.read_parquet(parquet_path)
            return None
        except Exception as e:
            logger.warning(f"读取 Parquet 分钟数据失败 {symbol}: {e}")
            return None


# ==================== HDF5 写入器实现 ====================
class Hdf5Writer(DataWriter):
    """HDF5 data writer implementation using h5py."""
    
    FILE_MAPPING = {
        '1d': 'stocks.h5',
        '5m': 'stocks_5m.h5',
        '15m': 'stocks_15m.h5',
        '30m': 'stocks_30m.h5',
        '60m': 'stocks_60m.h5',
    }
    
    def __init__(self, base_path: Path):
        super().__init__(base_path)
        try:
            import h5py
            self.h5py = h5py
        except ImportError:
            self.h5py = None
            logger.warning("h5py 未安装，HDF5 写入功能不可用")
    
    def _get_hdf5_path(self, frequency: str) -> Path:
        """Get HDF5 file path for frequency."""
        # For HDF5, we use the same file for all frequencies
        return path_manager.get_hdf5_path('stock')
    
    def write_daily_data(self, df: pd.DataFrame, symbol: str) -> bool:
        """Write daily data to HDF5."""
        if self.h5py is None:
            logger.error("h5py 未安装，无法写入 HDF5")
            return False
        
        try:
            h5_path = self._get_hdf5_path('1d')
            h5_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Use symbol as the key in HDF5
            key = symbol.replace('.', '_')
            
            with pd.HDFStore(str(h5_path), mode='a', complevel=9, complib='blosc') as store:
                store.put(key, df, format='table', data_columns=True)
            
            logger.debug(f"成功写入 HDF5 日线数据: {symbol}")
            return True
        except Exception as e:
            logger.error(f"写入 HDF5 日线数据失败 {symbol}: {e}")
            return False
    
    def write_minute_data(self, df: pd.DataFrame, symbol: str, frequency: str) -> bool:
        """Write minute data to HDF5."""
        if self.h5py is None:
            logger.error("h5py 未安装，无法写入 HDF5")
            return False
        
        try:
            h5_path = self._get_hdf5_path(frequency)
            h5_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Use symbol as the key in HDF5
            key = symbol.replace('.', '_')
            
            with pd.HDFStore(str(h5_path), mode='a', complevel=9, complib='blosc') as store:
                store.put(key, df, format='table', data_columns=True)
            
            logger.debug(f"成功写入 HDF5 分钟数据: {symbol} ({frequency})")
            return True
        except Exception as e:
            logger.error(f"写入 HDF5 分钟数据失败 {symbol}: {e}")
            return False
    
    def read_daily_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Read daily data from HDF5."""
        if self.h5py is None:
            return None
        
        try:
            h5_path = self._get_hdf5_path('1d')
            if not h5_path.exists():
                return None
            
            key = symbol.replace('.', '_')
            with pd.HDFStore(str(h5_path), mode='r') as store:
                if key in store:
                    return store[key]
            return None
        except Exception as e:
            logger.warning(f"读取 HDF5 日线数据失败 {symbol}: {e}")
            return None
    
    def read_minute_data(self, symbol: str, frequency: str) -> Optional[pd.DataFrame]:
        """Read minute data from HDF5."""
        if self.h5py is None:
            return None
        
        try:
            h5_path = self._get_hdf5_path(frequency)
            if not h5_path.exists():
                return None
            
            key = symbol.replace('.', '_')
            with pd.HDFStore(str(h5_path), mode='r') as store:
                if key in store:
                    return store[key]
            return None
        except Exception as e:
            logger.warning(f"读取 HDF5 分钟数据失败 {symbol}: {e}")
            return None


# ==================== 双重写入器实现 ====================
class DualWriter(DataWriter):
    """Dual writer that writes to both Parquet and HDF5."""
    
    def __init__(self, parquet_path: Path, hdf5_path: Path):
        self.parquet_writer = ParquetWriter(parquet_path)
        self.hdf5_writer = Hdf5Writer(hdf5_path)
    
    def write_daily_data(self, df: pd.DataFrame, symbol: str) -> bool:
        """Write daily data to both formats."""
        parquet_ok = self.parquet_writer.write_daily_data(df, symbol)
        hdf5_ok = self.hdf5_writer.write_daily_data(df, symbol)
        return parquet_ok or hdf5_ok  # Return True if at least one succeeds
    
    def write_minute_data(self, df: pd.DataFrame, symbol: str, frequency: str) -> bool:
        """Write minute data to both formats."""
        parquet_ok = self.parquet_writer.write_minute_data(df, symbol, frequency)
        hdf5_ok = self.hdf5_writer.write_minute_data(df, symbol, frequency)
        return parquet_ok or hdf5_ok
    
    def read_daily_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Read daily data (prefer Parquet)."""
        df = self.parquet_writer.read_daily_data(symbol)
        if df is not None:
            return df
        return self.hdf5_writer.read_daily_data(symbol)
    
    def read_minute_data(self, symbol: str, frequency: str) -> Optional[pd.DataFrame]:
        """Read minute data (prefer Parquet)."""
        df = self.parquet_writer.read_minute_data(symbol, frequency)
        if df is not None:
            return df
        return self.hdf5_writer.read_minute_data(symbol, frequency)


# ==================== 数据写入器工厂 ====================
class DataWriterFactory:
    """Factory for creating data writer instances."""
    
    _writer_map = {
        'parquet': ParquetWriter,
        'hdf5': Hdf5Writer,
        'dual': DualWriter,
    }
    
    @classmethod
    def create(cls, writer_type: Optional[str] = None) -> DataWriter:
        """Create a data writer instance based on configuration.
        
        Args:
            writer_type: Optional explicit writer type ('parquet', 'hdf5', 'dual')
                        If None, uses config settings
            
        Returns:
            DataWriter instance
        """
        config = CONFIG['default']
        
        # Determine writer type
        if writer_type is None:
            if config.DUAL_WRITE_ENABLED:
                writer_type = 'dual'
            else:
                writer_type = config.DATA_SOURCE
        
        writer_type = writer_type.lower()
        
        if writer_type == 'dual':
            return DualWriter(
                Path(config.DATA_PATH_PARQUET),
                Path(config.DATA_PATH_HDF5)
            )
        elif writer_type == 'parquet':
            return ParquetWriter(Path(config.DATA_PATH_PARQUET))
        elif writer_type == 'hdf5':
            return Hdf5Writer(Path(config.DATA_PATH_HDF5))
        else:
            raise ValueError(f"Unknown writer type: {writer_type}. "
                           f"Supported types: {list(cls._writer_map.keys())}")
    
    @classmethod
    def register(cls, name: str, writer_class: type):
        """Register a new writer type.
        
        Args:
            name: Name of the writer
            writer_class: Class implementing DataWriter interface
        """
        cls._writer_map[name.lower()] = writer_class
    
    @classmethod
    def get_supported_writers(cls) -> list:
        """Get list of supported writer types."""
        return list(cls._writer_map.keys())


# ==================== 全局写入器实例 ====================
_writer_instance: Optional[DataWriter] = None


def get_data_writer() -> DataWriter:
    """Get the configured data writer instance.
    
    Returns:
        DataWriter instance based on config
    """
    global _writer_instance
    if _writer_instance is None:
        _writer_instance = DataWriterFactory.create()
    return _writer_instance


def reload_data_writer():
    """Reload data writer from configuration.
    
    Call this after config changes to create a new writer instance.
    """
    global _writer_instance
    _writer_instance = None
