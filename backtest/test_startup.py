#!/usr/bin/env python3
import sys
import os

# 打印 Python 版本和路径
print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print(f"Current directory: {os.getcwd()}")

# 尝试导入必要的模块
try:
    print("\n=== Testing imports ===")
    import flask
    print(f"Flask version: {flask.__version__}")
    
    from app import create_app
    from app.config import CONFIG_ENV
    print("Successfully imported app modules")
    
    # 尝试创建应用
    print("\n=== Creating app ===")
    app = create_app(CONFIG_ENV)
    print("App created successfully")
    
    print("\n=== Startup test passed ===")
    print("The backend service should be able to start")
    
except Exception as e:
    print(f"\n=== ERROR: {type(e).__name__} ===")
    print(f"Message: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
