"""DAG 调度器测试."""

import pytest

from app.engine.dag import DAGCycleError, DAGScheduler


# ------------------------------------------------------------------
# 1. 线性链 A→B→C 拓扑排序
# ------------------------------------------------------------------


def test_linear_topological_sort():
    """线性链 A→B→C 拓扑排序结果为 [A, B, C]."""
    nodes = [
        {"key": "A", "depends_on": []},
        {"key": "B", "depends_on": ["A"]},
        {"key": "C", "depends_on": ["B"]},
    ]
    scheduler = DAGScheduler(nodes)
    order = scheduler.topological_sort()
    assert order == ["A", "B", "C"]


# ------------------------------------------------------------------
# 2. 并行节点 A→[B,C]→D
# ------------------------------------------------------------------


def test_parallel_topological_sort():
    """并行节点 A→[B,C]→D 拓扑排序."""
    nodes = [
        {"key": "A", "depends_on": []},
        {"key": "B", "depends_on": ["A"]},
        {"key": "C", "depends_on": ["A"]},
        {"key": "D", "depends_on": ["B", "C"]},
    ]
    scheduler = DAGScheduler(nodes)
    order = scheduler.topological_sort()
    assert order[0] == "A"
    assert order[-1] == "D"
    # B 和 C 都在 A 之后 D 之前
    assert set(order[1:3]) == {"B", "C"}


# ------------------------------------------------------------------
# 3. get_ready_nodes 只返回依赖已完成的节点
# ------------------------------------------------------------------


def test_get_ready_nodes_respects_dependencies():
    """get_ready_nodes 只返回依赖已完成的节点."""
    nodes = [
        {"key": "A", "depends_on": []},
        {"key": "B", "depends_on": ["A"]},
        {"key": "C", "depends_on": ["A", "B"]},
    ]
    scheduler = DAGScheduler(nodes)

    # 初始状态：只有 A 就绪
    ready = scheduler.get_ready_nodes(max_concurrent=5)
    assert len(ready) == 1
    assert ready[0]["key"] == "A"

    # 标记 A 运行中
    scheduler.mark_running("A")
    ready = scheduler.get_ready_nodes(max_concurrent=5)
    assert len(ready) == 0  # A 在运行，B/C 依赖未完成

    # 标记 A 完成
    scheduler.mark_completed("A")
    ready = scheduler.get_ready_nodes(max_concurrent=5)
    assert len(ready) == 1
    assert ready[0]["key"] == "B"  # B 就绪（C 依赖 B 未完成）

    # 标记 B 完成
    scheduler.mark_completed("B")
    ready = scheduler.get_ready_nodes(max_concurrent=5)
    assert len(ready) == 1
    assert ready[0]["key"] == "C"


# ------------------------------------------------------------------
# 4. max_concurrent 限制生效
# ------------------------------------------------------------------


def test_get_ready_nodes_max_concurrent():
    """max_concurrent 限制生效."""
    nodes = [
        {"key": "A", "depends_on": []},
        {"key": "B", "depends_on": []},
        {"key": "C", "depends_on": []},
    ]
    scheduler = DAGScheduler(nodes)

    # max_concurrent=2：只返回 2 个
    ready = scheduler.get_ready_nodes(max_concurrent=2)
    assert len(ready) == 2

    # 标记一个运行中
    scheduler.mark_running(ready[0]["key"])
    ready = scheduler.get_ready_nodes(max_concurrent=2)
    assert len(ready) == 1  # 只剩 1 个槽位


# ------------------------------------------------------------------
# 5. 环形依赖检测
# ------------------------------------------------------------------


def test_cyclic_dependency_detection():
    """环形依赖 → DAGCycleError."""
    nodes = [
        {"key": "A", "depends_on": ["C"]},
        {"key": "B", "depends_on": ["A"]},
        {"key": "C", "depends_on": ["B"]},
    ]
    scheduler = DAGScheduler(nodes)
    with pytest.raises(DAGCycleError, match="[Cc]ircular"):
        scheduler.topological_sort()


# ------------------------------------------------------------------
# 6. is_all_done 判断
# ------------------------------------------------------------------


def test_is_all_done():
    """is_all_done 在所有节点完成/跳过后返回 True."""
    nodes = [
        {"key": "A", "depends_on": []},
        {"key": "B", "depends_on": ["A"]},
    ]
    scheduler = DAGScheduler(nodes)

    assert not scheduler.is_all_done()

    scheduler.mark_completed("A")
    assert not scheduler.is_all_done()

    scheduler.mark_completed("B")
    assert scheduler.is_all_done()


def test_is_all_done_with_skip():
    """is_all_done 在节点跳过后也返回 True."""
    nodes = [
        {"key": "A", "depends_on": []},
        {"key": "B", "depends_on": ["A"]},
    ]
    scheduler = DAGScheduler(nodes)

    scheduler.mark_completed("A")
    scheduler.mark_skipped("B")
    assert scheduler.is_all_done()


# ------------------------------------------------------------------
# 7. get_downstream 获取下游节点
# ------------------------------------------------------------------


def test_get_downstream():
    """get_downstream 返回正确的下游节点."""
    nodes = [
        {"key": "A", "depends_on": []},
        {"key": "B", "depends_on": ["A"]},
        {"key": "C", "depends_on": ["A"]},
    ]
    scheduler = DAGScheduler(nodes)

    downstream = scheduler.get_downstream("A")
    assert set(downstream) == {"B", "C"}

    downstream = scheduler.get_downstream("B")
    assert downstream == []
