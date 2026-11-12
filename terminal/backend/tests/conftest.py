import os
import sys

# make `import stream_hub`, `import app`, `from providers import ...` resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
