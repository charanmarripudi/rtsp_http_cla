#!/usr/bin/env python3
"""
Root entrypoint wrapper for backwards-compatibility.
Seamlessly forwards to backend.server for execution.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.server import app, main

if __name__ == "__main__":
    main()
