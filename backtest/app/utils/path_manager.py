"""Path manager for data storage."""
from pathlib import Path
from typing import Optional

from app.config import CONFIG


class PathManager:
    """Path manager for data storage."""
    
    def __init__(self):
        """Initialize path manager."""
        self.config = CONFIG['default']
        self.base_hdf5 = Path(self.config.DATA_PATH_HDF5)
        self.base_parquet = Path(self.config.DATA_PATH_PARQUET)
    
    def get_hdf5_path(self, data_type: str) -> Path:
        """Get HDF5 file path for data type.
        
        Args:
            data_type: Type of financial instrument
            
        Returns:
            Path to HDF5 file
        """
        file_map = {
            'stock': 'stocks.h5',
            'fund': 'funds.h5',
            'futures': 'futures.h5',
            'index': 'indexes.h5',
            'bond': 'bonds.h5',
        }
        filename = file_map.get(data_type, f'{data_type}.h5')
        return self.base_hdf5 / filename
    
    def get_parquet_path(self, symbol: str, data_type: str = 'stock', frequency: str = '5m') -> Path:
        """Get Parquet file path for symbol.
        
        Args:
            symbol: Instrument symbol
            data_type: Type of financial instrument (default: 'stock')
            frequency: Data frequency (default: '5m')
            
        Returns:
            Path to Parquet file
        """
        # Standardized path: data/parquet/{frequency}/{symbol}.parquet
        return self.base_parquet / frequency / f'{symbol}.parquet'
    
    def get_parquet_directory(self, frequency: str = '5m') -> Path:
        """Get Parquet directory for frequency.
        
        Args:
            frequency: Data frequency (default: '5m')
            
        Returns:
            Path to Parquet directory
        """
        return self.base_parquet / frequency
    
    def ensure_directories(self):
        """Ensure all necessary directories exist."""
        # Ensure HDF5 directory exists
        self.base_hdf5.mkdir(parents=True, exist_ok=True)
        
        # Ensure Parquet directory exists
        self.base_parquet.mkdir(parents=True, exist_ok=True)
        
        # Ensure frequency subdirectories exist
        for freq in ['5m', '1d']:
            (self.base_parquet / freq).mkdir(parents=True, exist_ok=True)


# Global path manager instance
path_manager = PathManager()
