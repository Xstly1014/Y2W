"""Configuration package.

Loads `.env` into `os.environ` first so that non-Settings env vars (e.g.
`HF_ENDPOINT` for the HuggingFace mirror, `LANGSMITH_*` for tracing) are
available to third-party libraries. Then exposes the typed `settings`.
"""
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

# Compute project root without importing settings.py (avoid circular import
# and ensure .env is in os.environ *before* Settings() reads it).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env into os.environ (does not override already-set vars).
_load_dotenv(_PROJECT_ROOT / ".env", override=False)

from config.settings import settings  # noqa: E402

__all__ = ["settings"]
