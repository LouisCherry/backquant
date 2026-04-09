#!/opt/anaconda3/bin/python3
#coding: utf8

import sys
import os
from pathlib import Path

# 重定向标准输出和标准错误到文件
log_file = open('wsgi.log', 'w')
sys.stdout = log_file
sys.stderr = log_file

print("Starting wsgi.py...")
print(f"Python version: {sys.version}")
print(f"Current directory: {os.getcwd()}")


def _load_env_file() -> None:
    print("Loading environment file...")
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


# Ensure app config can read env vars before importing app modules.
_load_env_file()

print("Importing create_app...")
try:
    from app import create_app
    print("Imported create_app successfully")
except Exception as e:
    print(f"Error importing create_app: {e}")
    import traceback
    traceback.print_exc()
    log_file.close()
    sys.exit(1)

print("Importing CONFIG_ENV...")
try:
    from app.config import CONFIG_ENV
    print(f"CONFIG_ENV: {CONFIG_ENV}")
except Exception as e:
    print(f"Error importing CONFIG_ENV: {e}")
    import traceback
    traceback.print_exc()
    log_file.close()
    sys.exit(1)

print("Calling create_app...")
try:
    app = create_app(CONFIG_ENV)
    print("create_app completed successfully")
except Exception as e:
    print(f"Error calling create_app: {e}")
    import traceback
    traceback.print_exc()
    log_file.close()
    sys.exit(1)

print("Starting server...")
try:
    app.run(host='0.0.0.0', debug=True, port=54321)
except Exception as e:
    print(f"Error starting server: {e}")
    import traceback
    traceback.print_exc()
    log_file.close()
    sys.exit(1)

log_file.close()
