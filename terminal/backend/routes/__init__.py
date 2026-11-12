"""HTTP routers of the OPEN TERMINAL gateway — one module per slice owner.

app.py includes every router listed in ROUTERS (plain imports: a broken router must
fail loudly at startup, never be skipped).
"""
ROUTERS = ("markets", "flow", "options", "geo", "sat", "macro")
