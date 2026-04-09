#!/usr/bin/env python3

# 打开文件进行写入
with open('debug_output.txt', 'w') as f:
    # 写入基本信息
    f.write('=== Debug Output ===\n')
    f.write('Script started\n')
    
    # 导入必要的模块
    import sys
    f.write(f'Python version: {sys.version}\n')
    f.write(f'Python executable: {sys.executable}\n')
    
    import os
    f.write(f'Current directory: {os.getcwd()}\n')
    
    # 尝试导入 Flask
    try:
        import flask
        f.write(f'Flask version: {flask.__version__}\n')
    except ImportError as e:
        f.write(f'Error importing Flask: {e}\n')
    
    # 尝试导入应用模块
    try:
        from app import create_app
        from app.config import CONFIG_ENV
        f.write('Successfully imported app modules\n')
        
        # 尝试创建应用
        app = create_app(CONFIG_ENV)
        f.write('App created successfully\n')
        
    except Exception as e:
        f.write(f'Error importing or creating app: {e}\n')
        import traceback
        traceback.print_exc(file=f)
    
    f.write('Script completed\n')

print('Debug output written to debug_output.txt')
