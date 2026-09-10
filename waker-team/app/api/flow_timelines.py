"""Flow 运行时间线 REST 路由."""

from fastapi import APIRouter, HTTPException, Request

from app.services.flow_timeline_service import FlowTimelineService

router = APIRouter(prefix="/flow-runs", tags=["flow-timelines"])

_timeline_service = FlowTimelineService()


@router.get("/{run_id}/timeline")
async def get_flow_timeline(run_id: str, request: Request):
    """获取 Flow 运行时间线.

    返回 FLOW_RUN 摘要 + 所有 NODE_RUN 按时间排序的聚合视图，
    每个节点包含 input/output/error/retry_count 以及关联 Task 详情。
    """
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        timeline = await _timeline_service.get_timeline(run_id, session)
        if timeline is None:
            raise HTTPException(status_code=404, detail="Flow run not found")
        return timeline
