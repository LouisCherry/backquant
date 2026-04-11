-- BackQuant MariaDB Initialization Script
-- This script creates all necessary tables for the BackQuant platform

-- Set character set and collation
SET NAMES utf8mb4;
SET CHARACTER SET utf8mb4;

-- ============================================================================
-- 1. Authentication Database Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 2. Market Data Management Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS market_data_tasks (
    task_id VARCHAR(128) PRIMARY KEY,
    task_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,
    progress INTEGER DEFAULT 0,
    stage VARCHAR(100),
    message TEXT,
    source VARCHAR(50),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP NULL,
    finished_at TIMESTAMP NULL,
    error TEXT,
    INDEX idx_tasks_created (created_at DESC),
    INDEX idx_tasks_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_data_task_logs (
    log_id INTEGER PRIMARY KEY AUTO_INCREMENT,
    task_id VARCHAR(128) NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    INDEX idx_logs_task (task_id, timestamp),
    INDEX idx_logs_timestamp (timestamp DESC),
    FOREIGN KEY (task_id) REFERENCES market_data_tasks(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_data_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    bundle_path VARCHAR(500) NOT NULL,
    last_modified TIMESTAMP NULL,
    total_files INTEGER,
    total_size_bytes BIGINT,
    analyzed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stock_count INTEGER DEFAULT 0,
    fund_count INTEGER DEFAULT 0,
    futures_count INTEGER DEFAULT 0,
    index_count INTEGER DEFAULT 0,
    bond_count INTEGER DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_data_cron_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled BOOLEAN DEFAULT FALSE,
    cron_expression VARCHAR(100),
    task_type VARCHAR(50),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_data_cron_config_5min (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled BOOLEAN DEFAULT FALSE,
    cron_expression VARCHAR(100),
    task_type VARCHAR(50),
    script_path VARCHAR(500),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_data_cron_logs (
    log_id INTEGER PRIMARY KEY AUTO_INCREMENT,
    task_id VARCHAR(128),
    trigger_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) NOT NULL,
    message TEXT,
    INDEX idx_cron_logs_time (trigger_time DESC),
    FOREIGN KEY (task_id) REFERENCES market_data_tasks(task_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_data_files (
    file_id INTEGER PRIMARY KEY AUTO_INCREMENT,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size BIGINT,
    modified_at TIMESTAMP NULL,
    INDEX idx_files_name (file_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS python_packages (
    package_name VARCHAR(255) PRIMARY KEY,
    version VARCHAR(100) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_packages_updated (updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 3. Backtest Strategy Rename Mapping Tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS backtest_strategy_rename_map (
    from_id VARCHAR(128) NOT NULL,
    to_id VARCHAR(128) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by VARCHAR(255) NULL,
    PRIMARY KEY (from_id),
    INDEX idx_to_id (to_id),
    INDEX idx_updated_at (updated_at),
    CHECK (from_id <> to_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- 4. Research Management Tables (for future use)
-- ============================================================================

CREATE TABLE IF NOT EXISTS research_items (
    id VARCHAR(128) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    notebook_path VARCHAR(500),
    kernel VARCHAR(50) DEFAULT 'python3',
    status VARCHAR(50) DEFAULT 'DRAFT',
    tags JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Initial Data
-- ============================================================================

-- Insert default cron config (enabled by default, runs at 04:00 on 3rd of each month)
-- 选3号而非1号，因为每月1号米筐通常还未发布当月数据包
INSERT INTO market_data_cron_config (id, enabled, cron_expression, task_type, updated_at)
VALUES (1, TRUE, '0 4 3 * *', 'full', CURRENT_TIMESTAMP)
ON DUPLICATE KEY UPDATE id=id;

-- Insert default 5min cron config (enabled by default, runs every 5 minutes)
INSERT INTO market_data_cron_config_5min (id, enabled, cron_expression, task_type, script_path, updated_at)
VALUES (1, TRUE, '*/5 * * * *', '5min', '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/scripts/fetch_all_stocks_5min.py', CURRENT_TIMESTAMP)
ON DUPLICATE KEY UPDATE id=id;

-- Note: User initialization is handled by the application
-- The application will create the default admin user on first startup

-- ============================================================================
-- 5. VnPy Futures Bar Data (in backquant database)
-- ============================================================================

CREATE TABLE IF NOT EXISTS dbbardata (
    id int(11) NOT NULL AUTO_INCREMENT,
    symbol varchar(255) NOT NULL,
    exchange varchar(255) NOT NULL,
    datetime datetime NOT NULL,
    `interval` varchar(255) NOT NULL,
    volume double NOT NULL,
    turnover double NOT NULL,
    open_interest double NOT NULL,
    open_price double NOT NULL,
    high_price double NOT NULL,
    low_price double NOT NULL,
    close_price double NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY dbbardata_symbol_exchange_interval_datetime (symbol, exchange, `interval`, datetime),
    KEY idx_dbbardata_exchange (exchange)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ============================================================================
-- 6. VNPY Stats Cache (in backquant database)
-- ============================================================================

CREATE TABLE IF NOT EXISTS vnpy_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total_rows BIGINT DEFAULT 0,
    contract_count INTEGER DEFAULT 0,
    exchange_count INTEGER DEFAULT 0,
    min_date VARCHAR(30),
    max_date VARCHAR(30),
    by_exchange JSON,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
