#!/usr/bin/env python3
#coding: utf8

import sys
import os
from pathlib import Path

# 加载环境变量
def load_env_file() -> None:
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

# 检查依赖
def check_dependencies():
    print("\nChecking dependencies...")
    required_packages = [
        'flask',
        'flask-cors',
        'apscheduler',
        'pymysql',
        'sqlite3',
        'pathlib',
        'datetime',
        'logging'
    ]
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✓ {package} is installed")
        except ImportError:
            print(f"  ✗ {package} is not installed")

# 检查配置
def check_config():
    print("\nChecking configuration...")
    print(f"  BACKTEST_BASE_DIR: {os.environ.get('BACKTEST_BASE_DIR', 'Not set')}")
    print(f"  RQALPHA_BUNDLE_PATH: {os.environ.get('RQALPHA_BUNDLE_PATH', 'Not set')}")
    print(f"  SECRET_KEY: {os.environ.get('SECRET_KEY', 'Not set')}")
    print(f"  LOCAL_AUTH_MOBILE: {os.environ.get('LOCAL_AUTH_MOBILE', 'Not set')}")
    print(f"  LOCAL_AUTH_PASSWORD: {os.environ.get('LOCAL_AUTH_PASSWORD', 'Not set')}")

# 检查数据库连接
def check_database():
    print("\nChecking database...")
    try:
        import sqlite3
        # 尝试连接到一个临时数据库
        conn = sqlite3.connect(':memory:')
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE test (id INTEGER PRIMARY KEY)')
        cursor.execute('INSERT INTO test VALUES (1)')
        result = cursor.execute('SELECT * FROM test').fetchone()
        print(f"  ✓ SQLite connection successful: {result}")
        conn.close()
    except Exception as e:
        print(f"  ✗ SQLite connection failed: {e}")

# 检查端口
def check_port():
    print("\nChecking port 54321...")
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', 54321))
    if result == 0:
        print("  ✗ Port 54321 is already in use")
    else:
        print("  ✓ Port 54321 is available")
    sock.close()

# 测试导入
def test_imports():
    print("\nTesting imports...")
    try:
        from app import create_app
        print("  ✓ app.create_app imported successfully")
    except Exception as e:
        print(f"  ✗ app.create_app import failed: {e}")
    
    try:
        from app.config import CONFIG_ENV
        print(f"  ✓ app.config.CONFIG_ENV imported successfully: {CONFIG_ENV}")
    except Exception as e:
        print(f"  ✗ app.config import failed: {e}")

if __name__ == '__main__':
    print("Testing backend service...")
    print(f"Python version: {sys.version}")
    print(f"Current directory: {os.getcwd()}")
    
    load_env_file()
    check_dependencies()
    check_config()
    check_database()
    check_port()
    test_imports()
    
    print("\nTest completed.")
