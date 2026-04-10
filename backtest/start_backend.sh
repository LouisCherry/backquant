#!/bin/bash

# 启动简单的后端服务
echo "Starting simple backend service..."

# 确保虚拟环境存在
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo "Activating virtual environment..."
source venv/bin/activate

# 安装必要的依赖
echo "Installing dependencies..."
pip install flask flask-cors

# 运行简单的后端服务
echo "Running simple backend service..."
python simple_backend.py
