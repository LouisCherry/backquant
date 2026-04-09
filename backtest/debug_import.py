#!/usr/bin/env python3
import sys
import traceback

print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")

print("\n=== Testing imports ===")
try:
    import flask
    print(f"Flask version: {flask.__version__}")
    
    from app import create_app
    from app.config import CONFIG_ENV
    print("Successfully imported app modules")
    
    app = create_app(CONFIG_ENV)
    print("App created successfully")
    
    print("\n=== All tests passed ===")
    print("Backend service should be able to start")
    
except Exception as e:
    print(f"\n=== ERROR: {type(e).__name__} ===")
    print(f"Message: {e}")
    print("\nTraceback:")
    traceback.print_exc()
    sys.exit(1)
