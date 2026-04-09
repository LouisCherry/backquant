#!/usr/bin/env python3

print("Testing Python environment...")
print("Hello, World!")

# 尝试导入一些常见的库
try:
    import os
    print("os module imported successfully")
except Exception as e:
    print(f"Error importing os: {e}")

try:
    import sys
    print(f"Python version: {sys.version}")
except Exception as e:
    print(f"Error importing sys: {e}")

try:
    import flask
    print(f"Flask version: {flask.__version__}")
except Exception as e:
    print(f"Error importing flask: {e}")
