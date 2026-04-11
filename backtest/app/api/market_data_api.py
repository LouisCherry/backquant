"""Market data management API endpoints."""
from flask import Blueprint, jsonify, request
from pathlib import Path
import os
import re
import sys
from datetime import datetime
import base64
from io import BytesIO

from app.auth import auth_required
from app.database import get_db_connection, get_db_type
from app.market_data.task_manager import get_task_manager
from app.market_data.analyzer import analyze_bundle
from app.market_data.tasks import do_incremental_update, do_full_download
from app.market_data.utils import is_current_month_updated, get_market_data_db_path

# Add imports for data inspection
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'scripts'))
from scripts.sync_market_data import scan_parquet_data, generate_data_completeness_matrix, generate_data_distribution_histogram, generate_update_timeliness_scatter, print_inspection_report

bp_market_data = Blueprint('market_data', __name__, url_prefix='/api/market-data')


def _get_bundle_path():
    """Get bundle path."""
    return Path(os.environ.get('RQALPHA_BUNDLE_PATH', '/data/rqalpha/bundle'))


def _ensure_market_data_schema():
    """Ensure market data tables exist before serving requests.

    App startup already tries to initialize the schema, but that path is wrapped
    in a broad exception handler. Re-run the idempotent init here so first-time
    requests do not fail on missing cache tables like vnpy_stats.
    """
    from app.market_data.db_init import init_database, init_database_with_connection

    if get_db_type() == 'mariadb':
        with get_db_connection('market_data') as db:
            init_database_with_connection(db)
    else:
        db_path = get_market_data_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        init_database(db_path)


def _get_vnpy_table_name() -> str:
    """Return the vnpy source table name within the configured database.

    MariaDB connections already bind to DB_NAME, so vnpy source data, task
    metadata, and stats cache should all live in that same database. Accept
    only a simple table identifier here to avoid cross-schema drift.
    """
    table = (os.environ.get('DB_TABLE', 'dbbardata') or 'dbbardata').strip()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table):
        raise ValueError('DB_TABLE must be a simple table name in the configured database')
    return table


