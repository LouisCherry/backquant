#!/usr/bin/env python3

import sys
import os
import subprocess

# 写入系统信息到文件
with open('debug_output.txt', 'w') as f:
    f.write("=== System Information ===\n")
    f.write(f"Python version: {sys.version}\n")
    f.write(f"Current directory: {os.getcwd()}\n")
    f.write(f"Python path: {sys.path}\n")
    
    f.write("\n=== Environment Variables ===\n")
    for key, value in os.environ.items():
        if 'PYTHON' in key or 'PATH' in key or 'BACKTEST' in key:
            f.write(f"  {key}: {value}\n")
    
    f.write("\n=== Trying to start backend service ===\n")
    try:
        # 尝试运行后端服务，捕获输出
        result = subprocess.run(
            [sys.executable, 'wsgi.py'],
            capture_output=True,
            text=True,
            timeout=10
        )
        f.write(f"Return code: {result.returncode}\n")
        f.write("\n=== stdout ===\n")
        f.write(result.stdout)
        f.write("\n=== stderr ===\n")
        f.write(result.stderr)
    except subprocess.TimeoutExpired:
        f.write("Error: Timeout expired\n")
    except Exception as e:
        f.write(f"Error: {e}\n")

print("Debug information written to debug_output.txt")
