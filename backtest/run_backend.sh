#!/bin/bash

# 进入 backtest 目录
cd "$(dirname "$0")"

# 激活虚拟环境
source venv/bin/activate

# 启动后端服务并将输出写入文件
python wsgi.py > backend_output.log 2>&1 &

# 等待几秒钟
sleep 5

# 检查是否有进程在运行
ps aux | grep python | grep wsgi.py | grep -v grep > process.log

# 检查端口是否被占用
lsof -i :54321 > port.log 2>&1

# 显示结果
echo "Backend service started. Check backend_output.log for details."
