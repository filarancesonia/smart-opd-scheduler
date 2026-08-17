"""Aggregates every module's router under the versioned API prefix.

One line per room, added as each module lands.
"""

from fastapi import APIRouter

from app.modules.identity.router import router as identity_router

api_router = APIRouter()

api_router.include_router(identity_router)
