"""Aggregates every module's router under the versioned API prefix.

One line per room, added as each module lands.
"""

from fastapi import APIRouter

from app.modules.booking.router import router as booking_router
from app.modules.doctors.router import router as doctors_router
from app.modules.identity.router import router as identity_router
from app.modules.presence.router import router as presence_router
from app.modules.scheduling.router import router as scheduling_router

api_router = APIRouter()

api_router.include_router(identity_router)
api_router.include_router(doctors_router)  # Room 2
api_router.include_router(presence_router)  # Room 1
api_router.include_router(booking_router)  # Room 3
api_router.include_router(scheduling_router)  # Room 4
