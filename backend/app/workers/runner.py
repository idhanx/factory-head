"""Worker entry point: `python -m app.workers.runner`."""

from __future__ import annotations

import asyncio
import signal

import structlog

from app.core.config import settings
from app.core.db import dispose_engine
from app.workers import jobs
from app.workers.scheduler import Scheduler
from app.ws.manager import manager

log = structlog.get_logger()

scheduler = Scheduler(str(settings.redis_url))

# The ladder's rungs are 5/15/30 minutes, so a 30-second tick is ample
# precision and cheap. Everything else runs on the cadence its data changes.
scheduler.every(30, name="escalations", lock_seconds=60)(jobs.fire_due_escalations)
# The outbox: WhatsApp, SMS and push, retried with backoff.
scheduler.every(15, name="dispatch", lock_seconds=120)(jobs.dispatch_notifications)
scheduler.every(600, name="daily-owner-report", lock_seconds=300)(jobs.send_daily_owner_reports)
scheduler.every(3600, name="recovered-profit", lock_seconds=600)(
    jobs.accrue_recovered_profit
)
scheduler.every(300, name="coach-briefings", lock_seconds=600)(
    jobs.generate_coach_briefings
)
scheduler.every(1800, name="stale-downtime", lock_seconds=300)(jobs.sweep_stale_open_events)
# Audit readiness and training needs are re-scored here, not on page reads.
scheduler.every(3600, name="plugin-refresh", lock_seconds=900)(jobs.refresh_plugins)
# Kaizen follows its Core Actions: status for the operator, and the points
# that only land once Core says the saving held.
scheduler.every(3600, name="kaizen-sync", lock_seconds=600)(jobs.sync_kaizen)
# A pattern is a management decision, not an alert — weekly is the cadence.
scheduler.every(7 * 24 * 3600, name="kaizen-patterns", lock_seconds=1800)(
    jobs.detect_kaizen_patterns
)


async def main() -> None:
    await manager.startup()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, scheduler.stop)

    try:
        await scheduler.run()
    finally:
        await scheduler.aclose()
        await manager.shutdown()
        await dispose_engine()
        log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
