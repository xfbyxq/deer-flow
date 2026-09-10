"""Flow 定义 CRUD + JSON schema 校验."""

import json
from collections import defaultdict, deque
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FlowDefinitionV1, _VALID_NODE_TYPES
from app.models.flow import FlowDef, FlowRun, NodeRun
from app.time_utils import to_iso_utc


class FlowValidationError(ValueError):
    """Flow 定义 JSON schema 校验失败."""


class FlowDefService:
    """Flow 定义管理服务."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, data: dict) -> FlowDef:
        """创建 Flow 定义."""
        def_json = data["definition_json"]
        # 校验 schema
        self.validate_definition(def_json if isinstance(def_json, dict) else json.loads(def_json))

        flow = FlowDef(
            name=data["name"],
            group_id=data.get("group_id"),
            description=data.get("description"),
            definition_json=json.dumps(def_json) if isinstance(def_json, dict) else def_json,
            version=def_json.get("version", 1) if isinstance(def_json, dict) else 1,
            status="draft",
            created_by=data.get("created_by"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(flow)
        await self.db.commit()
        await self.db.refresh(flow)
        return flow

    async def list(self, group_id: str | None = None) -> "list[FlowDef]":
        """列表 Flow 定义（可选按 group_id 筛选）."""
        stmt = select(FlowDef).order_by(FlowDef.created_at)
        if group_id is not None:
            stmt = stmt.where(FlowDef.group_id == group_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, flow_id: str) -> FlowDef | None:
        """获取单个 Flow 定义."""
        result = await self.db.execute(select(FlowDef).where(FlowDef.id == flow_id))
        return result.scalars().first()

    async def update(self, flow_id: str, data: dict) -> FlowDef | None:
        """更新 Flow 定义."""
        flow = await self.get(flow_id)
        if flow is None:
            return None

        if "name" in data and data["name"] is not None:
            flow.name = data["name"]
        if "description" in data and data["description"] is not None:
            flow.description = data["description"]
        if "definition_json" in data and data["definition_json"] is not None:
            def_json = data["definition_json"]
            self.validate_definition(def_json if isinstance(def_json, dict) else json.loads(def_json))
            flow.definition_json = json.dumps(def_json) if isinstance(def_json, dict) else def_json
            if isinstance(def_json, dict):
                flow.version = def_json.get("version", 1)

        flow.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(flow)
        return flow

    async def delete(self, flow_id: str) -> None:
        """删除 Flow 定义（连同其运行记录，避免外键约束阻止删除）."""
        flow = await self.get(flow_id)
        if flow is None:
            return
        # 先清理 node_runs / flow_runs（外键指向 flow_defs）
        run_ids = (
            (await self.db.execute(select(FlowRun.id).where(FlowRun.flow_def_id == flow_id)))
            .scalars()
            .all()
        )
        if run_ids:
            await self.db.execute(delete(NodeRun).where(NodeRun.flow_run_id.in_(run_ids)))
            await self.db.execute(delete(FlowRun).where(FlowRun.flow_def_id == flow_id))
        await self.db.delete(flow)
        await self.db.commit()

    async def list_versions(self, flow_id: str) -> "list[dict]":
        """列出 Flow 定义的版本历史（P2b 用，当前只返回当前版本）."""
        flow = await self.get(flow_id)
        if flow is None:
            return []
        return [
            {
                "version": flow.version,
                "updated_at": to_iso_utc(flow.updated_at),
            }
        ]

    # ------------------------------------------------------------------
    # JSON schema 校验
    # ------------------------------------------------------------------

    @staticmethod
    def validate_definition(definition: dict) -> None:
        """校验 Flow 定义 JSON schema.

        规则：
        - version 必须为 1
        - nodes 必须是非空数组
        - 每个 node 必须有 key（唯一）、type（合法值）
        - depends_on 引用的 key 必须存在
        - 不能有环形依赖
        - waker_task/leader_plan 必须有 waker 字段
        - condition 必须有 expression 和 branches
        - notify 必须有 channel
        """
        # version 校验
        version = definition.get("version", 1)
        if version != 1:
            raise FlowValidationError(f"Unsupported flow definition version: {version}")

        # nodes 校验
        nodes = definition.get("nodes")
        if not nodes or not isinstance(nodes, list):
            raise FlowValidationError("'nodes' must be a non-empty array")

        # 收集所有 key
        keys: set[str] = set()
        for i, node in enumerate(nodes):
            key = node.get("key")
            if not key:
                raise FlowValidationError(f"Node at index {i} missing required 'key'")
            if key in keys:
                raise FlowValidationError(f"Duplicate node key: '{key}'")
            keys.add(key)

        # 逐节点校验
        for i, node in enumerate(nodes):
            key = node["key"]
            node_type = node.get("type")

            # type 合法性
            if node_type not in _VALID_NODE_TYPES:
                raise FlowValidationError(
                    f"Node '{key}': invalid type '{node_type}'. "
                    f"Must be one of: {sorted(_VALID_NODE_TYPES)}"
                )

            # depends_on 引用存在性
            depends_on = node.get("depends_on", [])
            for dep in depends_on:
                if dep not in keys:
                    raise FlowValidationError(
                        f"Node '{key}': depends_on references unknown key '{dep}'"
                    )

            # waker_task / leader_plan 必须有 waker
            if node_type in ("waker_task", "leader_plan") and not node.get("waker"):
                raise FlowValidationError(
                    f"Node '{key}': type '{node_type}' requires 'waker' field"
                )

            # condition 必须有 expression 和 branches
            if node_type == "condition":
                if not node.get("expression"):
                    raise FlowValidationError(
                        f"Node '{key}': type 'condition' requires 'expression' field"
                    )
                if not node.get("branches"):
                    raise FlowValidationError(
                        f"Node '{key}': type 'condition' requires 'branches' field"
                    )

            # notify 必须有 channel
            if node_type == "notify" and not node.get("channel"):
                raise FlowValidationError(
                    f"Node '{key}': type 'notify' requires 'channel' field"
                )

        # 环形依赖检测（拓扑排序）
        _check_cyclic_dependency(nodes)


def _check_cyclic_dependency(nodes: list[dict]) -> None:
    """检测节点依赖是否存在环."""
    # 构建邻接表
    in_degree: dict[str, int] = defaultdict(int)
    graph: dict[str, list[str]] = defaultdict(list)
    all_keys = {n["key"] for n in nodes}

    for node in nodes:
        key = node["key"]
        in_degree.setdefault(key, 0)
        for dep in node.get("depends_on", []):
            graph[dep].append(key)
            in_degree[key] += 1

    # BFS 拓扑排序
    queue = deque(k for k in all_keys if in_degree[k] == 0)
    visited = 0
    while queue:
        node_key = queue.popleft()
        visited += 1
        for neighbor in graph[node_key]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(all_keys):
        raise FlowValidationError("Circular dependency detected in flow nodes")
