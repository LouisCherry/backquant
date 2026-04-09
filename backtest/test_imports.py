#!/usr/bin/env python3
#coding: utf8

# 打开文件进行写入
with open('imports_test.log', 'w') as f:
    # 重定向标准输出和标准错误
    import sys
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = f
    sys.stderr = f
    
    try:
        print("Testing imports...")
        print(f"Python version: {sys.version}")
        
        # 加载环境变量
        import os
        from pathlib import Path
        
        def _load_env_file() -> None:
            env_path = os.environ.get("WSGI_ENV_FILE")
            if env_path:
                target = Path(env_path).expanduser()
            else:
                target = Path(__file__).resolve().parent / ".env.wsgi"

            if not target.exists():
                print(f"No .env.wsgi file found at: {target}")
                return

            print(f"Loading environment variables from: {target}")
            for line in target.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
                print(f"  Set {key}={value}")

        _load_env_file()
        
        # 测试导入各个模块
        print("\nTesting imports...")
        
        print("1. Testing Flask...")
        from flask import Flask
        print("Flask imported successfully")
        
        print("2. Testing Flask-CORS...")
        from flask_cors import CORS
        print("Flask-CORS imported successfully")
        
        print("3. Testing login_api...")
        from app.api.login_api import bp_login
        print("login_api imported successfully")
        
        print("4. Testing backtest_api...")
        from app.api.backtest_api import bp_backtest
        print("backtest_api imported successfully")
        
        print("5. Testing research_api...")
        from app.api.research_api import bp_research
        print("research_api imported successfully")
        
        print("6. Testing system_api...")
        from app.api.system_api import bp_system
        print("system_api imported successfully")
        
        print("7. Testing market_data_api...")
        from app.api.market_data_api import bp_market_data
        print("market_data_api imported successfully")
        
        print("8. Testing packages_api...")
        from app.api.packages_api import bp_packages
        print("packages_api imported successfully")
        
        print("9. Testing runner...")
        from app.backtest.services.runner import ensure_default_demo_strategy
        print("runner imported successfully")
        
        print("10. Testing database...")
        from app.database import get_db_connection, get_db_type
        print("database imported successfully")
        
        print("11. Testing scheduler...")
        from app.market_data.scheduler import init_scheduler
        print("scheduler imported successfully")
        
        print("\nAll imports successful!")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 恢复标准输出和标准错误
        sys.stdout = original_stdout
        sys.stderr = original_stderr

print("Import test completed. Check imports_test.log for details.")
