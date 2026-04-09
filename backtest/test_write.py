#!/usr/bin/env python3
#coding: utf8

# 写入文件测试
with open('test_output.txt', 'w') as f:
    f.write('Hello, World!\n')
    f.write('Testing file write operation...\n')

print('Test completed successfully!')
