from fastapi import APIRouter

from app.api.board import router as board_router
from app.api.conversations import router as conversations_router
from app.api.flow_reviews import router as flow_reviews_router
from app.api.flow_runs import router as flow_runs_router
from app.api.flow_timelines import router as flow_timelines_router
from app.api.flows import router as flows_router
from app.api.groups import router as groups_router
from app.api.health import router as health_router
from app.api.schedules import router as schedules_router
from app.api.settings import router as settings_router
from app.api.tasks import router as tasks_router
from app.api.wakers import router as wakers_router


def create_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    router.include_router(health_router)
    router.include_router(wakers_router)
    router.include_router(tasks_router)
    router.include_router(board_router)
    router.include_router(groups_router)
    router.include_router(flows_router)
    router.include_router(flow_runs_router)
    router.include_router(flow_timelines_router)
    router.include_router(flow_reviews_router)
    router.include_router(schedules_router)
    router.include_router(conversations_router)
    router.include_router(settings_router)
    return router
