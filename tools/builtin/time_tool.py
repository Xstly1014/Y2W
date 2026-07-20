"""Time tool — returns current time in the project timezone."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool


# Asia/Shanghai — matches the docstring and the project's primary user
# timezone. Using zoneinfo (not naive local time) means the returned
# %Z abbreviation is "CST" consistently regardless of the host's TZ env.
_TZ = ZoneInfo("Asia/Shanghai")


@tool
def current_time_tool() -> str:
    """Return the current date and time (Asia/Shanghai timezone)."""
    return datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
