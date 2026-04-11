"""Bundle data analyzer with factory pattern for multi-source data support."""
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from app.database import get_db_connection
from app.config import CONFIG
from app.utils.path_manager import path_manager
from app.utils.parquet_utils import read_parquet_safe


# ==================== 数据源抽象接口 ====================
class DataSource(ABC):
    """Abstract base class for data sources."""
    
    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
    
    @abstractmethod
    def count_instruments(self, data_type: str) -> int:
        """Count instruments of given type (stock, fund, futures, index, bond).
        
        Args:
            data_type: Type of financial instrument
            
        Returns:
            Number of instruments
        """
        pass
    
    @abstractmethod
    def get_price(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Get price data for a symbol.
        
        Args:
            symbol: Instrument symbol
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            
        Returns:
            DataFrame with price data
        """
        pass
    
    @abstractmethod
    def get_all_securities(self, data_type: str) -> List[str]:
        """Get list of all securities of given type.
        
        Args:
            data_type: Type of financial instrument
            
        Returns:
            List of security symbols
        """
        pass


# ==================== HDF5 数据源实现 ====================
class Hdf5DataSource(DataSource):
    """HDF5 data source implementation using h5py."""
    
    FILE_MAPPING = {
        'stock': 'stocks.h5',
        'fund': 'funds.h5',
        'futures': 'futures.h5',
        'index': 'indexes.h5',
        'bond': 'bonds.h5',
    }
    
    def __init__(self, base_path: Path):
        super().__init__(base_path)
        try:
            import h5py
            self.h5py = h5py
        except ImportError:
            self.h5py = None
    
    def _get_file_path(self, data_type: str) -> Optional[Path]:
        """Get HDF5 file path for data type."""
        filename = self.FILE_MAPPING.get(data_type)
        if not filename:
            return None
        return self.base_path / filename
    
    def count_instruments(self, data_type: str) -> int:
        """Count instruments in HDF5 file."""
        if self.h5py is None:
            return 0
            
        file_path = self._get_file_path(data_type)
        if not file_path or not file_path.exists():
            return 0
            
        try:
            with self.h5py.File(str(file_path), 'r') as f:
                return len(list(f.keys()))
        except Exception:
            return 0
    
    def get_price(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Get price data from HDF5."""
        # Implementation for reading price data from HDF5
        # This is a placeholder - actual implementation depends on HDF5 structure
        raise NotImplementedError("HDF5 price reading not yet implemented")
    
    def get_all_securities(self, data_type: str) -> List[str]:
        """Get all securities from HDF5."""
        if self.h5py is None:
            return []
            
        file_path = self._get_file_path(data_type)
        if not file_path or not file_path.exists():
            return []
            
        try:
            with self.h5py.File(str(file_path), 'r') as f:
                return list(f.keys())
        except Exception:
            return []


# ==================== Parquet 数据源实现 ====================
class ParquetDataSource(DataSource):
    """Parquet data source implementation using pandas."""
    
    def __init__(self, base_path: Path):
        super().__init__(base_path)
    
    def _get_parquet_files(self, data_type: str) -> List[Path]:
        """Get all parquet files for data type.
        
        Tries multiple directory structures:
        1. First tries: base_path/data_type/*.parquet
        2. Then tries: base_path/*/data_type/*.parquet (for frequency subdirectories like 5m/)
        3. Finally tries: base_path/**/*.parquet (fallback to any parquet file)
        """
        # Try direct type directory
        type_dir = self.base_path / data_type
        if type_dir.exists():
            files = list(type_dir.glob('*.parquet'))
            if files:
                return files
        
        # Try frequency subdirectories (using path manager)
        for freq in ['5m', '1d']:
            freq_dir = path_manager.get_parquet_directory(frequency=freq)
            if freq_dir.exists():
                files = list(freq_dir.glob('*.parquet'))
                if files:
                    return files
        
        # Try any parquet file in any subdirectory
        all_files = list(self.base_path.rglob('*.parquet'))
        if all_files:
            return all_files
        
        return []
    
    def count_instruments(self, data_type: str) -> int:
        """Count instruments by counting parquet files."""
        files = self._get_parquet_files(data_type)
        return len(files)
    
    def get_price(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Get price data from Parquet file."""
        # Find the parquet file for this symbol
        # Try all possible directory structures
        search_patterns = [
            # Direct type directories
            f"{symbol}.parquet",
            # Frequency subdirectories
            "*/{}".format(symbol),
            "*/*/{}".format(symbol)
        ]
        
        for pattern in search_patterns:
            for parquet_file in self.base_path.rglob(pattern):
                if parquet_file.suffix == '.parquet':
                    df = read_parquet_safe(parquet_file)
                    if df is not None:
                        # Filter by date range
                        if 'date' in df.columns:
                            df['date'] = pd.to_datetime(df['date'])
                            mask = (df['date'] >= start_date) & (df['date'] <= end_date)
                            df = df[mask]
                        return df
        return pd.DataFrame()
    
    def get_all_securities(self, data_type: str) -> List[str]:
        """Get all securities from parquet filenames."""
        files = self._get_parquet_files(data_type)
        return [f.stem for f in files]


# ==================== 数据源工厂 ====================
class DataSourceFactory:
    """Factory for creating data source instances."""
    
    _source_map = {
        'hdf5': Hdf5DataSource,
        'parquet': ParquetDataSource,
    }
    
    @classmethod
    def create(cls, source_type: str, base_path: Optional[Path] = None) -> DataSource:
        """Create a data source instance.
        
        Args:
            source_type: Type of data source ('hdf5' or 'parquet')
            base_path: Optional base path override
            
        Returns:
            DataSource instance
            
        Raises:
            ValueError: If source_type is not supported
        """
        source_type = source_type.lower()
        if source_type not in cls._source_map:
            raise ValueError(f"Unknown data source type: {source_type}. "
                           f"Supported types: {list(cls._source_map.keys())}")
        
        # Get base path from config if not provided
        if base_path is None:
            config = CONFIG['default']
            if source_type == 'hdf5':
                base_path = Path(config.DATA_PATH_HDF5)
            else:
                base_path = Path(config.DATA_PATH_PARQUET)
        
        return cls._source_map[source_type](base_path)
    
    @classmethod
    def register(cls, name: str, source_class: type):
        """Register a new data source type.
        
        Args:
            name: Name of the data source
            source_class: Class implementing DataSource interface
        """
        cls._source_map[name.lower()] = source_class
    
    @classmethod
    def get_supported_sources(cls) -> List[str]:
        """Get list of supported data source types."""
        return list(cls._source_map.keys())


# ==================== 全局数据源实例 ====================
# Initialize data source based on configuration
_config = CONFIG['default']
_data_source_instance: Optional[DataSource] = None


def get_data_source() -> DataSource:
    """Get the configured data source instance.
    
    Returns:
        DataSource instance based on config.DATA_SOURCE
    """
    global _data_source_instance
    if _data_source_instance is None:
        _data_source_instance = DataSourceFactory.create(_config.DATA_SOURCE)
    return _data_source_instance


def reload_data_source():
    """Reload data source from configuration.
    
    Call this after config changes to create a new data source instance.
    """
    global _data_source_instance
    _data_source_instance = None


# ==================== Bundle 分析功能 ====================
def analyze_bundle(task_id: str, bundle_path: Path, db_config_dict: dict):
    """Analyze RQAlpha bundle data.

    Args:
        task_id: Task ID for progress updates
        bundle_path: Path to bundle directory
        db_config_dict: Serialized DatabaseConfig dict (from DatabaseConfig.to_dict()).
                        Used instead of a Path so that background threads can connect
                        to the database without a Flask application context.
    """
    from app.market_data.task_manager import get_task_manager

    tm = get_task_manager()
    tm.log(task_id, 'INFO', '开始数据分析任务')
    tm.update_progress(task_id, 0, 'analyze', '开始分析...')

    try:
        # 1. Scan files
        tm.update_progress(task_id, 10, 'analyze', '正在扫描文件...')
        file_stats = _scan_files(bundle_path)

        # 2. Parse bundle data using data source
        tm.update_progress(task_id, 30, 'analyze', '正在解析行情数据...')
        data_counts = _parse_bundle_data_with_source(tm, task_id)

        # 3. Save to database
        tm.update_progress(task_id, 90, 'analyze', '正在写入数据库...')
        _save_stats(db_config_dict, bundle_path, file_stats, data_counts)

        tm.update_progress(task_id, 100, 'analyze', '分析完成')
        tm.log(task_id, 'INFO', '数据分析任务完成')

    except Exception as e:
        tm.log(task_id, 'ERROR', f'分析失败: {str(e)}')
        raise


def ensure_bundle_analysis_task(bundle_path: Path, db_config_dict: dict | None = None) -> str | None:
    """Queue a bundle analysis task if the bundle is ready but not yet analyzed."""
    from app.market_data.task_manager import get_task_manager

    tm = get_task_manager()
    config_dict = db_config_dict or tm.db_config_dict

    if not bundle_path.exists() or not bundle_path.is_dir():
        return None

    with get_db_connection(config_dict=config_dict) as db:
        existing_task = db.fetchone(
            """SELECT task_id FROM market_data_tasks
               WHERE status IN ('pending', 'running') AND task_type = 'analyze'
               ORDER BY created_at DESC
               LIMIT 1"""
        )
        if existing_task:
            return None

        row = db.fetchone(
            "SELECT bundle_path, total_files, analyzed_at FROM market_data_stats WHERE id = 1"
        )

    if row and row.get('bundle_path') == str(bundle_path) and (row.get('total_files') or 0) > 0:
        return None

    return tm.submit_task(
        'analyze',
        analyze_bundle,
        task_args=(bundle_path, config_dict),
        source='auto',
    )


def _scan_files(bundle_path: Path) -> Dict:
    """Scan files and collect statistics."""
    total_files = 0
    total_size = 0
    last_modified = None
    files_list = []

    if not bundle_path.exists():
        return {
            'total_files': 0,
            'total_size_bytes': 0,
            'last_modified': None,
            'files': []
        }

    for file_path in bundle_path.rglob('*'):
        if file_path.is_file():
            total_files += 1
            file_size = file_path.stat().st_size
            total_size += file_size
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

            # Collect file info
            relative_path = file_path.relative_to(bundle_path)
            files_list.append({
                'name': file_path.name,
                'path': str(relative_path),
                'size': file_size,
                'modified': mtime.isoformat()
            })

            if last_modified is None or mtime > last_modified:
                last_modified = mtime

    return {
        'total_files': total_files,
        'total_size_bytes': total_size,
        'last_modified': last_modified.isoformat() if last_modified else None,
        'files': files_list
    }


def _parse_bundle_data_with_source(tm, task_id: str) -> Dict:
    """Parse bundle data using configured data source.
    
    This replaces the old hardcoded HDF5 logic with dynamic data source selection.
    """
    counts = {
        'stock_count': 0,
        'fund_count': 0,
        'futures_count': 0,
        'index_count': 0,
        'bond_count': 0
    }

    try:
        # Get data source from factory (based on config)
        data_source = get_data_source()
        
        # Stock data
        tm.update_progress(task_id, 40, 'analyze', '正在解析股票数据...')
        counts['stock_count'] = data_source.count_instruments('stock')
        
        # Fund data
        tm.update_progress(task_id, 50, 'analyze', '正在解析基金数据...')
        counts['fund_count'] = data_source.count_instruments('fund')
        
        # Futures data
        tm.update_progress(task_id, 60, 'analyze', '正在解析期货数据...')
        counts['futures_count'] = data_source.count_instruments('futures')
        
        # Index data
        tm.update_progress(task_id, 70, 'analyze', '正在解析指数数据...')
        counts['index_count'] = data_source.count_instruments('index')
        
        # Bond data
        tm.update_progress(task_id, 80, 'analyze', '正在解析债券数据...')
        counts['bond_count'] = data_source.count_instruments('bond')
        
    except Exception as e:
        # Log error but don't fail - return zero counts
        tm.log(task_id, 'WARNING', f'数据解析警告: {str(e)}')

    return counts


def _save_stats(db_config_dict: dict, bundle_path: Path, file_stats: Dict, data_counts: Dict):
    """Save statistics to database (idempotent).

    Args:
        db_config_dict: Serialized DatabaseConfig dict for background-thread connection.
        bundle_path: Path to the bundle directory.
        file_stats: File scan results from _scan_files().
        data_counts: Instrument counts from _parse_bundle_data_with_source().
    """
    stats_cols = [
        'id', 'bundle_path', 'last_modified', 'total_files', 'total_size_bytes',
        'analyzed_at', 'stock_count', 'fund_count', 'futures_count',
        'index_count', 'bond_count',
    ]
    stats_vals = (
        1,
        str(bundle_path),
        file_stats['last_modified'],
        file_stats['total_files'],
        file_stats['total_size_bytes'],
        datetime.utcnow().isoformat(),
        data_counts['stock_count'],
        data_counts['fund_count'],
        data_counts['futures_count'],
        data_counts['index_count'],
        data_counts['bond_count'],
    )

    file_rows = [
        (f['name'], f['path'], f['size'], f['modified'])
        for f in file_stats.get('files', [])
    ]

    with get_db_connection(config_dict=db_config_dict) as db:
        # Idempotent upsert for the single-row stats table
        db.replace_into('market_data_stats', stats_cols, stats_vals)

        # Refresh file records
        db.execute("DELETE FROM market_data_files")
        if file_rows:
            db.executemany(
                "INSERT INTO market_data_files (file_name, file_path, file_size, modified_at) "
                "VALUES (?, ?, ?, ?)",
                file_rows,
            )
