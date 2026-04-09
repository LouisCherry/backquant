#!/usr/bin/env python3
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True)

@app.route('/api/test')
def test_api():
    return jsonify({'message': 'Backend service is running!'})

@app.route('/api/backtest/strategies')
def get_strategies():
    return jsonify([{'id': 'demo', 'name': 'Demo Strategy'}])

@app.route('/api/market-data/overview')
def get_market_data_overview():
    return jsonify({'status': 'ok', 'data': []})

if __name__ == '__main__':
    print("Starting simple backend service on port 54321...")
    app.run(host='0.0.0.0', port=54321, debug=True)
