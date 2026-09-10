"""Group 管理 REST 路由."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.schemas import (
    GroupCreate,
    GroupMemberAdd,
    GroupMemberResponse,
    GroupResponse,
    GroupSkillResponse,
    GroupUpdate,
    TransferLeaderRequest,
)
from app.models.task import Task
from app.services.group_service import GroupService
from app.time_utils import to_iso_utc

router = APIRouter(prefix="/groups", tags=["groups"])


def _clip(text: str | None, limit: int = 60) -> str:
    """单行截断（运行状态条标题用）."""
    s = (text or "").strip().replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def _get_service(request: Request) -> tuple:
    """从 request 构建 GroupService 实例."""
    session_factory = request.app.state.db_session_factory
    return session_factory


def _group_to_response(group) -> dict:
    """将 Group ORM 对象转为响应 dict."""
    return {
        "id": group.id,
        "name": group.name,
        "leader_waker_id": group.leader_waker_id,
        "project_id": group.project_id,
        "description": group.description,
        "sop_id": group.sop_id,
        "created_at": to_iso_utc(group.created_at),
        "updated_at": to_iso_utc(group.updated_at),
    }


def _member_to_response(member) -> dict:
    """将 GroupMember ORM 对象转为响应 dict."""
    return {
        "group_id": member.group_id,
        "waker_id": member.waker_id,
        "role": member.role,
        "joined_at": to_iso_utc(member.joined_at),
    }


def _skill_to_response(skill) -> dict:
    """将 GroupSkill ORM 对象转为响应 dict."""
    return {
        "id": skill.id,
        "group_id": skill.group_id,
        "skill_name": skill.skill_name,
        "created_at": to_iso_utc(skill.created_at),
    }


@router.get("", response_model=list[GroupResponse])
async def list_groups(request: Request):
    """列表所有群组."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        groups = await service.list_groups()
        return [_group_to_response(g) for g in groups]


@router.post("", response_model=GroupResponse, status_code=201)
async def create_group(data: GroupCreate, request: Request):
    """创建群组."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        try:
            group = await service.create_group(
                name=data.name,
                leader_waker_id=data.leader_waker_id,
                project_id=data.project_id,
                description=data.description,
                sop_id=data.sop_id,
            )
            return _group_to_response(group)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc) or "unique" in str(exc).lower():
                raise HTTPException(status_code=409, detail=f"Group name '{data.name}' already exists") from exc
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(group_id: str, request: Request):
    """获取群组详情."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        group = await service.get_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        return _group_to_response(group)


@router.put("/{group_id}", response_model=GroupResponse)
async def update_group(group_id: str, data: GroupUpdate, request: Request):
    """更新群组."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        try:
            group = await service.update_group(
                group_id,
                name=data.name,
                leader_waker_id=data.leader_waker_id,
                project_id=data.project_id,
                description=data.description,
                sop_id=data.sop_id,
            )
            if group is None:
                raise HTTPException(status_code=404, detail="Group not found")
            return _group_to_response(group)
        except HTTPException:
            raise
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc) or "unique" in str(exc).lower():
                raise HTTPException(status_code=409, detail=f"Group name '{data.name}' already exists") from exc
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: str, request: Request):
    """删除群组."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        deleted = await service.delete_group(group_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Group not found")
        return None


@router.post("/{group_id}/members", response_model=GroupMemberResponse, status_code=201)
async def add_member(group_id: str, data: GroupMemberAdd, request: Request):
    """添加成员到群组."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        member = await service.add_member(group_id, data.waker_id, data.role)
        if member is None:
            raise HTTPException(status_code=404, detail="Group not found")
        return _member_to_response(member)


@router.delete("/{group_id}/members/{waker_id}", status_code=204)
async def remove_member(group_id: str, waker_id: str, request: Request):
    """从群组移除成员."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        removed = await service.remove_member(group_id, waker_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Member not found in group")
        return None


