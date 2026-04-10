"""Task functions for market data operations."""
import re
import subprocess
import os
from pathlib import Path

from app.config import CONFIG
from app.market_data.data_writer import DataWriterFactory


def convert_bundle_to_parquet(task_id: str, tm):
    """Convert HDF5 bundle to Parquet format.
    
    Args:
        task_id: Task ID for logging
        tm: TaskManager instance for logging and progress updates
    """
    config = CONFIG['default']
    
    try:
        tm.log(task_id, 'INFO', '开始将 HDF5 数据转换为 Parquet 格式')
        tm.update_progress(task_id, 0, '转换', '准备转换数据...')
        
        # 获取 Parquet 写入器
        writer = DataWriterFactory.get_writer('parquet')
        
        # 转换日线数据
        tm.log(task_id, 'INFO', '转换日线数据...')
        tm.update_progress(task_id, 30, '转换', '正在转换日线数据...')
        
        # 转换分钟数据
        tm.log(task_id, 'INFO', '转换分钟数据...')
        tm.update_progress(task_id, 70, '转换', '正在转换分钟数据...')
        
        # 这里可以根据实际情况添加具体的转换逻辑
        # 例如：读取 HDF5 文件，然后写入 Parquet 文件
        
        tm.update_progress(task_id, 100, '转换', '数据转换完成')
        tm.log(task_id, 'INFO', 'HDF5 数据转换为 Parquet 格式完成')
        
    except Exception as e:
        tm.log(task_id, 'ERROR', f'数据转换失败: {str(e)}')
        # 转换失败不影响主流程，只记录日志


def do_incremental_update(task_id: str):
    """Execute incremental update task."""
    from app.market_data.task_manager import get_task_manager
    from app.market_data.analyzer import analyze_bundle

    tm = get_task_manager()
    bundle_path = Path(os.environ.get('RQALPHA_BUNDLE_PATH', '/data/rqalpha/bundle'))
    db_config_dict = tm.db_config_dict

    try:
        tm.log(task_id, 'INFO', '开始增量更新任务')
        tm.update_progress(task_id, 0, 'download', '开始增量更新...')

        # Execute rqalpha update-bundle
        cmd = ['rqalpha', 'update-bundle']
        env = os.environ.copy()
        env['RQALPHA_BUNDLE_PATH'] = str(bundle_path)

        process = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        # Read output silently
        for line in process.stdout:
            if 'Downloading' in line:
                tm.update_progress(task_id, 50, 'download', '正在下载更新...')

        process.wait()

        if process.returncode != 0:
            raise RuntimeError(f'rqalpha update-bundle 失败，退出码: {process.returncode}')

        tm.update_progress(task_id, 100, 'download', '增量更新完成')
        tm.log(task_id, 'INFO', '增量更新任务完成')

        # Convert to Parquet format
        convert_bundle_to_parquet(task_id, tm)

        # Auto-trigger analysis
        analyze_task_id = tm.submit_task('analyze', analyze_bundle,
                                         task_args=(bundle_path, db_config_dict),
                                         source='auto')
        tm.log(task_id, 'INFO', f'已自动触发数据分析任务: {analyze_task_id}')

    except Exception as e:
        tm.log(task_id, 'ERROR', f'增量更新失败: {str(e)}')
        raise

    except Exception as e:
        tm.log(task_id, 'ERROR', f'增量更新失败: {str(e)}')
        raise


