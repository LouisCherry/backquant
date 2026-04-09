# BackQuant 系统数据库说明文档

## 1. 文档简介

本文档详细说明 BackQuant 系统的数据库设计，包括数据库类型、配置参数、表结构、数据存储结构以及初始化和维护相关内容。

## 2. 数据库类型

BackQuant 系统支持两种数据库类型，可根据部署环境选择：

| 数据库类型 | 默认配置 | 适用场景 |
|---------|---------|--------|
| SQLite | 开发环境默认 | 轻量级本地存储，适合开发和测试环境 |
| MariaDB | 生产环境推荐 | 高性能关系型数据库，适合生产环境部署 |

## 3. 数据库配置

### 3.1 核心配置参数

| 配置项 | 描述 | 默认值 | 备注 |
|-------|------|-------|------|
| DB_TYPE | 数据库类型 (sqlite/mariadb) | sqlite | 开发环境建议使用 sqlite |
| DB_HOST | 数据库主机地址 | localhost | MariaDB 部署时需指定 |
| DB_PORT | 数据库端口 | 3306 | MariaDB 默认端口 |
| DB_NAME | 数据库名称 | backquant | 生产环境建议使用专用数据库 |
| DB_USER | 数据库用户名 | root | 生产环境建议创建专用用户 |
| DB_PASSWORD | 数据库密码 | (空) | 生产环境必须设置强密码 |

### 3.2 SQLite 数据库文件

系统使用多个 SQLite 数据库文件，按功能分类存储：

| 数据库文件 | 功能用途 | 默认存储路径 |
|---------|---------|-----------|
| auth.sqlite3 | 认证与用户管理 | `<BACKTEST_BASE_DIR>/auth.sqlite3` |
| backtest_meta.sqlite3 | 回测策略元数据 | `<BACKTEST_BASE_DIR>/backtest_meta.sqlite3` |
| market_data.sqlite3 | 市场数据管理 | `<BACKTEST_BASE_DIR>/market_data.sqlite3` |

## 4. 数据库表结构

### 4.1 认证管理表

#### users 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| id | INTEGER | PRIMARY KEY AUTO_INCREMENT | 用户唯一标识 |
| username | VARCHAR(255) | NOT NULL UNIQUE | 登录用户名 |
| password_hash | VARCHAR(255) | NOT NULL | 密码哈希值（bcrypt加密） |
| is_admin | BOOLEAN | DEFAULT FALSE | 是否为管理员权限 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 用户创建时间 |

### 4.2 市场数据管理表

#### market_data_tasks 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| task_id | VARCHAR(128) | PRIMARY KEY | 任务唯一标识 |
| task_type | VARCHAR(50) | NOT NULL | 任务类型（如数据下载、更新等） |
| status | VARCHAR(50) | NOT NULL | 任务状态（如待处理、运行中、完成、失败） |
| progress | INTEGER | DEFAULT 0 | 任务进度（百分比） |
| stage | VARCHAR(100) | | 当前任务阶段描述 |
| message | TEXT | | 任务相关消息 |
| source | VARCHAR(50) | | 数据来源（如 AKShare、Baostock） |
| created_at | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP | 任务创建时间 |
| started_at | TIMESTAMP | NULL | 任务开始执行时间 |
| finished_at | TIMESTAMP | NULL | 任务完成时间 |
| error | TEXT | | 任务执行错误信息 |

#### market_data_task_logs 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| log_id | INTEGER | PRIMARY KEY AUTO_INCREMENT | 日志唯一标识 |
| task_id | VARCHAR(128) | NOT NULL | 关联的任务ID |
| timestamp | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP | 日志记录时间 |
| level | VARCHAR(20) | NOT NULL | 日志级别（如 INFO、ERROR） |
| message | TEXT | NOT NULL | 日志详细内容 |

