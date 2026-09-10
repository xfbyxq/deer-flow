"""Group 管理业务逻辑：CRUD + 成员管理 + 技能管理 + 转移群主."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.flow import FlowDef
from app.models.group import Group, GroupMember
from app.models.group_skill import GroupSkill
from app.models.task import Task


class GroupService:
    """Group（群组）管理服务."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    # ------------------------------------------------------------------
    # Group CRUD
    # ------------------------------------------------------------------

    async def create_group(
        self,
        name: str,
        leader_waker_id: str | None = None,
        project_id: str | None = None,
        description: str | None = None,
        sop_id: str | None = None,
    ) -> Group:
        """创建群组."""
        group = Group(
            name=name,
            leader_waker_id=leader_waker_id,
            project_id=project_id,
            description=description,
            sop_id=sop_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(group)
        await self.db.commit()
        await self.db.refresh(group)
        return group

    async def list_groups(self) -> list[Group]:
        """列表所有群组."""
        result = await self.db.execute(select(Group).order_by(Group.created_at))
        return list(result.scalars().all())

    async def get_group(self, group_id: str) -> Group | None:
        """获取单个群组."""
        result = await self.db.execute(select(Group).where(Group.id == group_id))
        return result.scalars().first()

    async def update_group(
        self,
        group_id: str,
        name: str | None = None,
        leader_waker_id: str | None = None,
        project_id: str | None = None,
        description: str | None = None,
        sop_id: str | None = None,
    ) -> Group | None:
        """更新群组."""
        group = await self.get_group(group_id)
        if group is None:
            return None
        if name is not None:
            group.name = name
        if leader_waker_id is not None:
            group.leader_waker_id = leader_waker_id
        if project_id is not None:
            group.project_id = project_id
        if description is not None:
            group.description = description
        if sop_id is not None:
            group.sop_id = sop_id
        group.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(group)
        return group

    async def delete_group(self, group_id: str) -> bool:
        """删除群组（同时删除成员关联和技能关联）."""
        group = await self.get_group(group_id)
        if group is None:
            return False
        # 先清空 leader_waker_id 避免 FK 约束冲突
        group.leader_waker_id = None
        # 清空关联任务的 group_id
        tasks_result = await self.db.execute(
            select(Task).where(Task.group_id == group_id)
        )
        for task in tasks_result.scalars().all():
            task.group_id = None
        # 解除会话与 Flow 定义的群关联（外键指向 groups.id，保留数据仅解除引用）
        await self.db.execute(
            update(Conversation).where(Conversation.group_id == group_id).values(group_id=None)
        )
        await self.db.execute(
            update(FlowDef).where(FlowDef.group_id == group_id).values(group_id=None)
        )
        # 删成员关联
        members_result = await self.db.execute(
            select(GroupMember).where(GroupMember.group_id == group_id)
        )
        for member in members_result.scalars().all():
            await self.db.delete(member)
        # 删技能关联
        skills_result = await self.db.execute(
            select(GroupSkill).where(GroupSkill.group_id == group_id)
        )
        for skill in skills_result.scalars().all():
            await self.db.delete(skill)
        await self.db.delete(group)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------
    # Member management
    # ------------------------------------------------------------------

    async def add_member(
        self,
        group_id: str,
        waker_id: str,
        role: str = "member",
    ) -> GroupMember | None:
        """添加成员到群组。群组不存在返回 None."""
        group = await self.get_group(group_id)
        if group is None:
            return None
        # 检查是否已是成员
        existing = await self._get_member(group_id, waker_id)
        if existing is not None:
            return existing
        member = GroupMember(
            group_id=group_id,
            waker_id=waker_id,
            role=role,
            joined_at=datetime.now(UTC),
        )
        self.db.add(member)
        await self.db.commit()
        await self.db.refresh(member)
        return member

    async def remove_member(self, group_id: str, waker_id: str) -> bool:
        """从群组移除成员."""
        member = await self._get_member(group_id, waker_id)
        if member is None:
            return False
        await self.db.delete(member)
        await self.db.commit()
        return True

    async def list_members(self, group_id: str) -> list[GroupMember]:
        """列出群组所有成员."""
        result = await self.db.execute(
            select(GroupMember).where(GroupMember.group_id == group_id)
        )
        return list(result.scalars().all())

    async def get_waker_groups(self, waker_id: str) -> list[Group]:
        """查询某 waker 所属的所有组."""
        result = await self.db.execute(
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(GroupMember.waker_id == waker_id)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Skill management
    # ------------------------------------------------------------------

    async def list_skills(self, group_id: str) -> list[GroupSkill]:
        """列出群组所有技能."""
        result = await self.db.execute(
            select(GroupSkill).where(GroupSkill.group_id == group_id)
        )
        return list(result.scalars().all())

    async def add_skill(self, group_id: str, skill_name: str) -> GroupSkill | None:
        """添加技能到群组。群组不存在返回 None."""
        group = await self.get_group(group_id)
        if group is None:
            return None
        # 检查是否已存在
        existing = await self._get_skill(group_id, skill_name)
        if existing is not None:
            return existing
        skill = GroupSkill(
            group_id=group_id,
            skill_name=skill_name,
            created_at=datetime.now(UTC),
        )
        self.db.add(skill)
        await self.db.commit()
        await self.db.refresh(skill)
        return skill

    async def remove_skill(self, group_id: str, skill_name: str) -> bool:
        """从群组移除技能."""
        skill = await self._get_skill(group_id, skill_name)
        if skill is None:
            return False
        await self.db.delete(skill)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------
    # Transfer leader
    # ------------------------------------------------------------------

    async def transfer_leader(self, group_id: str, target_waker_id: str) -> Group | None:
        """转移群主给目标成员。群组不存在返回 None，目标非成员返回 ValueError."""
        group = await self.get_group(group_id)
        if group is None:
            return None
        # 验证目标是群成员
        member = await self._get_member(group_id, target_waker_id)
        if member is None:
            raise ValueError(f"Waker '{target_waker_id}' is not a member of group '{group_id}'")
        group.leader_waker_id = target_waker_id
        group.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(group)
        return group

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_member(self, group_id: str, waker_id: str) -> GroupMember | None:
        """查询单个成员关联."""
        result = await self.db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.waker_id == waker_id,
            )
        )
        return result.scalars().first()

    async def _get_skill(self, group_id: str, skill_name: str) -> GroupSkill | None:
        """查询单个技能关联."""
        result = await self.db.execute(
            select(GroupSkill).where(
                GroupSkill.group_id == group_id,
                GroupSkill.skill_name == skill_name,
            )
        )
        return result.scalars().first()
