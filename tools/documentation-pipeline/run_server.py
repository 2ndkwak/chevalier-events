"""
run_server.py -- boot the real Flask app locally on a throwaway port, for
documentation screenshot generation (see README.md in this folder).

Run this in the background so it survives the sandbox's per-tool-call
process boundary:

    setsid nohup python3 run_server.py > server.log 2>&1 < /dev/null &

Then confirm it's actually still alive on the NEXT tool call (a plain `&`
without setsid/nohup gets killed when that tool call ends) before running
capture.py against it:

    curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5050/login

Kill it when done: pkill -f run_server.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from backend import create_app
app = create_app()
app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)
