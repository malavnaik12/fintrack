"""
core/config.py

Central config for FinTrack.
All paths and external service settings live here — nothing hardcoded elsewhere.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # Load .env file

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent  # project root
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"  # drop PDFs here
DB_PATH = str(DATA_DIR / "FinTrack.db")

# ---------------------------------------------------------------------------
# Ollama / LLM
# ---------------------------------------------------------------------------

# When developing: point this at your Mac M1's local IP over WiFi
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "LLM Address Unavailable")
OLLAMA_MODEL = "llama3.1:8b"

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

API_HOST = "0.0.0.0"
API_PORT = 8000