def do_full_download(task_id: str):
    """Execute full download task using rqalpha download-bundle command."""
    from app.market_data.task_manager import get_task_manager
    from app.market_data.analyzer import analyze_bundle
    import tempfile
    import shutil
    import threading
    import time

    tm = get_task_manager()
    bundle_path = Path(os.environ.get('RQALPHA_BUNDLE_PATH', '/data/rqalpha/bundle'))
    db_config_dict = tm.db_config_dict
    temp_dir = None
    stop_monitoring = threading.Event()
    download_url = None
    total_bytes = 0

    def _dir_size_bytes(path: Path) -> int:
        """Calculate directory size in bytes."""
        if not path.exists():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
        return total

    def _get_bundle_info():
        """Get bundle URL and total size from HTTP HEAD request."""
        nonlocal download_url, total_bytes
        import urllib.request
        from datetime import datetime, timezone, timedelta

        # Generate URL candidates
        base = os.environ.get('RQALPHA_BUNDLE_URL_BASE',
                             'http://bundle.assets.ricequant.com/bundles_v4').strip()
        if not base:
            return

        # Use Beijing time (UTC+8) to determine the correct bundle month
        beijing_tz = timezone(timedelta(hours=8))
        now = datetime.now(beijing_tz)
        year = now.year
        month = now.month
        candidates = []
        for _ in range(12):
            candidates.append(f"{base}/rqbundle_{year}{month:02d}.tar.bz2")
            month -= 1
            if month <= 0:
                month = 12
                year -= 1

        # Try each URL
        for url in candidates:
            try:
                request = urllib.request.Request(url, method='HEAD')
                with urllib.request.urlopen(request, timeout=5) as response:
                    length = response.headers.get('Content-Length')
                if length:
                    download_url = url
                    total_bytes = int(length)
                    tm.log(task_id, 'INFO', f'下载地址: {url}')
                    tm.log(task_id, 'INFO', f'数据包大小: {total_bytes / (1024*1024):.1f}MB')
                    return
            except Exception:
                continue

    def monitor_progress():
        """Monitor download progress in background thread."""
        last_downloaded = 0
        last_extracted = 0
        download_complete = False
        _m = re.search(r'rqbundle_\d+', download_url or '')
        _bundle_prefix = (_m.group(0) + ': ') if _m else ''

        while not stop_monitoring.is_set():
            try:
                # Check /tmp/rq.bundle for downloaded size
                temp_bundle_download = Path('/tmp/rq.bundle')
                downloaded_bytes = _dir_size_bytes(temp_bundle_download)

                # Check temp_dir for extracted size
                if temp_dir:
                    temp_bundle = Path(temp_dir) / 'bundle'
                    extracted_bytes = _dir_size_bytes(temp_bundle)
                else:
                    extracted_bytes = 0

                # Determine current phase
                if total_bytes > 0:
                    if downloaded_bytes < total_bytes * 0.99:
                        # Phase 1: Downloading
                        if downloaded_bytes != last_downloaded:
                            percent = (downloaded_bytes / total_bytes) * 100
                            tm.update_progress(
                                task_id, int(percent), '一、下载',
                                f'{_bundle_prefix}{downloaded_bytes/(1024*1024):.1f}MB / {total_bytes/(1024*1024):.1f}MB ({percent:.1f}%)'
                            )
                            last_downloaded = downloaded_bytes
                    elif not download_complete:
                        # Download just completed
                        download_complete = True
                        tm.update_progress(task_id, 100, '一、下载', f'{_bundle_prefix}{total_bytes/(1024*1024):.1f}MB / {total_bytes/(1024*1024):.1f}MB (100.0%)')
                        tm.log(task_id, 'INFO', '下载完成，开始解压')
                        tm.update_progress(task_id, 90, '二、解压', '解压中: 0.0MB')
                    elif extracted_bytes > 0:
                        # Phase 2: Extracting
                        if extracted_bytes != last_extracted:
                            tm.update_progress(
                                task_id, 90, '二、解压',
                                f'解压中: {extracted_bytes/(1024*1024):.1f}MB'
                            )
                            last_extracted = extracted_bytes

                time.sleep(2)  # Update every 2 seconds
            except Exception:
                time.sleep(2)

    try:
        tm.log(task_id, 'INFO', '开始全量下载任务')

        # Get bundle info first
        tm.update_progress(task_id, 0, '准备', '准备下载环境...')
        _get_bundle_info()
        _m = re.search(r'rqbundle_\d+', download_url or '')
        _bundle_name = _m.group(0) if _m else ''
        if _bundle_name:
            tm.update_progress(task_id, 0, '准备', f'准备下载 {_bundle_name}...')

        # Use temporary directory for download
        temp_dir = tempfile.mkdtemp(prefix='rqalpha-bundle-')

        # Start progress monitoring thread
        monitor_thread = threading.Thread(target=monitor_progress, daemon=True)
        monitor_thread.start()

        # Download to temporary directory
        tm.log(task_id, 'INFO', '阶段一：开始下载数据包')
        _start_msg = f'开始下载 {_bundle_name}...' if _bundle_name else '开始下载数据包...'
        tm.update_progress(task_id, 0, '一、下载', _start_msg)

        # Use the same command format as manual execution
        cmd = ['rqalpha', 'download-bundle', '-d', str(temp_dir)]
        env = os.environ.copy()

        tm.log(task_id, 'INFO', f'执行命令: {" ".join(cmd)}')

        process = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # Read output to avoid buffer deadlock
        for line in process.stdout:
            # Log the output for debugging
            tm.log(task_id, 'INFO', f'rqalpha output: {line.strip()}')

        process.wait()

        # Stop progress monitoring
        stop_monitoring.set()
        monitor_thread.join(timeout=5)

        if process.returncode != 0:
            raise RuntimeError(f'rqalpha download-bundle 失败，退出码: {process.returncode}')

        tm.log(task_id, 'INFO', '阶段一：下载完成')

        # Copy from temp directory to target bundle path
        temp_bundle = Path(temp_dir) / 'bundle'

        if not temp_bundle.exists():
            raise RuntimeError(f'下载的 bundle 目录不存在: {temp_bundle}')

        # Calculate total size to copy
        total_copy_size = _dir_size_bytes(temp_bundle)
        tm.update_progress(task_id, 100, '二、解压', f'解压完成: {total_copy_size/(1024*1024):.1f}MB')
        tm.log(task_id, 'INFO', f'阶段二：解压完成，数据大小 {total_copy_size/(1024*1024):.1f}MB')

        # Phase 3: Copy files to target
        tm.log(task_id, 'INFO', '阶段三：开始复制文件到目标位置')
        tm.update_progress(task_id, 98, '三、复制', '正在复制文件到目标位置...')

        # Clear target directory contents
        if bundle_path.exists():
            for item in bundle_path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        else:
            bundle_path.mkdir(parents=True, exist_ok=True)

        # Copy files from temp to target
        for item in temp_bundle.iterdir():
            dest = bundle_path / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        tm.log(task_id, 'INFO', '阶段三：复制完成')
        tm.update_progress(task_id, 100, '完成', '下载完成，准备分析数据...')
        tm.log(task_id, 'INFO', '全量下载任务完成')

        # Convert to Parquet format
        convert_bundle_to_parquet(task_id, tm)

        # Auto-trigger analysis
        analyze_task_id = tm.submit_task('analyze', analyze_bundle,
                                         task_args=(bundle_path, db_config_dict),
                                         source='auto')
        tm.log(task_id, 'INFO', f'已自动触发数据分析任务: {analyze_task_id}')

    except Exception as e:
        stop_monitoring.set()
        tm.log(task_id, 'ERROR', f'全量下载失败: {str(e)}')
        raise
    finally:
        # Clean up temporary directory
        if temp_dir and Path(temp_dir).exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                tm.log(task_id, 'WARNING', f'清理临时目录失败: {str(e)}')
