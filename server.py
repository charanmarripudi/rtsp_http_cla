#!/usr/bin/env python3
"""
Root entrypoint wrapper for backwards-compatibility.
Seamlessly forwards to backend.server for execution.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

# Ensure PROJECT_ROOT is strictly first in sys.path so 'backend' is recognized as a package
if PROJECT_ROOT in sys.path:
    sys.path.remove(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

try:
    from backend.server import app, main
except (ImportError, ModuleNotFoundError):
    import server as backend_server
    app = backend_server.app
    main = backend_server.main

if __name__ == "__main__":
    main()

