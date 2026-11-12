import os
import sys

# make `import stream_hub`, `import app`, `from providers import ...` resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests are network-free: no background quote poller and no upstream websocket feed
# unless a test asks for one explicitly (see REBUILD_SPEC §3.1 env switches).
os.environ.setdefault("OT_QUOTES", "0")
os.environ.setdefault("OT_FEEDS", "none")
