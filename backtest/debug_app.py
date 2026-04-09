#!/usr/bin/env python3

# 打开文件进行写入
with open('app_debug.log', 'w') as f:
    # 写入基本信息
    f.write('=== App Debug Output ===\n')
    f.write('Script started\n')
    
    # 导入必要的模块
    import sys
    f.write(f'Python version: {sys.version}\n')
    f.write(f'Python executable: {sys.executable}\n')
    
    import os
    f.write(f'Current directory: {os.getcwd()}\n')
    
    # 加载环境变量
    f.write('\n=== Loading environment variables ===\n')
    from pathlib import Path
    
    def _load_env_file() -> None:
        env_path = os.environ.get("WSGI_ENV_FILE")
        if env_path:
            target = Path(env_path).expanduser()
        else:
            target = Path(__file__).resolve().parent / ".env.wsgi"

        if not target.exists():
            f.write(f"No .env.wsgi file found at: {target}\n")
            return

        f.write(f"Loading environment variables from: {target}\n")
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
            f.write(f"  Set {key}={value}\n")

    _load_env_file()
    
    # 导入应用模块
    f.write('\n=== Importing app modules ===\n')
    try:
        from app import create_app
        from app.config import CONFIG_ENV
        f.write(f'Successfully imported app modules. Config environment: {CONFIG_ENV}\n')
        
        # 尝试创建应用
        f.write('\n=== Creating app ===\n')
        app = create_app(CONFIG_ENV)
        f.write('App created successfully\n')
        
        # 打印应用配置
        f.write('\n=== App configuration ===\n')
        for key, value in app.config.items():
            if key not in ['SECRET_KEY', 'LOCAL_AUTH_PASSWORD', 'LOCAL_AUTH_PASSWORD_HASH']:
                f.write(f'  {key}: {value}\n')
        
        f.write('\n=== App creation completed successfully ===\n')
        
    except Exception as e:
        f.write(f'Error importing or creating app: {e}\n')
        import traceback
        traceback.print_exc(file=f)
    
    f.write('\nScript completed\n')

print('App debug output written to app_debug.log')
