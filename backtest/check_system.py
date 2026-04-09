#!/usr/bin/env python3

import sys
import os
import platform

# 写入系统信息到文件
with open('system_info.txt', 'w') as f:
    f.write(f"Python version: {sys.version}\n")
    f.write(f"Platform: {platform.platform()}\n")
    f.write(f"OS: {platform.system()} {platform.release()}\n")
    f.write(f"Current directory: {os.getcwd()}\n")
    f.write(f"Python path: {sys.path}\n")
    f.write(f"Environment variables:\n")
    for key, value in os.environ.items():
        if 'PYTHON' in key or 'PATH' in key:
            f.write(f"  {key}: {value}\n")

print("System information written to system_info.txt")
