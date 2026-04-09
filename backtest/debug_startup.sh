#!/bin/bash

# 详细的启动脚本，将所有输出写入文件

LOG_FILE="detailed_startup.log"
echo "$(date) - Starting backend service startup process" > $LOG_FILE

echo "=== Environment Check ===" >> $LOG_FILE
echo "Current directory: $(pwd)" >> $LOG_FILE
echo "Python version: $(python3 --version)" >> $LOG_FILE
echo "Virtual environment: $VIRTUAL_ENV" >> $LOG_FILE

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "=== Creating virtual environment ===" >> $LOG_FILE
    python3 -m venv venv >> $LOG_FILE 2>&1
    if [ $? -ne 0 ]; then
        echo "Failed to create virtual environment" >> $LOG_FILE
        exit 1
    fi
fi

# 激活虚拟环境
echo "=== Activating virtual environment ===" >> $LOG_FILE
source venv/bin/activate >> $LOG_FILE 2>&1

# 安装依赖
echo "=== Installing dependencies ===" >> $LOG_FILE
pip install -r requirements.txt >> $LOG_FILE 2>&1
if [ $? -ne 0 ]; then
    echo "Failed to install dependencies" >> $LOG_FILE
    exit 1
fi

# 检查环境变量文件
if [ -f ".env.wsgi" ]; then
    echo "=== Environment variables ===" >> $LOG_FILE
    cat .env.wsgi >> $LOG_FILE
else
    echo "=== No .env.wsgi file found ===" >> $LOG_FILE
fi

# 启动后端服务
echo "=== Starting backend service ===" >> $LOG_FILE
echo "Running: python wsgi.py" >> $LOG_FILE
python wsgi.py >> $LOG_FILE 2>&1 &

# 保存进程ID
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID" >> $LOG_FILE

# 等待服务启动
echo "=== Waiting for service to start ===" >> $LOG_FILE
sleep 5

# 检查服务状态
echo "=== Checking service status ===" >> $LOG_FILE
if ps -p $BACKEND_PID > /dev/null; then
    echo "Backend service is running with PID: $BACKEND_PID" >> $LOG_FILE
else
    echo "Backend service failed to start" >> $LOG_FILE
fi

# 检查端口
echo "=== Checking port 54321 ===" >> $LOG_FILE
lsof -i :54321 >> $LOG_FILE 2>&1
if [ $? -ne 0 ]; then
    echo "Port 54321 is not in use" >> $LOG_FILE
fi

# 检查日志文件大小
echo "=== Log file information ===" >> $LOG_FILE
echo "Log file size: $(du -h $LOG_FILE | cut -f1)" >> $LOG_FILE

echo "$(date) - Startup process completed" >> $LOG_FILE

echo "Startup process completed. Check $LOG_FILE for details."
