#!/bin/bash

# 进入 backtest 目录
cd "$(dirname "$0")"

# 写入系统信息到文件
echo "=== System Information ===" > debug_all.log
echo "$(date)" >> debug_all.log
echo "Current directory: $(pwd)" >> debug_all.log
echo "Python version: $(python --version 2>&1)" >> debug_all.log
echo "Python3 version: $(python3 --version 2>&1)" >> debug_all.log
echo "Docker version: $(docker --version 2>&1)" >> debug_all.log
echo "Docker Compose version: $(docker-compose --version 2>&1)" >> debug_all.log
echo "Port 54321 status: $(lsof -i :54321 2>&1 || echo "No process")" >> debug_all.log

# 检查虚拟环境
echo "\n=== Virtual Environment ===" >> debug_all.log
echo "Virtual environment exists: $(ls -la venv/ 2>&1)" >> debug_all.log

# 检查依赖
echo "\n=== Dependencies ===" >> debug_all.log
echo "$(source venv/bin/activate && pip list | head -20)" >> debug_all.log

# 尝试启动后端服务
echo "\n=== Starting Backend Service ===" >> debug_all.log
source venv/bin/activate
python wsgi.py > backend.log 2>&1 &

# 等待几秒钟
sleep 5

# 检查进程
echo "\n=== Process Status ===" >> debug_all.log
echo "$(ps aux | grep python | grep wsgi.py | grep -v grep)" >> debug_all.log

# 检查端口
echo "\n=== Port Status ===" >> debug_all.log
echo "$(lsof -i :54321 2>&1)" >> debug_all.log

# 显示结果
echo "Debug information written to debug_all.log"
