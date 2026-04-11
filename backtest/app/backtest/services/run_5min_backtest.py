#!/usr/bin/env python3
import sys
import os
import yaml
from pathlib import Path
from datetime import datetime, timedelta, time

def get_data_source_config():
    data_source = os.environ.get('DATA_SOURCE', 'parquet')
    data_path_parquet = os.environ.get('PARQUET_ROOT_DIR', '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/data/parquet')
    data_path_hdf5 = os.environ.get('DATA_PATH_HDF5', '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/data/hdf5')
    
    print(f"[DEBUG] Got DATA_SOURCE from env: {data_source}", flush=True)
    print(f"[DEBUG] Got PARQUET_ROOT_DIR from env: {data_path_parquet}", flush=True)
    print(f"[DEBUG] Got DATA_PATH_HDF5 from env: {data_path_hdf5}", flush=True)
    
    if not data_path_parquet or data_path_parquet == '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/data/parquet':
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent))
            from app.config import CONFIG_ENV, CONFIG
            
            config_class = CONFIG.get(CONFIG_ENV, CONFIG['default'])
            data_source = getattr(config_class, 'DATA_SOURCE', data_source)
            data_path_parquet = getattr(config_class, 'DATA_PATH_PARQUET', data_path_parquet)
            data_path_hdf5 = getattr(config_class, 'DATA_PATH_HDF5', data_path_hdf5)
            
            print(f"[DEBUG] Got DATA_SOURCE from config class: {data_source}", flush=True)
            print(f"[DEBUG] Got DATA_PATH_PARQUET from config class: {data_path_parquet}", flush=True)
            print(f"[DEBUG] Got DATA_PATH_HDF5 from config class: {data_path_hdf5}", flush=True)
            
        except Exception as e:
            print(f"[DEBUG] Failed to get config from app.config: {e}", flush=True)
    
    return data_source, data_path_parquet, data_path_hdf5

def _get_5min_trading_minutes(self, trading_date):
    trading_minutes = set()
    current_dt = datetime.combine(trading_date, time(9, 35))
    am_end_dt = current_dt.replace(hour=11, minute=30)
    pm_start_dt = current_dt.replace(hour=13, minute=5)
    pm_end_dt = current_dt.replace(hour=15, minute=0)
    delta_5min = timedelta(minutes=5)
    while current_dt <= am_end_dt:
        trading_minutes.add(current_dt)
        current_dt += delta_5min
    current_dt = pm_start_dt
    while current_dt <= pm_end_dt:
        trading_minutes.add(current_dt)
        current_dt += delta_5min
    return sorted(list(trading_minutes))