@router.get("/{group_id}/members", response_model=list[GroupMemberResponse])
async def list_members(group_id: str, request: Request):
    """列出群组所有成员."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        # 先检查群组是否存在
        group = await service.get_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        members = await service.list_members(group_id)
        return [_member_to_response(m) for m in members]


# ------------------------------------------------------------------
# Group skills
# ------------------------------------------------------------------


@router.get("/{group_id}/skills", response_model=list[GroupSkillResponse])
async def list_skills(group_id: str, request: Request):
    """列出群组所有技能."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        group = await service.get_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        skills = await service.list_skills(group_id)
        return [_skill_to_response(s) for s in skills]


@router.post("/{group_id}/skills", response_model=GroupSkillResponse, status_code=201)
async def add_skill(group_id: str, body: dict, request: Request):
    """添加技能到群组. body: {"skill_name": "xxx"}."""
    skill_name = body.get("skill_name")
    if not skill_name:
        raise HTTPException(status_code=422, detail="Missing 'skill_name' field in body")
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        skill = await service.add_skill(group_id, skill_name)
        if skill is None:
            raise HTTPException(status_code=404, detail="Group not found")
        return _skill_to_response(skill)


@router.delete("/{group_id}/skills/{skill_name}", status_code=204)
async def remove_skill(group_id: str, skill_name: str, request: Request):
    """从群组移除技能."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        removed = await service.remove_skill(group_id, skill_name)
        if not removed:
            raise HTTPException(status_code=404, detail="Skill not found in group")
        return None


# ------------------------------------------------------------------
# Transfer leader
# ------------------------------------------------------------------


@router.post("/{group_id}/transfer-leader", response_model=GroupResponse)
async def transfer_leader(group_id: str, data: TransferLeaderRequest, request: Request):
    """转移群主."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = GroupService(session)
        try:
            group = await service.transfer_leader(group_id, data.target_waker_id)
            if group is None:
                raise HTTPException(status_code=404, detail="Group not found")
            return _group_to_response(group)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


# ------------------------------------------------------------------
# Activity（群内运行状态聚合）
# ------------------------------------------------------------------


@router.get("/{group_id}/activity")
async def get_group_activity(group_id: str, request: Request):
    """群内活动聚合：正在运行/排队的成员任务 + Leader 回复 run（运行状态条数据源）.

    - 成员任务：Task 表中 pending/running 的派活任务（manual/delegate/
      async_delegate/flow_node/schedule——任何让成员真实运行的任务）；
    - Leader run：chat_reply 内存态快照（进程重启后降级为仅成员任务）。
    """
    session_factory = request.app.state.db_session_factory
    now = datetime.now(UTC)
    items: list[dict] = []

    # 1. 成员任务
    async with session_factory() as session:
        service = GroupService(session)
        group = await service.get_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        task_rows = (
            await session.execute(
                select(Task)
                .where(
                    Task.group_id == group_id,
                    Task.status.in_(["pending", "running"]),
                    Task.kind.in_(
                        ["manual", "delegate", "async_delegate", "flow_node", "schedule"]
                    ),
                )
                .order_by(Task.created_at.asc())
            )
        ).scalars().all()

    for task in task_rows:
        created = task.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        elapsed = int((now - created).total_seconds()) if created else 0
        items.append(
            {
                "waker": task.executor,
                "kind": "member_task",
                "status": "running" if task.status == "running" else "queued",
                "task_id": task.id,
                "title": _clip(task.input_text),
                "elapsed": elapsed,
                "started_at": to_iso_utc(task.created_at),
            }
        )

    # 2. Leader 进行中的回复 run（内存态快照）
    chat_reply = getattr(request.app.state, "chat_reply", None)
    if chat_reply is not None:
        try:
            active_runs = await chat_reply.list_active_runs(group_id)
        except Exception:
            active_runs = []
        for run in active_runs:
            items.append(
                {
                    "waker": run["target"],
                    "kind": "leader_run",
                    "status": "running",
                    "conversation_id": run["conversation_id"],
                    "title": run.get("conversation_title") or "正在回复群消息",
                    "elapsed": run["elapsed"],
                }
            )

    return {"active": len(items) > 0, "items": items}
