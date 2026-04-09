#!/usr/bin/env python3
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello, World!'

if __name__ == '__main__':
    print("Starting test server on port 54321...")
    app.run(host='0.0.0.0', port=54321, debug=True)