def setup_5min_support():
    print("[DEBUG] Setting up 5min frequency support...")
    
    data_source, data_path_parquet, data_path_hdf5 = get_data_source_config()
    
    parquet_root = data_path_parquet
    print(f"[DEBUG] Using Parquet root path: {parquet_root}", flush=True)
    
    # 1. Monkey patch SimulationMod.parse_matching_type
    from rqalpha.mod.rqalpha_mod_sys_simulation import mod
    from rqalpha.const import MATCHING_TYPE
    
    @staticmethod
    def patched_parse_matching_type(me_str, frequency):
        if me_str is None:
            if frequency in ["1d", "1m", "5m"]:
                me_str = "current_bar"
            elif frequency == "tick":
                me_str = "last"
            else:
                raise ValueError(f"frequency only support ['1d', '1m', '5m', 'tick'], got {frequency}")
        assert isinstance(me_str, str)
        me_str = me_str.lower()
        if me_str == "current_bar":
            return MATCHING_TYPE.CURRENT_BAR_CLOSE
        if me_str == "vwap":
            return MATCHING_TYPE.VWAP
        elif me_str == "next_bar":
            return MATCHING_TYPE.NEXT_BAR_OPEN
        elif me_str == "last":
            return MATCHING_TYPE.NEXT_TICK_LAST
        elif me_str == "best_own":
            return MATCHING_TYPE.NEXT_TICK_BEST_OWN
        elif me_str == "best_counterparty":
            return MATCHING_TYPE.NEXT_TICK_BEST_COUNTERPARTY
        elif me_str == "counterparty_offer":
            return MATCHING_TYPE.COUNTERPARTY_OFFER
        else:
            raise NotImplementedError
    
    mod.SimulationMod.parse_matching_type = patched_parse_matching_type
    print("[DEBUG] Patched SimulationMod.parse_matching_type to support 5min")
    
    # 2. Monkey patch BaseDataSource.available_data_range
    from rqalpha.data.base_data_source import BaseDataSource
    
    original_available_data_range = BaseDataSource.available_data_range
    
    def patched_available_data_range(self, frequency):
        print(f"[DEBUG] available_data_range called with frequency={frequency}", flush=True)
        
        if frequency == "5m":
            try:
                parquet_dir = Path(parquet_root) / "5m"
                print(f"[DEBUG] Checking parquet directory: {parquet_dir}", flush=True)
                
                if parquet_dir.exists():
                    parquet_files = list(parquet_dir.glob("*.parquet"))
                    print(f"[DEBUG] Found {len(parquet_files)} parquet files", flush=True)
                    
                    if parquet_files:
                        import pandas as pd
                        df = pd.read_parquet(parquet_files[0])
                        print(f"[DEBUG] DataFrame columns: {df.columns.tolist()}", flush=True)
                        if 'datetime' in df.columns:
                            start_date = df['datetime'].min().date()
                            end_date = df['datetime'].max().date()
                            print(f"[DEBUG] Found 5min data range: {start_date} ~ {end_date}", flush=True)
                            return start_date, end_date
                else:
                    print(f"[DEBUG] Parquet directory does not exist: {parquet_dir}", flush=True)
            except Exception as e:
                print(f"[DEBUG] Error getting 5min data range: {e}", flush=True)
                import traceback
                traceback.print_exc()
        
        return original_available_data_range(self, frequency)
    
    BaseDataSource.available_data_range = patched_available_data_range
    print("[DEBUG] Patched BaseDataSource.available_data_range to support 5min")
    
    # 3. Monkey patch SimulationEventSource.events to support 5m frequency
    from rqalpha.mod.rqalpha_mod_sys_simulation.simulation_event_source import SimulationEventSource
    from rqalpha.core.events import Event, EVENT
    
    original_events = SimulationEventSource.events
    
    def patched_events(self, start_date, end_date, frequency):
        if frequency == "5m":
            trading_dates = self._env.data_proxy.get_trading_dates(start_date, end_date)
            for day in trading_dates:
                before_trading_flag = True
                date = day.to_pydatetime()
                last_dt = None
                done = False
                
                dt_before_day_trading = date.replace(hour=8, minute=30)
                
                while True:
                    if done:
                        break
                    exit_loop = True
                    trading_minutes = _get_5min_trading_minutes(self, date)
                    for calendar_dt in trading_minutes:
                        if last_dt is not None and calendar_dt < last_dt:
                            continue
                        
                        if calendar_dt < dt_before_day_trading:
                            trading_dt = calendar_dt.replace(year=date.year, month=date.month, day=date.day)
                        else:
                            trading_dt = calendar_dt
                        
                        if before_trading_flag:
                            before_trading_flag = False
                            yield Event(
                                EVENT.BEFORE_TRADING,
                                calendar_dt=calendar_dt - timedelta(minutes=30),
                                trading_dt=trading_dt - timedelta(minutes=30)
                            )
                            yield Event(
                                EVENT.OPEN_AUCTION,
                                calendar_dt=calendar_dt - timedelta(minutes=3),
                                trading_dt=trading_dt - timedelta(minutes=3),
                            )
                        
                        if self._universe_changed:
                            self._universe_changed = False
                            last_dt = calendar_dt
                            exit_loop = False
                            break
                        
                        yield Event(EVENT.BAR, calendar_dt=calendar_dt, trading_dt=trading_dt)
                    
                    if exit_loop:
                        done = True
                
                dt = self._get_after_trading_dt(date)
                yield Event(EVENT.AFTER_TRADING, calendar_dt=dt, trading_dt=dt)
        else:
            yield from original_events(self, start_date, end_date, frequency)
    
    SimulationEventSource.events = patched_events
    print("[DEBUG] Patched SimulationEventSource.events to support 5min")
    
    # 4. Monkey patch rqalpha.main to use ParquetDataSource instead of BaseDataSource
    import rqalpha.main as rqalpha_main
    from rqalpha.data.base_data_source import BaseDataSource as _OriginalBaseDataSource
    
    # 添加项目路径，确保能导入 ParquetDataSource
    project_root = str(Path(__file__).parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    try:
        from app.backtest.services.parquet_data_source import ParquetDataSource
        print("[DEBUG] Successfully imported ParquetDataSource", flush=True)
    except ImportError as e:
        print(f"[ERROR] Failed to import ParquetDataSource: {e}", flush=True)
        print("[DEBUG] Falling back to patching BaseDataSource.get_bar and history_bars", flush=True)
        ParquetDataSource = None
    
    if ParquetDataSource is not None:
        # 替换 rqalpha.main 模块中的 BaseDataSource 引用
        rqalpha_main.BaseDataSource = ParquetDataSource
        # 同时替换 rqalpha.data.base_data_source 模块中的 BaseDataSource
        import rqalpha.data.base_data_source as base_data_source_module
        base_data_source_module.BaseDataSource = ParquetDataSource
        # 替换 rqalpha.data 模块中的 BaseDataSource
        import rqalpha.data as data_module
        if hasattr(data_module, 'BaseDataSource'):
            data_module.BaseDataSource = ParquetDataSource
        
        print("[DEBUG] Patched BaseDataSource -> ParquetDataSource in rqalpha.main", flush=True)
    else:
        # 如果无法导入 ParquetDataSource，则 patch BaseDataSource 的 get_bar 和 history_bars 方法
        original_get_bar = _OriginalBaseDataSource.get_bar
        original_history_bars = _OriginalBaseDataSource.history_bars
        
        def patched_get_bar(self, instrument, dt, frequency):
            if frequency == '5m':
                bars = patched_history_bars(self, instrument, 1, frequency, None, dt, include_now=True)
                if bars is not None and len(bars) > 0:
                    return bars[-1]
                return None
            return original_get_bar(self, instrument, dt, frequency)
        
        def patched_history_bars(self, instrument, bar_count, frequency, fields, dt,
                                  skip_suspended=True, include_now=False, adjust_type='pre', adjust_orig=None):
            if frequency == '5m':
                try:
                    import pandas as pd
                    import numpy as np
                    from rqalpha.utils.datetime_func import convert_date_to_int
                    
                    code = instrument.order_book_id.split('.')[0] if '.' in instrument.order_book_id else instrument.order_book_id
                    parquet_path = Path(parquet_root) / "5m" / f"{code}.parquet"
                    
                    if not parquet_path.exists():
                        return None
                    
                    df = pd.read_parquet(parquet_path)
                    if df is None or df.empty:
                        return None
                    
                    if 'datetime' in df.columns:
                        dt_pd = pd.Timestamp(dt)
                        if include_now:
                            mask = df['datetime'] <= dt_pd
                        else:
                            mask = df['datetime'] < dt_pd
                        df_filtered = df[mask].copy()
                        
                        if bar_count is not None and bar_count > 0:
                            df_filtered = df_filtered.tail(bar_count)
                        
                        if df_filtered.empty:
                            return None
                        
                        col_map = {
                            'open_price': 'open', 'high_price': 'high',
                            'low_price': 'low', 'close_price': 'close',
                            'volume': 'volume', 'turnover': 'total_turnover'
                        }
                        for old_col, new_col in col_map.items():
                            if old_col in df_filtered.columns and new_col not in df_filtered.columns:
                                df_filtered = df_filtered.rename(columns={old_col: new_col})
                        
                        if fields is None:
                            fields_list = ['datetime', 'open', 'high', 'low', 'close', 'volume', 'total_turnover']
                        elif isinstance(fields, str):
                            fields_list = [fields]
                        else:
                            fields_list = list(fields)
                        
                        available_fields = [f for f in fields_list if f in df_filtered.columns]
                        if not available_fields:
                            return None
                        
                        df_result = df_filtered[available_fields].copy()
                        
                        if 'datetime' in df_result.columns:
                            df_result['datetime'] = df_result['datetime'].apply(
                                lambda x: convert_date_to_int(x.to_pydatetime())
                            )
                        
                        dtype = [(f, 'f8') if f != 'datetime' else (f, 'i8') for f in available_fields]
                        result = np.array([tuple(row) for row in df_result.values], dtype=dtype)
                        return result
                except Exception as e:
                    print(f"[DEBUG] Error in patched_history_bars for 5m: {e}", flush=True)
                    import traceback
                    traceback.print_exc()
                    return None
            
            return original_history_bars(self, instrument, bar_count, frequency, fields, dt,
                                          skip_suspended=skip_suspended, include_now=include_now,
                                          adjust_type=adjust_type, adjust_orig=adjust_orig)
        
        _OriginalBaseDataSource.get_bar = patched_get_bar
        _OriginalBaseDataSource.history_bars = patched_history_bars
        print("[DEBUG] Patched BaseDataSource.get_bar and history_bars to support 5m", flush=True)

def main():
    if len(sys.argv) < 3:
        print("Usage: python run_5min_backtest.py <strategy.py> <config.yml>")
        sys.exit(1)
    
    strategy_path = sys.argv[1]
    config_path = sys.argv[2]
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    bundle_path = config.get('base', {}).get('data_bundle_path', '')
    benchmark = config.get('base', {}).get('benchmark', '')
    print(f"[DEBUG] Reading config file: {config_path}", flush=True)
    print(f"[DEBUG] Data bundle path: {bundle_path}", flush=True)
    print(f"[DEBUG] Benchmark: {benchmark}", flush=True)
    
    if benchmark and benchmark != 'None':
        print(f"[DEBUG] Removing benchmark '{benchmark}' for 5min backtest to avoid data insufficient error", flush=True)
        config['base']['benchmark'] = None
        if 'mod' in config and 'sys_analyser' in config['mod']:
            config['mod']['sys_analyser']['benchmark'] = None
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        print(f"[DEBUG] Updated config file: benchmark set to None", flush=True)
    
    setup_5min_support()
    
    print(f"[DEBUG] Starting rqalpha via command line entry point...", flush=True)
    
    from rqalpha.__main__ import entry_point
    
    sys.argv = [
        'rqalpha',
        'run',
        '-f', strategy_path,
        '--config', config_path,
    ]
    
    try:
        entry_point()
        print("[DEBUG] rqalpha run completed successfully", flush=True)
    except SystemExit as e:
        if e.code != 0:
            print(f"[ERROR] rqalpha exited with code: {e.code}", flush=True)
            sys.exit(e.code)
        else:
            print("[DEBUG] rqalpha run completed successfully", flush=True)
    except Exception as e:
        print(f"[ERROR] rqalpha run failed: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
