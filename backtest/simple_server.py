#!/usr/bin/env python3

import http.server
import socketserver
import os

PORT = 54321

handler = http.server.SimpleHTTPRequestHandler

try:
    with socketserver.TCPServer(("0.0.0.0", PORT), handler) as httpd:
        print(f"Server running at http://0.0.0.0:{PORT}")
        httpd.serve_forever()
except Exception as e:
    print(f"Error starting server: {e}")
