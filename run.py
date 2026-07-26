#!/usr/bin/env python3
"""
Chevalier Events - run.py
Start the app: python run.py
"""
import sys, os, threading, webbrowser, time
sys.path.insert(0, os.path.dirname(__file__))

from backend import create_app

app = create_app()

def _open_browser():
    """Wait for Flask to start, then open the browser."""
    time.sleep(1.5)
    webbrowser.open("http://localhost:5000/login")

if __name__ == "__main__":
    print("\nChevalier Events starting...")
    print("   Admin:  http://localhost:5000/login")
    print("   Portal: http://localhost:5000/portal")
    print("   Press Ctrl+C to stop.\n")

    # Open browser in background thread so Flask starts first
    t = threading.Thread(target=_open_browser, daemon=True)
    t.start()

    app.run(debug=False, host="127.0.0.1", port=5000)
