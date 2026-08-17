"""Aggregates every module's router under the versioned API prefix.

One line per room, added as each module lands.
"""

from fastapi import APIRouter

from app.modules.booking.router import router as booking_router
from app.modules.doctors.router import router as doctors_router
from app.modules.identity.router import router as identity_router
from app.modules.analytics.router import router as analytics_router
from app.modules.emergency.router import router as emergency_router
from app.modules.integration.router import router as integration_router
from app.modules.notifications.router import router as notifications_router
from app.modules.presence.router import router as presence_router
from app.modules.queue.router import router as queue_router
from app.modules.scheduling.router import router as scheduling_router

api_router = APIRouter()

api_router.include_router(identity_router)
api_router.include_router(doctors_router)  # Room 2
api_router.include_router(presence_router)  # Room 1
api_router.include_router(booking_router)  # Room 3
api_router.include_router(scheduling_router)  # Room 4
api_router.include_router(queue_router)  # Room 5
api_router.include_router(notifications_router)  # Room 6
api_router.include_router(emergency_router)  # Room 7
api_router.include_router(analytics_router)  # Room 8
api_router.include_router(integration_router)  # Room 9
