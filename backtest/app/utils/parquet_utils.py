"""Parquet utility functions for safe reading and writing with date handling.

This module provides utility functions for reading and writing Parquet files
with proper date handling to ensure consistent datetime64[ns] format.
"""
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)


def read_parquet_safe(path: Path) -> Optional[pd.DataFrame]:
    """Safely read a Parquet file with automatic date handling.
    
    Args:
        path: Path to Parquet file
        
    Returns:
        DataFrame if successful, None otherwise
    """
    try:
        df = pd.read_parquet(path)
        
        # Handle datetime column if present
        if 'datetime' in df.columns:
            try:
                # Try to convert to datetime64[ns]
                df['datetime'] = pd.to_datetime(df['datetime'])
            except Exception as e:
                logger.warning(f"Failed to convert datetime column in {path}: {e}")
                # Try to extract timestamp from malformed datetime strings
                if df['datetime'].dtype == 'object':
                    try:
                        def fix_datetime(value):
                            if isinstance(value, str) and ' ' in value:
                                # Handle malformed datetime like "2026-04-03 20260403093500000"
                                parts = value.split(' ')
                                if len(parts) == 2:
                                    # Use the timestamp part
                                    return pd.to_datetime(parts[1], format='%Y%m%d%H%M%S%f')
                            return pd.to_datetime(value)
                        
                        df['datetime'] = df['datetime'].apply(fix_datetime)
                        logger.info(f"Fixed datetime column in {path}")
                    except Exception as e2:
                        logger.error(f"Failed to fix datetime column in {path}: {e2}")
                        return None
        
        return df
    except Exception as e:
        logger.error(f"Failed to read Parquet file {path}: {e}")
        return None


def write_parquet_safe(df: pd.DataFrame, path: Path, **kwargs) -> bool:
    """Safely write a DataFrame to Parquet with automatic date handling.
    
    Args:
        df: DataFrame to write
        path: Path to Parquet file
        **kwargs: Additional arguments to pass to df.to_parquet
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Create a copy to avoid modifying the original DataFrame
        df_copy = df.copy()
        
        # Ensure datetime column is in correct format
        if 'datetime' in df_copy.columns:
            # Handle mixed format dates
            if df_copy['datetime'].dtype == 'object':
                # Convert each value individually
                def fix_datetime(value):
                    if isinstance(value, str):
                        if ' ' in value:
                            # Handle malformed datetime like "2026-04-03 20260403093500000"
                            parts = value.split(' ')
                            if len(parts) == 2:
                                # Use the first part (date only)
                                return pd.to_datetime(parts[0])
                    return pd.to_datetime(value)
                
                df_copy['datetime'] = df_copy['datetime'].apply(fix_datetime)
            else:
                # Convert to datetime64[ns]
                df_copy['datetime'] = pd.to_datetime(df_copy['datetime'])
        
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to Parquet
        df_copy.to_parquet(path, **kwargs)
        logger.debug(f"Successfully wrote Parquet file: {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write Parquet file {path}: {e}")
        return False
