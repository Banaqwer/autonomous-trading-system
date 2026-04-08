"""
START ORCHESTRATOR AND TRADING BOT
===========================================================

This script starts the complete AI Singularity Orchestrator
with the Hurst trading bot underneath.

Monday April 8, 2026 - Launch sequence
"""

import os
import sys
import time
import subprocess
import webbrowser
from pathlib import Path
from datetime import datetime

def main():
    print("\n" + "="*100)
    print("STARTING AI SINGULARITY TRADING SYSTEM")
    print("="*100 + "\n")

    # Check if orchestrator exists
    if not Path("ai_singularity_orchestrator.py").exists():
        print("[FAIL] ai_singularity_orchestrator.py not found")
        sys.exit(1)

    # Check if bot exists
    if not Path("trading_bot_alpaca_integration.py").exists():
        print("[FAIL] trading_bot_alpaca_integration.py not found")
        sys.exit(1)

    # Check if dashboard exists
    if not Path("ai_orchestrator_dashboard.html").exists():
        print("[FAIL] ai_orchestrator_dashboard.html not found")
        sys.exit(1)

    print("[OK] All components found")
    print()

    # Start orchestrator in background
    print("[STARTING] AI Singularity Orchestrator...")
    orchestrator_process = subprocess.Popen(
        [sys.executable, "ai_singularity_orchestrator.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    print(f"[OK] Orchestrator started (PID: {orchestrator_process.pid})")
    time.sleep(2)

    # Start trading bot
    print("[STARTING] Hurst Trading Bot...")
    bot_process = subprocess.Popen(
        [sys.executable, "trading_bot_alpaca_integration.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    print(f"[OK] Trading bot started (PID: {bot_process.pid})")
    time.sleep(2)

    # Open dashboard
    print("[OPENING] Dashboard in browser...")
    dashboard_path = Path("ai_orchestrator_dashboard.html").absolute()
    webbrowser.open(f"file:///{dashboard_path}")
    print(f"[OK] Dashboard opened")
    print()

    print("="*100)
    print("SYSTEM OPERATIONAL")
    print("="*100)
    print()
    print("  Orchestrator Status: RUNNING")
    print("  Trading Bot Status: RUNNING")
    print("  Dashboard: Open in browser")
    print()
    print("  Monitor metrics at: ai_orchestrator_dashboard.html")
    print("  Logs available at: ai_logs/")
    print()
    print("  Press Ctrl+C to shutdown")
    print()

    try:
        # Keep running
        while True:
            time.sleep(1)

            # Check if processes are still alive
            if orchestrator_process.poll() is not None:
                print("[WARN] Orchestrator process died. Restarting...")
                orchestrator_process = subprocess.Popen(
                    [sys.executable, "ai_singularity_orchestrator.py"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

            if bot_process.poll() is not None:
                print("[WARN] Bot process died. Restarting...")
                bot_process = subprocess.Popen(
                    [sys.executable, "trading_bot_alpaca_integration.py"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

    except KeyboardInterrupt:
        print("\n\n[SHUTTING DOWN]")
        print("Terminating orchestrator...")
        orchestrator_process.terminate()
        print("Terminating bot...")
        bot_process.terminate()
        time.sleep(2)
        orchestrator_process.kill()
        bot_process.kill()
        print("[OK] System shutdown complete")


if __name__ == '__main__':
    main()
