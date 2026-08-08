"""Minimal timezone-aware scheduler; suitable for a single Docker worker."""

from __future__ import annotations

import os
import time
from datetime import datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from .pipeline import run_pipeline


def seconds_until_next_run(zone: str, hour: int = 15, minute: int = 0) -> float:
    tz = ZoneInfo(zone)
    now = datetime.now(tz)
    target = datetime.combine(now.date(), clock_time(hour, minute), tzinfo=tz)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def main() -> None:
    zone = os.getenv("TZ", "Pacific/Auckland")
    hour = int(os.getenv("RUN_HOUR", "15"))
    minute = int(os.getenv("RUN_MINUTE", "0"))
    while True:
        time.sleep(seconds_until_next_run(zone, hour, minute))
        try:
            run_pipeline()
        except Exception:
            # Keep the scheduler alive; the failed run is persisted in SQLite.
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()

