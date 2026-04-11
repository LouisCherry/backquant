"""Scheduler for cron jobs."""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pathlib import Path
from datetime import datetime
import logging
import subprocess
import sys

from app.database import DatabaseConfig, get_db_connection

logger = logging.getLogger(__name__)

_scheduler = None

# Serialized DB config stored at init_scheduler() time (Flask context).
# APScheduler callbacks run in background threads without Flask context,
# so this dict is used to reconnect without current_app.
# init_scheduler() is called exactly once during app startup (create_app).
_db_config_dict: dict = None


def get_scheduler():
    """Get scheduler singleton."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.start()
        logger.info('APScheduler started')
    return _scheduler


def load_cron_config() -> dict:
    """Load cron configuration from database."""
    with get_db_connection(config_dict=_db_config_dict) as db:
        return db.fetchone("SELECT * FROM market_data_cron_config WHERE id = 1")


def load_cron_config_5min() -> dict:
    """Load 5min cron configuration from database."""
    with get_db_connection(config_dict=_db_config_dict) as db:
        return db.fetchone("SELECT * FROM market_data_cron_config_5min WHERE id = 1")


def cron_job_handler():
    """Cron job handler for full/incremental download."""
    from app.market_data.task_manager import get_task_manager
    from app.market_data.tasks import do_full_download, do_incremental_update

    config = load_cron_config()

    if not config or not config['enabled']:
        logger.info('Cron job disabled, skipping')
        _log_cron_run(None, 'skipped', '定时任务未启用')
        return

    tm = get_task_manager()

    # Check for running tasks (mutex)
    if tm._has_running_task():
        logger.warning('Task already running, skipping cron job')
        _log_cron_run(None, 'skipped', '已有任务正在运行')
        return

    # Submit task based on config
    task_type = config['task_type']
    try:
        if task_type == 'full':
            task_id = tm.submit_task('full', do_full_download, source='cron')
        elif task_type == 'incremental':
            task_id = tm.submit_task('incremental', do_incremental_update, source='cron')
        else:
            raise ValueError(f'Unknown task type: {task_type}')

        logger.info(f'Cron job submitted task: {task_id}')
        _log_cron_run(task_id, 'success', f'已提交任务: {task_id}')

    except Exception as e:
        logger.error(f'Cron job failed: {str(e)}')
        _log_cron_run(None, 'failed', str(e))


def cron_job_handler_5min():
    """Cron job handler for 5min data download."""
    config = load_cron_config_5min()

    if not config or not config['enabled']:
        logger.info('5min cron job disabled, skipping')
        _log_cron_run_5min(None, 'skipped', '定时任务未启用')
        return

    script_path = config.get('script_path')
    if not script_path:
        logger.error('5min cron job: script path not configured')
        _log_cron_run_5min(None, 'failed', '脚本路径未配置')
        return

    script_path_obj = Path(script_path)
    if not script_path_obj.exists():
        logger.error(f'5min cron job: script not found at {script_path}')
        _log_cron_run_5min(None, 'failed', f'脚本不存在: {script_path}')
        return

    logger.info(f'5min cron job triggered, executing script: {script_path}')

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=3600
        )

        if result.returncode == 0:
            logger.info(f'5min cron job completed successfully')
            _log_cron_run_5min(None, 'success', '脚本执行成功')
        else:
            logger.error(f'5min cron job failed with return code {result.returncode}')
            logger.error(f'Stdout: {result.stdout}')
            logger.error(f'Stderr: {result.stderr}')
            _log_cron_run_5min(None, 'failed', f'脚本执行失败，退出码: {result.returncode}')

    except subprocess.TimeoutExpired:
        logger.error('5min cron job timed out')
        _log_cron_run_5min(None, 'failed', '脚本执行超时')
    except Exception as e:
        logger.error(f'5min cron job failed: {str(e)}')
        _log_cron_run_5min(None, 'failed', str(e))


def _log_cron_run(task_id, status: str, message: str):
    """Log cron run for full/incremental tasks."""
    with get_db_connection(config_dict=_db_config_dict) as db:
        db.execute(
            """INSERT INTO market_data_cron_logs
               (task_id, trigger_time, status, message)
               VALUES (?, ?, ?, ?)""",
            (task_id, datetime.utcnow().isoformat(), status, message)
        )


def _log_cron_run_5min(task_id, status: str, message: str):
    """Log cron run for 5min tasks."""
    with get_db_connection(config_dict=_db_config_dict) as db:
        db.execute(
            """INSERT INTO market_data_cron_logs
               (task_id, trigger_time, status, message)
               VALUES (?, ?, ?, ?)""",
            (task_id, datetime.utcnow().isoformat(), status, message)
        )


def update_cron_schedule(cron_expression: str):
    """Update cron schedule for full/incremental download."""
    scheduler = get_scheduler()

    # Remove old job if exists
    try:
        scheduler.remove_job('market_data_cron', jobstore=None)
    except Exception:
        pass

    # Add new job
    if cron_expression:
        trigger = CronTrigger.from_crontab(cron_expression)
        scheduler.add_job(
            cron_job_handler,
            trigger=trigger,
            id='market_data_cron',
            replace_existing=True
        )
        logger.info(f'Cron schedule updated: {cron_expression}')


def update_5min_cron_schedule(cron_expression: str, script_path: str = None):
    """Update cron schedule for 5min data download."""
    scheduler = get_scheduler()

    # Remove old job if exists
    try:
        scheduler.remove_job('market_data_cron_5min', jobstore=None)
    except Exception:
        pass

    # Add new job
    if cron_expression:
        trigger = CronTrigger.from_crontab(cron_expression)
        scheduler.add_job(
            cron_job_handler_5min,
            trigger=trigger,
            id='market_data_cron_5min',
            replace_existing=True
        )
        logger.info(f'5min Cron schedule updated: {cron_expression}')


def init_scheduler():
    """Initialize scheduler on app startup.

    Must be called once within a Flask application context (e.g., from create_app).
    Stores the DB connection config so that APScheduler background threads can
    connect without a Flask context.
    """
    global _db_config_dict

    config = DatabaseConfig.from_flask_config('market_data')
    _db_config_dict = config.to_dict()

    cron_config = load_cron_config()

    if cron_config and cron_config['enabled'] and cron_config['cron_expression']:
        update_cron_schedule(cron_config['cron_expression'])
        logger.info(f'Cron schedule loaded: {cron_config["cron_expression"]}')

    cron_config_5min = load_cron_config_5min()

    if cron_config_5min and cron_config_5min['enabled'] and cron_config_5min['cron_expression']:
        update_5min_cron_schedule(cron_config_5min['cron_expression'], cron_config_5min.get('script_path'))
        logger.info(f'5min Cron schedule loaded: {cron_config_5min["cron_expression"]}')
