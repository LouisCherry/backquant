# BackQuant 量化回测平台

[English](README_EN.md) | 简体中文

> 重要提示：示例网站已上线，请轻操：https://my.clawbot.help/    
> 默认用户名/密码：`admin` / `pass123456`

<u>**完全本地化部署，策略和数据本地运行，不依赖第三方平台，既保障隐私安全，又具备高度可定制性。**</u>

本仓库包含后端（Flask + RQAlpha）与前端（Vue 3）两部分，并提供 Research 工作台（Jupyter Lab）集成能力。
支持 **RQAlpha 股票日线回测** 与 **VnPy 期货 CTA 策略可视化回测**，期货数据从 rqalpha bundle 一键导入 MariaDB。

## 一、项目目录结构

```
backquant/
├── backtest/           # 后端代码
│   ├── app/            # Flask 应用
│   ├── data/           # 数据存储
│   │   ├── backtest/   # 回测结果、策略文件、日志
│   │   ├── rqalpha/    # RQAlpha 行情数据包
│   │   └── notebooks/  # Jupyter Notebook 文件
│   ├── scripts/        # 脚本工具
│   ├── venv/           # Python 虚拟环境
│   ├── requirements.txt # 依赖包列表
│   └── wsgi.py         # 应用入口
├── frontend/           # 前端代码
│   ├── public/         # 静态资源
│   ├── src/            # 源代码
│   ├── package.json    # 依赖配置
│   └── vue.config.js   # Vue 配置
├── .env.example        # 环境变量示例
└── README.md           # 项目说明文档
```

## 二、功能点

### 1. 回测功能
- **RQAlpha 股票日线回测**：支持基于 RQAlpha 框架的股票策略回测
- **策略管理**：支持策略的创建、编辑、删除和运行
- **回测结果分析**：提供详细的回测结果分析，包括收益率、最大回撤、夏普比率等指标

### 2. 研究功能
- **Jupyter Lab 集成**：内置 Jupyter Lab，支持交互式数据分析和策略研究
- **数据可视化**：支持各种数据可视化工具，方便策略研究和分析

### 3. 数据管理
- **RQAlpha 行情数据**：内置 RQAlpha 行情数据，时间范围为 200501 至 202602
- **数据自动更新**：支持行情数据的自动更新

### 4. 系统管理
- **用户管理**：支持用户的创建、编辑和删除
- **系统配置**：支持系统参数的配置和管理

## 三、数据库表结构

### 1. 回测元数据（SQLite）
- **backtest_meta.sqlite3**：存储回测结果的元数据
  - `backtest_runs`：回测运行记录
  - `backtest_results`：回测结果详情
  - `strategies`：策略信息

### 2. 认证数据（SQLite）
- **auth.sqlite3**：存储用户认证信息
  - `users`：用户信息
  - `roles`：用户角色

## 四、部署方式

### 1. Docker 安装与部署（推荐）

#### 安装 Docker

```bash
sudo curl -fsSL https://get.docker.com | sh
```

#### 安装前注意事项

1. **RQAlpha 行情数据时间范围为 200501 至 202602**，压缩包约 1G，下载解压耗时较长。
2. **Docker 构建完成后需等待行情下载完成才能登录**。
3. **系统运行请至少准备 5G 硬盘空间**。

#### 安装与启动（Docker Compose）

Docker Compose 默认使用 named volume 持久化所有数据，下载逻辑在容器 entrypoint 内完成：
首次启动会下载行情数据到 `/data/rqalpha/bundle`，之后复用同一 volume，不会重复下载。

```bash
cp .env.example .env
docker compose up --build -d
```

### 2. 非 Docker 部署

#### 后端部署

```bash
cd backtest
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.wsgi.example .env.wsgi  # 如果文件不存在，参考 .env.example 创建
python wsgi.py
```

#### 前端部署

```bash
cd frontend
npm install
npm run serve
```

#### Jupyter Lab 部署

```bash
cd backtest
source venv/bin/activate
jupyter lab --ip=0.0.0.0 --port=8889 --no-browser --ServerApp.base_url=/jupyter --ServerApp.root_dir=data/notebooks --ServerApp.token='' --ServerApp.password='' --ServerApp.allow_origin='*' --ServerApp.allow_credentials=True
```

### RQAlpha 与日线数据

