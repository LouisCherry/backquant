#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新 admin 密码脚本
"""

import sqlite3
import bcrypt
import os
from pathlib import Path


def update_admin_password(new_password):
    """更新 admin 密码"""
    # 获取认证数据库路径
    backtest_base_dir = '/Users/panshunxing/eclipse-workspace/BackQuant/backquant/backtest/data/backtest'
    auth_db_path = os.path.join(backtest_base_dir, 'auth.sqlite3')
    
    print(f"认证数据库路径: {auth_db_path}")
    
    # 检查数据库文件是否存在
    if not os.path.exists(auth_db_path):
        print("错误：认证数据库文件不存在！")
        return False
    
    # 连接数据库
    conn = sqlite3.connect(auth_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # 生成新密码的哈希值
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # 更新 admin 用户的密码
        cursor.execute("""
            UPDATE users 
            SET password_hash = ? 
            WHERE username = 'admin'
        """, (hashed_password,))
        
        # 提交更改
        conn.commit()
        
        # 检查更新是否成功
        if cursor.rowcount > 0:
            print("成功：admin 密码已更新为 ZAQ@123wsx")
            return True
        else:
            print("错误：未找到 admin 用户！")
            return False
    except Exception as e:
        print(f"错误：更新密码时发生错误: {e}")
        return False
    finally:
        # 关闭数据库连接
        conn.close()


if __name__ == '__main__':
    # 新密码
    new_password = 'ZAQ@123wsx'
    
    # 执行密码更新
    update_admin_password(new_password)
