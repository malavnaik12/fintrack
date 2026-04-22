"""
core/config.py

Central config for fin_intel.
All paths and external service settings live here — nothing hardcoded elsewhere.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent  # project root
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"  # drop PDFs here
DB_PATH = str(DATA_DIR / "fin_intel.db")

# ---------------------------------------------------------------------------
# Ollama / LLM
# ---------------------------------------------------------------------------

# When developing: point this at your Mac M1's local IP over WiFi
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = "llama3.1:8b"

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

API_HOST = "0.0.0.0"
API_PORT = 8000
