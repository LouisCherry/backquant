#!/usr/bin/env python3
import sys
import os
import traceback
import logging

# 设置日志级别
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print(f"Current directory: {os.getcwd()}")

# 检查必要的文件
print("\n=== File Checks ===")
files_to_check = [
    "wsgi.py",
    "app/__init__.py",
    "app/config.py",
    "app/api/login_api.py",
    "app/api/backtest_api.py",
    "app/api/research_api.py",
    "app/api/system_api.py",
    "app/api/market_data_api.py",
    "app/api/packages_api.py",
    "app/backtest/services/runner.py",
    "app/database.py",
    "app/market_data/scheduler.py",
    "app/market_data/db_init.py",
    "app/market_data/utils.py"
]

for file_path in files_to_check:
    if os.path.exists(file_path):
        print(f"✓ {file_path} exists")
    else:
        print(f"✗ {file_path} does not exist")

# 尝试导入和启动
print("\n=== Startup Test ===")
try:
    # 加载环境变量
    if os.path.exists(".env.wsgi"):
        print("Loading environment variables from .env.wsgi")
        with open(".env.wsgi", 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if value.startswith(('"', "'")) and value.endswith(('"', "'")):
                        value = value[1:-1]
                    os.environ.setdefault(key, value)
                    print(f"  Set {key}={value}")
    
    # 导入必要的模块
    print("\nImporting modules...")
    
    # 首先导入基础模块
    import flask
    print(f"  Flask version: {flask.__version__}")
    
    # 导入应用模块
    print("  Importing app modules...")
    from app.config import CONFIG, CONFIG_ENV
    print(f"  Config environment: {CONFIG_ENV}")
    
    from app.api.login_api import bp_login
    print("  ✓ login_api imported")
    
    from app.api.backtest_api import bp_backtest
    print("  ✓ backtest_api imported")
    
    from app.api.research_api import bp_research
    print("  ✓ research_api imported")
    
    from app.api.system_api import bp_system
    print("  ✓ system_api imported")
    
    from app.api.market_data_api import bp_market_data
    print("  ✓ market_data_api imported")
    
    from app.api.packages_api import bp_packages
    print("  ✓ packages_api imported")
    
    from app.backtest.services.runner import ensure_default_demo_strategy
    print("  ✓ runner imported")
    
    from app.database import get_db_connection, get_db_type
    print("  ✓ database imported")
    
    from app.market_data.scheduler import init_scheduler
    print("  ✓ scheduler imported")
    
    print("  All modules imported successfully")
    
    # 创建应用
    print("\nCreating app...")
    app = flask.Flask(__name__)
    print("  Flask app created")
    
    from flask_cors import CORS
    CORS(app, supports_credentials=True)
    print("  CORS configured")
    
    app.config.from_object(CONFIG[CONFIG_ENV])
    print("  Config loaded")
    
    # 注册蓝图
    app.register_blueprint(bp_login)
    app.register_blueprint(bp_backtest)
    app.register_blueprint(bp_research)
    app.register_blueprint(bp_system)
    app.register_blueprint(bp_market_data)
    app.register_blueprint(bp_packages)
    print("  Blueprints registered")
    
    # 初始化应用
    print("\nInitializing app...")
    with app.app_context():
        print("  Inside app context")
        
        # 确保默认策略存在
        try:
            ensure_default_demo_strategy()
            print("  ✓ Default demo strategy ensured")
        except Exception as e:
            print(f"  ✗ Error ensuring default strategy: {e}")
        
        # 初始化数据库
        try:
            from app.market_data.db_init import init_database, init_database_with_connection
            from app.market_data.utils import get_market_data_db_path
            db_path = get_market_data_db_path()
            print(f"  Market data DB path: {db_path}")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            print("  DB directory created")
            
            if get_db_type() == "mariadb":
                print("  Using MariaDB")
                with get_db_connection("market_data") as db:
                    init_database_with_connection(db)
            else:
                print("  Using SQLite")
                init_database(db_path)
            print("  ✓ Database initialized")
        except Exception as e:
            print(f"  ✗ Error initializing database: {e}")
            traceback.print_exc()
        
        # 初始化调度器
        try:
            init_scheduler()
            print("  ✓ Scheduler initialized")
        except Exception as e:
            print(f"  ✗ Error initializing scheduler: {e}")
        
        # 初始化包缓存
        try:
            from app.api.packages_api import refresh_packages_cache
            refresh_packages_cache()
            print("  ✓ Packages cache refreshed")
        except Exception as e:
            print(f"  ✗ Error refreshing packages cache: {e}")
    
    print("\n=== App initialized successfully ===")
    print("Starting server on port 54321...")
    
    # 启动服务器
    app.run(host='0.0.0.0', debug=True, port=54321)
    
except Exception as e:
    print(f"\n=== ERROR: {type(e).__name__} ===")
    print(f"Message: {e}")
    print("\nTraceback:")
    traceback.print_exc()
    sys.exit(1)
