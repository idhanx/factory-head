"""v1 API surface."""

from fastapi import APIRouter

from . import (
    actions,
    admin,
    ai,
    attachments,
    auth,
    capture,
    corrections,
    delivery,
    escalations,
    ingest,
    maintenance,
    masters,
    onboarding,
    plugins,
    reports,
    shifts,
    stores,
    sync,
    trace,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(masters.router)
api_router.include_router(shifts.router)
api_router.include_router(capture.router)
api_router.include_router(escalations.router)
# §1's two 'when events occur' roles. Each desk reads Core data the plant is
# already capturing and writes only the one thing §1 names for that role.
api_router.include_router(maintenance.router)
api_router.include_router(stores.router)
api_router.include_router(delivery.router)
api_router.include_router(sync.router)
api_router.include_router(corrections.router)
api_router.include_router(attachments.router)
api_router.include_router(actions.router)
api_router.include_router(reports.router)
api_router.include_router(ai.router)
api_router.include_router(onboarding.router)
api_router.include_router(plugins.router)
api_router.include_router(trace.router)
api_router.include_router(ingest.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
