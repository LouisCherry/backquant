#!/usr/bin/env python3

import sqlite3
import bcrypt
from pathlib import Path

# 获取认证数据库路径
auth_db_path = Path('data/backtest/auth.sqlite3')

# 生成新的密码哈希
password = 'ZAQ@123wsx'
hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

print(f"Generated password hash: {hashed_password}")

# 连接数据库并更新密码
try:
    conn = sqlite3.connect(auth_db_path)
    cursor = conn.cursor()
    
    # 更新管理员用户的密码
    cursor.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'", (hashed_password,))
    
    # 提交更改
    conn.commit()
    
    print("Password updated successfully!")
    
    # 验证更新是否成功
    cursor.execute("SELECT password_hash FROM users WHERE username = 'admin'")
    result = cursor.fetchone()
    if result:
        print("Updated password hash in database:", result[0])
    
    conn.close()
except Exception as e:
    print(f"Error updating password: {e}")
