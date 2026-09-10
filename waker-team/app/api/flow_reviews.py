"""人工确认 REST 路由 — approve / reject."""

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import ReviewAction
from app.engine.review_service import ReviewServiceError

router = APIRouter(prefix="/flow-runs", tags=["flow-reviews"])


@router.post("/{run_id}/review")
async def submit_review(run_id: str, body: ReviewAction, request: Request):
    """提交人工确认操作.

    Parameters
    ----------
    run_id:
        Flow 运行 ID。
    body:
        确认操作：action=approve|reject, node_key, comment。
    """
    review_service = getattr(request.app.state, "review_service", None)
    if review_service is None:
        raise HTTPException(status_code=503, detail="Review service not initialized")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            if body.action == "approve":
                node_run = await review_service.approve(
                    flow_run_id=run_id,
                    node_key=body.node_key,
                    db=session,
                    comment=body.comment,
                )
            elif body.action == "reject":
                if not body.comment:
                    raise HTTPException(
                        status_code=422,
                        detail="comment is required for rejection",
                    )
                node_run = await review_service.reject(
                    flow_run_id=run_id,
                    node_key=body.node_key,
                    db=session,
                    comment=body.comment,
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid action: {body.action}",
                )

            return {
                "id": node_run.id,
                "node_key": node_run.node_key,
                "status": node_run.status,
                "outcome": node_run.outcome_json,
            }
        except ReviewServiceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
