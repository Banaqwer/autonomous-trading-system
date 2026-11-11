"""Minimal code-execution seam.

In production this is a per-project remote machine (a container/microVM). Here
it is a guarded local subprocess with a wall-clock timeout, standing in for the
'Cloud Python sandbox'. Swap `run_python` for a real sandbox driver (e.g. a
gVisor container or a Firecracker microVM over a WebSocket terminal) without
touching the agent or tool layer.

Runs with `-I` (isolated) so ambient env/user-site can't leak in; the engine
root is injected onto sys.path explicitly so `import openquant` still works.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

# engine root = parent of the `openquant` package dir
_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_python(code: str, timeout: int = 30, workdir: str | None = None,
               expose_engine: bool = True) -> dict:
    workdir = workdir or tempfile.mkdtemp(prefix="oq_sandbox_")
    bootstrap = (f"import sys; sys.path.insert(0, {_ENGINE_ROOT!r})\n" if expose_engine else "")
    path = os.path.join(workdir, "_run.py")
    with open(path, "w") as f:
        f.write(bootstrap + code)
    try:
        proc = subprocess.run(
            [sys.executable, "-I", path],
            capture_output=True, text=True, timeout=timeout, cwd=workdir,
        )
        return {"stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:],
                "exit_code": proc.returncode, "workdir": workdir}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"timeout after {timeout}s", "exit_code": -1, "workdir": workdir}