- 已内置 RQAlpha（`rqalpha==6.1.2`）。
- 已预装常用量化库：`numpy`、`pandas`、`statsmodels`、`scikit-learn`
- 内置一个默认策略 `demo`，可直接在策略列表中运行。
- 日线数据按月更新：默认每月 1 日 03:00 运行更新任务。
- 如需调整更新时间，设置环境变量 `RQALPHA_BUNDLE_CRON`（例如 `0 4 1 * *`）。
- 如需关闭自动更新，设置 `RQALPHA_BUNDLE_CRON=off`。
- 如需跳过首次下载，设置 `RQALPHA_BUNDLE_BOOTSTRAP=0`（仅建议已手动准备好 bundle 时使用）。

### 访问

- **前端**：`http://localhost:8081`
- **后端 API**：`http://localhost:54321`
- **Jupyter Lab**：`http://localhost:8889/jupyter/lab`
- 首次登录账号/密码：`admin` / `pass123456`

> 注意：根据项目规则，端口配置已固定，不得随意修改。

### 系统截图

![Screenshot 0](images/screen0.png?v=2)
![Screenshot 1](images/screen1.png?v=2)
![Screenshot 3](images/screen3.png?v=2)

## 二、配置说明

### 后端配置

后端主要配置在 `backtest/.env.wsgi`：

- `SECRET_KEY` JWT 签名密钥，必须修改
- `LOCAL_AUTH_MOBILE` / `LOCAL_AUTH_PASSWORD` 默认管理员用户名/密码（首次初始化写入数据库）
- `LOCAL_AUTH_PASSWORD_HASH` 可选，bcrypt hash 优先级高于明文密码
- `RQALPHA_BUNDLE_PATH` RQAlpha 数据 bundle 路径
- `BACKTEST_BASE_DIR` 回测数据存储目录
- `RESEARCH_NOTEBOOK_*` Jupyter 相关配置
  - `RESEARCH_NOTEBOOK_API_BASE` Jupyter API 地址，默认：`http://localhost:8889/jupyter`
- 说明：Jupyter token 可不设置（空值表示不启用 token 鉴权，仅建议用于内网/本机）。

### 前端配置

前端支持两种方式配置 API 基址：

- 构建时环境变量 `VUE_APP_API_BASE`
- 运行时 `frontend/public/config.js`（无需重新构建），默认：`http://localhost:54321`

### 端口配置

根据项目规则，端口配置已固定：
- **前端服务**：8081
- **后端服务**：54321
- **Jupyter Lab**：8889

> 注意：不得随意修改端口号，确保开发、测试和生产环境的一致性。

## 三、其他的

### 数据持久化

#### Docker 部署

所有重要数据均存储在 Docker named volume 中，**重建镜像、升级、重启容器均不会丢失数据**：

| Volume 名称 | 挂载路径 | 存储内容 |
|------------|---------|---------|
| `mariadb_data` | MariaDB `/var/lib/mysql` | 数据库（用户、市场任务、回测元数据等） |
| `backtest_data` | 容器 `/data/backtest` | 回测结果、策略文件、日志 |
| `rqalpha_bundle` | 容器 `/data/rqalpha/bundle` | RQAlpha 行情数据包 |
| `notebooks` | 容器 `/data/notebooks` | Jupyter Notebook 文件 |

**常用操作对数据的影响：**

```bash
docker compose build          # ✅ 安全：只重建镜像，volume 不受影响
docker compose up -d          # ✅ 安全：容器重建时 volume 自动重新挂载
docker compose down           # ✅ 安全：停止并删除容器，volume 保留
docker compose down -v        # ⚠️ 危险：会同时删除所有 volume，数据永久丢失
```

#### 非 Docker 部署

数据存储在本地文件系统中：

| 存储路径 | 存储内容 |
|---------|---------|
| `backtest/data/backtest` | 回测结果、策略文件、日志 |
| `backtest/data/rqalpha/bundle` | RQAlpha 行情数据包 |
| `backtest/data/notebooks` | Jupyter Notebook 文件 |
| `backtest/data/backtest/backtest_meta.sqlite3` | 回测元数据（SQLite 数据库） |

### Jupyter 示例

- 示例 Notebook：`docs/notebooks/example.ipynb`
- 详细说明：`docs/jupyter.md`

### Nginx 反代说明

生产环境可参考 `docs/nginx.md`。

### API 文档

后端 API 说明见 `backtest/README.md`。

### License

Apache-2.0. See `LICENSE`.
