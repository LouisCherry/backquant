"""Integration test for Parquet data source."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import numpy as np

# Add the project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import CONFIG
from app.market_data.data_writer import get_data_writer
from app.market_data.analyzer import get_data_source
from app.utils.path_manager import path_manager


class TestParquetIntegration(unittest.TestCase):
    """Integration test for Parquet data source."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a temporary directory for testing
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        
        # Save original config
        self.original_data_path_parquet = CONFIG['default'].DATA_PATH_PARQUET
        self.original_data_path_hdf5 = CONFIG['default'].DATA_PATH_HDF5
        
        # Override config for testing
        CONFIG['default'].DATA_PATH_PARQUET = str(self.test_dir / 'parquet')
        CONFIG['default'].DATA_PATH_HDF5 = str(self.test_dir / 'hdf5')
        
        # Reload path manager and data writer
        from app.utils.path_manager import path_manager
        path_manager.__init__()  # Reinitialize with new paths
        
        from app.market_data.data_writer import reload_data_writer
        reload_data_writer()
        
        from app.market_data.analyzer import reload_data_source
        reload_data_source()
    
    def tearDown(self):
        """Clean up test environment."""
        # Restore original config
        CONFIG['default'].DATA_PATH_PARQUET = self.original_data_path_parquet
        CONFIG['default'].DATA_PATH_HDF5 = self.original_data_path_hdf5
        
        # Clean up temporary directory
        self.temp_dir.cleanup()
    
    def test_write_parquet_data(self):
        """Test writing data to Parquet."""
        # Create test data
        dates = pd.date_range('2023-01-01', '2023-01-10')
        df = pd.DataFrame({
            'date': dates,
            'open': np.random.randn(10) + 100,
            'high': np.random.randn(10) + 101,
            'low': np.random.randn(10) + 99,
            'close': np.random.randn(10) + 100,
            'volume': np.random.randint(100000, 1000000, 10)
        })
        
        # Get data writer
        writer = get_data_writer()
        
        # Write data
        symbol = '600000'
        result = writer.write_minute_data(df, symbol, '5m')
        
        # Verify write success
        self.assertTrue(result)
        
        # Check if file exists
        from app.utils.path_manager import path_manager
        parquet_path = path_manager.get_parquet_path(symbol, frequency='5m')
        self.assertTrue(parquet_path.exists())
        self.assertTrue(parquet_path.suffix == '.parquet')
    
    def test_read_parquet_data(self):
        """Test reading data from Parquet."""
        # Create test data
        dates = pd.date_range('2023-01-01', '2023-01-10')
        df = pd.DataFrame({
            'date': dates,
            'open': np.random.randn(10) + 100,
            'high': np.random.randn(10) + 101,
            'low': np.random.randn(10) + 99,
            'close': np.random.randn(10) + 100,
            'volume': np.random.randint(100000, 1000000, 10)
        })
        
        # Write data
        writer = get_data_writer()
        symbol = '600001'
        writer.write_minute_data(df, symbol, '5m')
        
        # Read data back
        data_source = get_data_source()
        read_df = data_source.get_price(symbol, '2023-01-01', '2023-01-10')
        
        # Verify data integrity
        self.assertIsNotNone(read_df)
        self.assertEqual(len(read_df), len(df))
        self.assertEqual(list(read_df.columns), list(df.columns))
    
    def test_count_instruments(self):
        """Test counting instruments."""
        # Create test data for multiple symbols
        dates = pd.date_range('2023-01-01', '2023-01-10')
        writer = get_data_writer()
        
        # Write data for 3 symbols
        for i in range(3):
            symbol = f'60000{i+2}'
            df = pd.DataFrame({
                'date': dates,
                'open': np.random.randn(10) + 100,
                'high': np.random.randn(10) + 101,
                'low': np.random.randn(10) + 99,
                'close': np.random.randn(10) + 100,
                'volume': np.random.randint(100000, 1000000, 10)
            })
            writer.write_minute_data(df, symbol, '5m')
        
        # Count instruments
        data_source = get_data_source()
        count = data_source.count_instruments('stock')
        
        # Verify count is correct
        self.assertGreater(count, 0)
        self.assertEqual(count, 3)
    
    def test_get_all_securities(self):
        """Test getting all securities."""
        # Create test data for multiple symbols
        dates = pd.date_range('2023-01-01', '2023-01-10')
        writer = get_data_writer()
        symbols = ['600003', '600004', '600005']
        
        # Write data for each symbol
        for symbol in symbols:
            df = pd.DataFrame({
                'date': dates,
                'open': np.random.randn(10) + 100,
                'high': np.random.randn(10) + 101,
                'low': np.random.randn(10) + 99,
                'close': np.random.randn(10) + 100,
                'volume': np.random.randint(100000, 1000000, 10)
            })
            writer.write_minute_data(df, symbol, '5m')
        
        # Get all securities
        data_source = get_data_source()
        securities = data_source.get_all_securities('stock')
        
        # Verify securities list
        self.assertIsInstance(securities, list)
        self.assertEqual(len(securities), len(symbols))
        for symbol in symbols:
            self.assertIn(symbol, securities)


if __name__ == '__main__':
    unittest.main()
