#!/usr/bin/env python3
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello from Simple Flask App!'

@app.route('/api/test')
def test_api():
    return {'message': 'API test successful'}

if __name__ == '__main__':
    print("Starting simple Flask server on port 54321...")
    app.run(host='0.0.0.0', port=54321, debug=True)
