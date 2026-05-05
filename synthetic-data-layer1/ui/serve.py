"""Tiny launcher: `python -m ui.serve` or `python ui/serve.py` boots the UI.

Defaults to 127.0.0.1:8000. Override via TRACEX_UI_HOST / TRACEX_UI_PORT env vars.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

LAYER1_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAYER1_ROOT))


def main() -> None:
    host = os.environ.get("TRACEX_UI_HOST", "127.0.0.1")
    # Default to 8765 to avoid common collisions (8000 is the Themis Agent API on
    # this machine). Override with TRACEX_UI_PORT.
    port = int(os.environ.get("TRACEX_UI_PORT", "8765"))
    # Plain ASCII banner — Windows console default codec (cp1252) can't encode
    # arrow / em-dash characters, and uvicorn will refuse to start if the banner
    # print raises before its own startup.
    print(f"\n  TraceX UI  ->  http://{host}:{port}\n")
    uvicorn.run("ui.api:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