@bp_market_data.route('/overview', methods=['GET'])
@auth_required
def get_overview():
    """Get data overview."""
    try:
        with get_db_connection('market_data') as db:
            row = db.fetchone("SELECT * FROM market_data_stats WHERE id = 1")
            if not row:
                return jsonify({
                    'analyzed': False,
                    'message': '尚未分析数据，请先触发数据分析'
                }), 200
            files = db.fetchall("""
                SELECT file_name, file_path, file_size, modified_at
                FROM market_data_files
                ORDER BY file_name
            """)
        return jsonify({'analyzed': True, 'data': row, 'files': files}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/analyze', methods=['POST'])
@auth_required
def trigger_analyze():
    """Trigger data analysis."""
    try:
        tm = get_task_manager()
        bundle_path = _get_bundle_path()

        source = 'manual'
        if request.json:
            source = request.json.get('source', 'manual')

        task_id = tm.submit_task(
            'analyze',
            analyze_bundle,
            task_args=(bundle_path, tm.db_config_dict),
            source=source
        )

        return jsonify({'task_id': task_id}), 202

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/download/incremental', methods=['POST'])
@auth_required
def trigger_incremental():
    """Trigger incremental update."""
    bundle_path = _get_bundle_path()
    force = request.json.get('force', False) if request.json else False

    if not force and is_current_month_updated(bundle_path):
        return jsonify({
            'need_confirm': True,
            'message': '检测到当月已有更新，确定要再次更新吗？'
        }), 200

    try:
        tm = get_task_manager()
        task_id = tm.submit_task('incremental', do_incremental_update, source='manual')
        return jsonify({'task_id': task_id}), 202

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/download/full', methods=['POST'])
@auth_required
def trigger_full():
    """Trigger full download."""
    from app.market_data.utils import get_bundle_update_status

    bundle_path = _get_bundle_path()
    force = request.json.get('force', False) if request.json else False

    if not force:
        needs_confirm, message = get_bundle_update_status(bundle_path)
        if needs_confirm and message:
            return jsonify({
                'need_confirm': True,
                'message': message
            }), 200

    try:
        tm = get_task_manager()
        task_id = tm.submit_task('full', do_full_download, source='manual')
        return jsonify({'task_id': task_id}), 202

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/tasks/running', methods=['GET'])
@auth_required
def get_running_task():
    """Get currently running task."""
    try:
        tm = get_task_manager()
        task = tm.get_running_task()
        return jsonify({'task': task}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/tasks/<task_id>', methods=['GET'])
@auth_required
def get_task_status(task_id: str):
    """Get task status."""
    try:
        tm = get_task_manager()
        task = tm.get_task_status(task_id)

        if not task:
            return jsonify({'error': '任务不存在'}), 404

        return jsonify(task), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/tasks/<task_id>/retry', methods=['POST'])
@auth_required
def retry_task(task_id: str):
    """Retry failed task."""
    tm = get_task_manager()
    task = tm.get_task_status(task_id)

    if not task:
        return jsonify({'error': '任务不存在'}), 404

    if task['status'] != 'failed':
        return jsonify({'error': '只能重试失败的任务'}), 400

    task_type = task['task_type']
    try:
        if task_type == 'analyze':
            bundle_path = _get_bundle_path()
            new_task_id = tm.submit_task('analyze', analyze_bundle,
                                         task_args=(bundle_path, tm.db_config_dict),
                                         source='retry')
        elif task_type == 'incremental':
            new_task_id = tm.submit_task('incremental', do_incremental_update, source='retry')
        elif task_type == 'full':
            new_task_id = tm.submit_task('full', do_full_download, source='retry')
        else:
            return jsonify({'error': '未知任务类型'}), 400

        return jsonify({'task_id': new_task_id}), 202

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/tasks/<task_id>/logs', methods=['GET'])
@auth_required
def get_task_logs(task_id: str):
    """Get logs for a specific task."""
    try:
        limit = request.args.get('limit', 100, type=int)
        with get_db_connection('market_data') as db:
            logs = db.fetchall(
                "SELECT * FROM market_data_task_logs WHERE task_id = ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (task_id, limit)
            )
        return jsonify({'logs': logs}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/cron/config', methods=['GET'])
@auth_required
def get_cron_config():
    """Get cron configuration."""
    try:
        with get_db_connection('market_data') as db:
            row = db.fetchone("SELECT * FROM market_data_cron_config WHERE id = 1")
            if not row:
                db.execute(
                    """INSERT INTO market_data_cron_config
                       (id, enabled, cron_expression, task_type, updated_at)
                       VALUES (1, 1, '0 4 1 * *', 'full', ?)""",
                    (datetime.utcnow().isoformat(),)
                )
                return jsonify({
                    'enabled': True,
                    'cron_expression': '0 4 1 * *',
                    'task_type': 'full'
                }), 200
        return jsonify(row), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/cron/config', methods=['PUT'])
@auth_required
def update_cron_config():
    """Update cron configuration."""
    data = request.json
    enabled = data.get('enabled', False)
    cron_expression = data.get('cron_expression', '0 2 1 * *')
    task_type = data.get('task_type', 'incremental')

    if task_type not in ('incremental', 'full'):
        return jsonify({'error': '无效的任务类型'}), 400

    try:
        from apscheduler.triggers.cron import CronTrigger
        try:
            CronTrigger.from_crontab(cron_expression)
        except Exception:
            return jsonify({'error': '无效的 cron 表达式'}), 400

        with get_db_connection('market_data') as db:
            db.replace_into(
                'market_data_cron_config',
                ['id', 'enabled', 'cron_expression', 'task_type', 'updated_at'],
                (1, 1 if enabled else 0, cron_expression, task_type, datetime.utcnow().isoformat())
            )

        from app.market_data.scheduler import update_cron_schedule
        if enabled:
            update_cron_schedule(cron_expression)
        else:
            update_cron_schedule(None)

        return jsonify({'message': '配置已更新'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/cron/config/5min', methods=['GET'])
@auth_required
def get_cron_config_5min():
    """Get 5min cron configuration."""
    try:
        with get_db_connection('market_data') as db:
            row = db.fetchone("SELECT * FROM market_data_cron_config_5min WHERE id = 1")
            if not row:
                db.execute(
                    """INSERT INTO market_data_cron_config_5min
                       (id, enabled, cron_expression, task_type, script_path, updated_at)
                       VALUES (1, 1, '*/5 * * * *', '5min', '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/scripts/fetch_all_stocks_5min.py', ?)""",
                    (datetime.utcnow().isoformat(),)
                )
                return jsonify({
                    'enabled': True,
                    'cron_expression': '*/5 * * * *',
                    'task_type': '5min'
                }), 200
        # 只返回需要的字段，移除 script_path
        return jsonify({
            'enabled': row.get('enabled', True),
            'cron_expression': row.get('cron_expression', '*/5 * * * *'),
            'task_type': row.get('task_type', '5min')
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/cron/config/5min', methods=['PUT'])
@auth_required
def update_cron_config_5min():
    """Update 5min cron configuration."""
    data = request.json
    enabled = data.get('enabled', True)
    cron_expression = data.get('cron_expression', '*/5 * * * *')
    task_type = data.get('task_type', '5min')
    # 硬编码脚本路径
    script_path = '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/scripts/fetch_all_stocks_5min.py'

    if task_type != '5min':
        return jsonify({'error': '无效的任务类型'}), 400

    try:
        from apscheduler.triggers.cron import CronTrigger
        try:
            CronTrigger.from_crontab(cron_expression)
        except Exception:
            return jsonify({'error': '无效的 cron 表达式'}), 400

        with get_db_connection('market_data') as db:
            db.replace_into(
                'market_data_cron_config_5min',
                ['id', 'enabled', 'cron_expression', 'task_type', 'script_path', 'updated_at'],
                (1, 1 if enabled else 0, cron_expression, task_type, script_path, datetime.utcnow().isoformat())
            )

        from app.market_data.scheduler import update_5min_cron_schedule
        if enabled:
            update_5min_cron_schedule(cron_expression, script_path)
        else:
            update_5min_cron_schedule(None, None)

        return jsonify({'message': '配置已更新'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/logs', methods=['DELETE'])
@auth_required
def clear_all_logs():
    """Clear all task logs and tasks."""
    try:
        with get_db_connection('market_data') as db:
            db.execute("DELETE FROM market_data_task_logs")
            db.execute("DELETE FROM market_data_tasks")
        return jsonify({'message': '日志已清空'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/logs', methods=['GET'])
@auth_required
def get_all_logs():
    """Get all task logs (including manual and cron tasks)."""
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)

    try:
        with get_db_connection('market_data') as db:
            logs = db.fetchall("""
                SELECT
                    tl.log_id,
                    tl.task_id,
                    tl.timestamp,
                    tl.level,
                    tl.message,
                    t.task_type,
                    t.status as task_status,
                    t.source
                FROM market_data_task_logs tl
                LEFT JOIN market_data_tasks t ON tl.task_id = t.task_id
                ORDER BY tl.timestamp DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            total_row = db.fetchone("SELECT COUNT(*) as count FROM market_data_task_logs")
            total = total_row['count'] if total_row else 0

        return jsonify({
            'logs': logs,
            'total': total,
            'limit': limit,
            'offset': offset
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/cron/logs', methods=['GET'])
@auth_required
def get_cron_logs():
    """Get cron logs."""
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)

    try:
        with get_db_connection('market_data') as db:
            logs = db.fetchall("""
                SELECT * FROM market_data_cron_logs
                ORDER BY trigger_time DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            total_row = db.fetchone("SELECT COUNT(*) as count FROM market_data_cron_logs")
            total = total_row['count'] if total_row else 0

        return jsonify({
            'logs': logs,
            'total': total,
            'limit': limit,
            'offset': offset
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/cron/logs/<int:log_id>', methods=['GET'])
@auth_required
def get_cron_log_detail(log_id: int):
    """Get cron log detail."""
    try:
        with get_db_connection('market_data') as db:
            row = db.fetchone(
                "SELECT * FROM market_data_cron_logs WHERE log_id = ?",
                (log_id,)
            )
            if not row:
                return jsonify({'error': '日志不存在'}), 404

            if row.get('task_id'):
                task_row = db.fetchone(
                    "SELECT * FROM market_data_tasks WHERE task_id = ?",
                    (row['task_id'],)
                )
                if task_row:
                    row['task'] = task_row

        return jsonify(row), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# VNPY Futures Data Management
# ============================================================================

def _refresh_vnpy_stats_to_db(config_dict=None):
    """Query dbbardata live stats and save to vnpy_stats cache table."""
    import json

    table = _get_vnpy_table_name()

    if config_dict is None:
        _ensure_market_data_schema()

    with get_db_connection(config_dict=config_dict) as db:
        lock_acquired = False
        try:
            if db.config.db_type == 'mariadb':
                row = db.fetchone("SELECT GET_LOCK(?, ?) AS got", ("vnpy_stats_refresh", 10))
                lock_acquired = bool(row and row.get("got") == 1)
                if not lock_acquired:
                    raise RuntimeError("刷新统计正在进行中，请稍后重试")

            # Single scan: get all stats + per-exchange breakdown in one query
            by_exchange = db.fetchall(
                f"SELECT exchange, COUNT(DISTINCT symbol) AS contracts, COUNT(*) AS `rows`, "
                f"MIN(datetime) AS min_dt, MAX(datetime) AS max_dt "
                f"FROM {table} GROUP BY exchange ORDER BY `rows` DESC"
            )

            total_rows = sum(r['rows'] for r in by_exchange)
            contract_count = sum(r['contracts'] for r in by_exchange)
            exchange_count = len(by_exchange)
            min_date = min((str(r['min_dt']) for r in by_exchange if r['min_dt']), default=None)
            max_date = max((str(r['max_dt']) for r in by_exchange if r['max_dt']), default=None)

            # Strip min_dt/max_dt from per-exchange results before caching
            by_exchange_out = [
                {'exchange': r['exchange'], 'contracts': r['contracts'], 'rows': r['rows']}
                for r in by_exchange
            ]

            # Write to vnpy_stats cache (id=1), safe for concurrent refreshes
            db.upsert(
                "vnpy_stats",
                ["id", "total_rows", "contract_count", "exchange_count", "min_date", "max_date", "by_exchange"],
                (1, total_rows, contract_count, exchange_count, min_date, max_date, json.dumps(by_exchange_out)),
                "id",
                ["total_rows", "contract_count", "exchange_count", "min_date", "max_date", "by_exchange"],
            )
        finally:
            if db.config.db_type == 'mariadb' and lock_acquired:
                db.execute("SELECT RELEASE_LOCK(?)", ("vnpy_stats_refresh",))

    return {
        'total_rows': total_rows,
        'contract_count': contract_count,
        'exchange_count': exchange_count,
        'min_date': min_date,
        'max_date': max_date,
        'by_exchange': by_exchange_out,
    }


@bp_market_data.route('/vnpy/stats', methods=['GET'])
@auth_required
def get_vnpy_stats():
    """Get dbbardata table statistics from cache."""
    import json
    try:
        _ensure_market_data_schema()
        with get_db_connection('market_data') as db:
            row = db.fetchone("SELECT * FROM vnpy_stats WHERE id = 1")

        if not row:
            return jsonify({
                'total_rows': 0,
                'contract_count': 0,
                'exchange_count': 0,
                'min_date': None,
                'max_date': None,
                'by_exchange': [],
                'updated_at': None,
            }), 200

        by_exchange = row.get('by_exchange')
        if isinstance(by_exchange, str):
            by_exchange = json.loads(by_exchange)

        return jsonify({
            'total_rows': row['total_rows'],
            'contract_count': row['contract_count'],
            'exchange_count': row['exchange_count'],
            'min_date': row['min_date'],
            'max_date': row['max_date'],
            'by_exchange': by_exchange or [],
            'updated_at': str(row['updated_at']) if row.get('updated_at') else None,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/vnpy/refresh-stats', methods=['POST'])
@auth_required
def refresh_vnpy_stats():
    """Manually refresh vnpy stats cache from live data."""
    try:
        result = _refresh_vnpy_stats_to_db()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/vnpy/running-task', methods=['GET'])
@auth_required
def get_vnpy_running_task():
    """Get currently running vnpy_import task, if any."""
    try:
        tm = get_task_manager()
        task = tm.get_running_task_by_type('vnpy_import')
        if task:
            return jsonify(task), 200
        return jsonify(None), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/vnpy/cancel/<task_id>', methods=['POST'])
@auth_required
def cancel_vnpy_task(task_id: str):
    """Cancel a running vnpy_import task."""
    try:
        tm = get_task_manager()
        ok = tm.cancel_task(task_id)
        if ok:
            return jsonify({'message': '任务已取消'}), 200
        return jsonify({'error': '无法取消该任务（可能已完成）'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/vnpy/import', methods=['POST'])
@auth_required
def trigger_vnpy_import():
    """Trigger futures data import from rqalpha bundle into MariaDB."""
    try:
        bundle_path = _get_bundle_path()
        h5_path = bundle_path / 'futures.h5'
        pk_path = bundle_path / 'instruments.pk'

        if not h5_path.exists():
            return jsonify({'error': f'futures.h5 不存在: {h5_path}'}), 400
        if not pk_path.exists():
            return jsonify({'error': f'instruments.pk 不存在: {pk_path}'}), 400

        tm = get_task_manager()
        task_id = tm.submit_task(
            'vnpy_import',
            _do_vnpy_import,
            (str(h5_path), str(pk_path)),
            source='manual',
        )
        return jsonify({'task_id': task_id}), 200

    except RuntimeError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp_market_data.route('/inspection', methods=['GET'])
@auth_required
def get_data_inspection():
    """Get data inspection report with visualizations."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        # Get frequency parameter from request
        frequency = request.args.get('frequency', None)

        # Scan data
        stats = scan_parquet_data(frequency)

        if not stats['frequencies']:
            return jsonify({'error': '未找到任何 Parquet 数据文件'}), 404

        # Generate completeness matrix
        matrix_buffer = BytesIO()
        generate_data_completeness_matrix(stats, sample_size=50, output_path=matrix_buffer)
        matrix_buffer.seek(0)
        matrix_base64 = base64.b64encode(matrix_buffer.read()).decode('utf-8')
        matrix_buffer.close()

        # Generate distribution histogram
        hist_buffer = BytesIO()
        generate_data_distribution_histogram(stats, output_path=hist_buffer)
        hist_buffer.seek(0)
        hist_base64 = base64.b64encode(hist_buffer.read()).decode('utf-8')
        hist_buffer.close()

        # Generate timeliness scatter
        scatter_buffer = BytesIO()
        generate_update_timeliness_scatter(stats, output_path=scatter_buffer)
        scatter_buffer.seek(0)
        scatter_base64 = base64.b64encode(scatter_buffer.read()).decode('utf-8')
        scatter_buffer.close()

        # Close all plots to free memory
        plt.close('all')

        # Prepare response data
        response = {
            'stats': stats,
            'visualizations': {
                'completeness_matrix': matrix_base64,
                'distribution_histogram': hist_base64,
                'timeliness_scatter': scatter_base64
            }
        }

        return jsonify(response), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _do_vnpy_import(task_id: str, h5_path: str, pk_path: str):
    """Run the import script as a subprocess, streaming logs to TaskManager."""
    import subprocess

    tm = get_task_manager()
    tm.update_progress(task_id, 0, '导入', '启动期货数据导入...')
    tm.log(task_id, 'INFO', f'h5={h5_path}, pk={pk_path}')

    script = str(Path(__file__).resolve().parent.parent.parent / 'scripts' / 'import_rqalpha_futures_to_mariadb.py')

    cmd = [
        sys.executable, script,
        '--h5', h5_path,
        '--pk', pk_path,
    ]

    # 9-step progress: Reading futures.h5 gets the largest share (15%→55%)
    step_progress = [
        ('Loading instruments.pk', 5),
        ('Loaded exchange map', 10),
        ('Reading futures.h5', 15),
        ('Parsed rows', 55),
        ('Truncating table', 65),
        ('Loading data into MariaDB', 75),
        ('Import finished', 85),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    current_pct = 0
    for line in proc.stdout:
        line = line.rstrip('\n')
        if not line:
            continue
        tm.log(task_id, 'INFO', line)

        for keyword, pct in step_progress:
            if keyword in line:
                current_pct = pct
                break

        tm.update_progress(task_id, current_pct, '导入', line)

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f'导入脚本退出码: {proc.returncode}')

    tm.update_progress(task_id, 90, '统计', '正在刷新统计数据...')
    tm.log(task_id, 'INFO', '导入完成，刷新统计缓存...')
    _refresh_vnpy_stats_to_db(config_dict=tm.db_config_dict)

    tm.update_progress(task_id, 100, '完成', '期货数据导入完成')