#### market_data_stats 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| id | INTEGER | PRIMARY KEY CHECK (id = 1) | 固定为1，单条记录 |
| bundle_path | VARCHAR(500) | NOT NULL | 数据 bundle 存储路径 |
| last_modified | TIMESTAMP | NULL | 数据最后修改时间 |
| total_files | INTEGER | | 数据文件总数 |
| total_size_bytes | BIGINT | | 数据总大小（字节） |
| analyzed_at | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP | 数据统计分析时间 |
| stock_count | INTEGER | DEFAULT 0 | 股票数据数量 |
| fund_count | INTEGER | DEFAULT 0 | 基金数据数量 |
| futures_count | INTEGER | DEFAULT 0 | 期货数据数量 |
| index_count | INTEGER | DEFAULT 0 | 指数数据数量 |
| bond_count | INTEGER | DEFAULT 0 | 债券数据数量 |

#### market_data_cron_config 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| id | INTEGER | PRIMARY KEY CHECK (id = 1) | 固定为1，单条记录 |
| enabled | BOOLEAN | DEFAULT FALSE | 是否启用定时任务 |
| cron_expression | VARCHAR(100) | | Cron 表达式（定时执行规则） |
| task_type | VARCHAR(50) | | 任务类型 |
| updated_at | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP | 配置更新时间 |

#### market_data_cron_logs 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| log_id | INTEGER | PRIMARY KEY AUTO_INCREMENT | 日志唯一标识 |
| task_id | VARCHAR(128) | | 关联的任务ID |
| trigger_time | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP | 任务触发时间 |
| status | VARCHAR(50) | NOT NULL | 触发状态 |
| message | TEXT | | 触发相关消息 |

#### market_data_files 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| file_id | INTEGER | PRIMARY KEY AUTO_INCREMENT | 文件唯一标识 |
| file_name | VARCHAR(255) | NOT NULL | 文件名 |
| file_path | VARCHAR(500) | NOT NULL | 文件存储路径 |
| file_size | BIGINT | | 文件大小（字节） |
| modified_at | TIMESTAMP | NULL | 文件最后修改时间 |

#### python_packages 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| package_name | VARCHAR(255) | PRIMARY KEY | Python 包名 |
| version | VARCHAR(100) | NOT NULL | 包版本号 |
| updated_at | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

### 4.3 回测策略管理表

#### backtest_strategy_rename_map 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| from_id | VARCHAR(128) | NOT NULL PRIMARY KEY | 原策略ID |
| to_id | VARCHAR(128) | NOT NULL | 新策略ID |
| updated_at | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP | 更新时间 |
| updated_by | VARCHAR(255) | NULL | 更新操作人 |

### 4.4 研究管理表

#### research_items 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| id | VARCHAR(128) | PRIMARY KEY | 研究项目唯一标识 |
| title | VARCHAR(255) | NOT NULL | 研究项目标题 |
| description | TEXT | | 研究项目描述 |
| notebook_path | VARCHAR(500) | | Jupyter 笔记本文件路径 |
| kernel | VARCHAR(50) | DEFAULT 'python3' | 笔记本内核类型 |
| status | VARCHAR(50) | DEFAULT 'DRAFT' | 项目状态（如草稿、发布） |
| tags | JSON | | 项目标签（JSON格式） |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

### 4.5 VnPy 期货数据相关表

#### dbbardata 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| id | int(11) | NOT NULL AUTO_INCREMENT PRIMARY KEY | 数据唯一标识 |
| symbol | varchar(255) | NOT NULL | 期货合约代码 |
| exchange | varchar(255) | NOT NULL | 交易所名称 |
| datetime | datetime | NOT NULL | 数据时间戳 |
| interval | varchar(255) | NOT NULL | 数据周期（如 1m、5m、1d） |
| volume | double | NOT NULL | 成交量 |
| turnover | double | NOT NULL | 成交额 |
| open_interest | double | NOT NULL | 持仓量 |
| open_price | double | NOT NULL | 开盘价 |
| high_price | double | NOT NULL | 最高价 |
| low_price | double | NOT NULL | 最低价 |
| close_price | double | NOT NULL | 收盘价 |

