"""DAG 拓扑排序 + 就绪队列调度器."""

from collections import defaultdict, deque


class DAGCycleError(ValueError):
    """环形依赖检测异常."""


class DAGScheduler:
    """Flow 节点调度器 — 管理 DAG 依赖与并发."""

    def __init__(self, nodes: list[dict]) -> None:
        """初始化调度器.

        Parameters
        ----------
        nodes:
            Flow definition 中的 nodes 列表，每个 node 至少包含
            ``key`` 和 ``depends_on`` 字段。
        """
        self._nodes: dict[str, dict] = {n["key"]: n for n in nodes}
        self._completed: set[str] = set()
        self._running: set[str] = set()
        self._skipped: set[str] = set()

        # 构建邻接表与入度表
        self._downstream: dict[str, list[str]] = defaultdict(list)
        self._upstream: dict[str, set[str]] = {}
        for node in nodes:
            key = node["key"]
            deps = node.get("depends_on", [])
            self._upstream[key] = set(deps)
            for dep in deps:
                self._downstream[dep].append(key)

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------

    def mark_running(self, node_key: str) -> None:
        """标记节点为运行中."""
        self._running.add(node_key)

    def mark_completed(self, node_key: str) -> None:
        """标记节点为已完成."""
        self._running.discard(node_key)
        self._completed.add(node_key)

    def mark_skipped(self, node_key: str) -> None:
        """标记节点为跳过."""
        self._running.discard(node_key)
        self._skipped.add(node_key)

    # ------------------------------------------------------------------
    # 调度查询
    # ------------------------------------------------------------------

    def get_ready_nodes(self, max_concurrent: int) -> list[dict]:
        """获取所有依赖已满足且未运行的节点.

        Parameters
        ----------
        max_concurrent:
            最大并行节点数，限制返回数量。

        Returns
        -------
        可立即执行的节点定义列表。
        """
        active_count = len(self._running)
        available_slots = max(0, max_concurrent - active_count)
        if available_slots == 0:
            return []

        ready: list[dict] = []
        for key, node in self._nodes.items():
            if key in self._completed or key in self._running or key in self._skipped:
                continue
            # 检查所有依赖是否已完成
            deps = self._upstream.get(key, set())
            if deps.issubset(self._completed):
                ready.append(node)
                if len(ready) >= available_slots:
                    break
        return ready

    def is_all_done(self) -> bool:
        """所有节点都已完成或跳过."""
        terminal = self._completed | self._skipped
        return terminal == set(self._nodes.keys())

    def get_downstream(self, node_key: str) -> list[str]:
        """获取下游节点 key 列表."""
        return list(self._downstream.get(node_key, []))

    # ------------------------------------------------------------------
    # 拓扑排序（验证/调试用）
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        """Kahn 算法拓扑排序.

        Returns
        -------
        节点 key 的拓扑顺序列表。

        Raises
        ------
        DAGCycleError
            存在环形依赖时抛出。
        """
        in_degree: dict[str, int] = {k: 0 for k in self._nodes}
        for key, deps in self._upstream.items():
            in_degree[key] = len(deps)

        queue = deque(k for k, d in in_degree.items() if d == 0)
        order: list[str] = []

        while queue:
            key = queue.popleft()
            order.append(key)
            for downstream_key in self._downstream.get(key, []):
                in_degree[downstream_key] -= 1
                if in_degree[downstream_key] == 0:
                    queue.append(downstream_key)

        if len(order) != len(self._nodes):
            raise DAGCycleError("Circular dependency detected in flow nodes")

        return order
