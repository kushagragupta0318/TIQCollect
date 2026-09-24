from fastapi import APIRouter
from app.api.v1.endpoints import auth, health, agent, manager, verify, events

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(health.router)
api_router.include_router(verify.router)
api_router.include_router(agent.router)
api_router.include_router(manager.router)
api_router.include_router(events.router)