#### vnpy_stats 表
| 字段名 | 数据类型 | 约束 | 描述 |
|-------|---------|------|------|
| id | INTEGER | PRIMARY KEY CHECK (id = 1) | 固定为1，单条记录 |
| total_rows | BIGINT | DEFAULT 0 | 期货数据总行数 |
| contract_count | INTEGER | DEFAULT 0 | 合约数量 |
| exchange_count | INTEGER | DEFAULT 0 | 交易所数量 |
| min_date | VARCHAR(30) | | 最早数据日期 |
| max_date | VARCHAR(30) | | 最新数据日期 |
| by_exchange | JSON | | 按交易所统计数据（JSON格式） |
| updated_at | TIMESTAMP | NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

## 5. 数据存储结构

除数据库表外，系统还使用文件系统存储以下数据：

| 存储路径 | 用途 | 存储内容 |
|---------|------|--------|
| `<BACKTEST_BASE_DIR>/strategies/` | 策略文件管理 | 策略代码文件（.py）和元数据文件（.meta.json） |
| `<BACKTEST_BASE_DIR>/runs/` | 回测运行结果 | 按日期组织的回测任务结果，包含配置、策略、结果文件等 |
| `<RQALPHA_BUNDLE_PATH>` | RQAlpha 数据 bundle | 市场数据文件（如 stocks.h5、indexes.h5 等） |

## 6. 数据库初始化

系统启动时会自动初始化数据库：

- **SQLite**：通过应用程序代码创建所需表结构
- **MariaDB**：通过 `db/init.sql` 脚本创建表结构

默认会创建管理员用户（具体由应用程序处理，初始密码可通过环境变量配置）。

## 7. 定时任务配置

系统默认配置了市场数据更新的定时任务：

| 配置项 | 值 | 说明 |
|-------|-----|------|
| Cron 表达式 | `0 4 3 * *` | 每月3日凌晨4点执行 |
| 任务类型 | `full` | 完整更新市场数据 |
| 状态 | 默认启用 | 可通过管理界面调整 |

选择3号执行是因为每月1号 RQAlpha 通常还未发布当月数据包。

## 8. 维护建议

1. **定期备份**：建议定期备份数据库文件和存储目录，特别是生产环境
2. **性能优化**：对于 MariaDB 部署，可根据数据量调整配置参数
3. **数据清理**：定期清理过期的回测任务和日志数据
4. **安全管理**：生产环境应设置强密码，限制数据库访问权限

## 9. 附录

### 9.1 索引说明

系统在关键表上创建了索引以优化查询性能：

- `users` 表：`idx_username`（用户名索引）
- `market_data_tasks` 表：`idx_tasks_created`（创建时间索引）、`idx_tasks_status`（状态索引）
- `market_data_task_logs` 表：`idx_logs_task`（任务ID和时间索引）、`idx_logs_timestamp`（时间索引）
- `market_data_cron_logs` 表：`idx_cron_logs_time`（触发时间索引）
- `market_data_files` 表：`idx_files_name`（文件名索引）
- `python_packages` 表：`idx_packages_updated`（更新时间索引）
- `backtest_strategy_rename_map` 表：`idx_to_id`（新策略ID索引）、`idx_updated_at`（更新时间索引）
- `research_items` 表：`idx_status`（状态索引）、`idx_created`（创建时间索引）
- `dbbardata` 表：`dbbardata_symbol_exchange_interval_datetime`（唯一索引）、`idx_dbbardata_exchange`（交易所索引）

### 9.2 外键关系

系统使用外键约束确保数据完整性：

- `market_data_task_logs.task_id` → `market_data_tasks.task_id`（级联删除）
- `market_data_cron_logs.task_id` → `market_data_tasks.task_id`（设置为NULL）