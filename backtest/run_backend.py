#!/usr/bin/env python3

# 打开文件进行写入
with open('backend_output.log', 'w') as f:
    # 重定向标准输出和标准错误
    import sys
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = f
    sys.stderr = f
    
    try:
        print("Starting backend service...")
        print("Python version:", sys.version)
        
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
        
        # 导入并运行后端服务
        from app import create_app
        from app.config import CONFIG_ENV
        
        print(f"Creating app with config: {CONFIG_ENV}")
        app = create_app(CONFIG_ENV)
        print("App created successfully")
        
        print("Starting server on port 54321...")
        app.run(host='0.0.0.0', debug=True, port=54321)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 恢复标准输出和标准错误
        sys.stdout = original_stdout
        sys.stderr = original_stderr

print("Backend service output written to backend_output.log")
