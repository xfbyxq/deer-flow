"""同步引擎测试."""

import asyncio
import inspect
import time

import httpx
import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.deerflow.errors import (
    AgentNotFoundError,
    DeerFlowUnavailableError,
    ThreadNotFoundError,
)
from app.models.task import Task
from app.services.sync_engine import (
    _DEAD_ERRORS,
    _MAX_CONSECUTIVE_FAILURES,
    _TRANSIENT_ERRORS,
    SyncEngine,
)
from app.services.wake_engine import WakeEngine


@pytest.fixture
async def test_db_engine():
    """创建测试用内存数据库."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def test_session_factory(test_db_engine):
    return async_sessionmaker(test_db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def mock_deerflow():
    """构建 mock DeerFlowClient."""
    client = MagicMock()
    client.get_run = AsyncMock()
    client.get_thread_state = AsyncMock(return_value={"values": {"messages": []}})
    return client


async def _create_running_task(session_factory, task_id: str, thread_id: str = "thread-1", run_id: str = "run-1"):
    """辅助函数：创建 running 状态的 Task."""
    async with session_factory() as session:
        session.add(Task(
            id=task_id, kind="manual", executor="alice", status="running",
            input_text="test task", thread_id=thread_id, run_id=run_id, created_by="system",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        ))
        await session.commit()


# ------------------------------------------------------------------
# 状态同步测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_success(test_session_factory, mock_deerflow):
    """run 状态 success → TASK done；结果正文从成员 thread state 提取.

    回归：run 响应（get_run）本身不含 messages，旧实现据此提取导致
    result_summary 永远为空（真实环境成员产出丢失）；应复用 thread state 提取器。
    """
    await _create_running_task(test_session_factory, "task-1")
    mock_deerflow.get_run.return_value = {"status": "success"}
    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "任务完成，结果已生成",
                    "additional_kwargs": {"run_id": "run-1"},
                }
            ]
        }
    }

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    # 验证 TASK 状态与结果摘要（来自 thread state）
    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-1"))
        task = result.scalars().first()
        assert task.status == "done"
        assert task.result_summary == "任务完成，结果已生成"
    mock_deerflow.get_thread_state.assert_awaited()


@pytest.mark.asyncio
async def test_async_delegate_callback_invoked(test_session_factory, mock_deerflow):
    """回归：async_delegate 任务终态 → on_run_completed 回调被调用.

    历史故障：main.py 未向 SyncEngine 注入 async_delegate_service，
    回调被跳过，delegation_ledger 永远停留 running。
    """
    async with test_session_factory() as session:
        session.add(
            Task(
                id="t-async-delegate",
                kind="async_delegate",
                ticket_id="t-async-delegate",
                executor="alice",
                status="running",
                input_text="delegate task",
                thread_id="thread-ad",
                run_id="run-ad",
                created_by="bob",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    mock_deerflow.get_run.return_value = {"status": "success", "messages": []}

    mock_service = MagicMock()
    mock_service.on_run_completed = AsyncMock()
    engine = SyncEngine(
        test_session_factory, mock_deerflow, interval=1.0, async_delegate_service=mock_service
    )
    await engine._sync_once()

    mock_service.on_run_completed.assert_awaited_once()
    synced_task = mock_service.on_run_completed.await_args.args[0]
    assert synced_task.id == "t-async-delegate"
    assert synced_task.status == "done"


@pytest.mark.asyncio
async def test_non_delegate_task_skips_callback(test_session_factory, mock_deerflow):
    """非 async_delegate 任务不触发回调."""
    await _create_running_task(test_session_factory, "task-plain")
    mock_deerflow.get_run.return_value = {"status": "success", "messages": []}

    mock_service = MagicMock()
    mock_service.on_run_completed = AsyncMock()
    engine = SyncEngine(
        test_session_factory, mock_deerflow, interval=1.0, async_delegate_service=mock_service
    )
    await engine._sync_once()

    mock_service.on_run_completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_error(test_session_factory, mock_deerflow):
    """run 状态 error → TASK failed."""
    await _create_running_task(test_session_factory, "task-2")
    mock_deerflow.get_run.return_value = {"status": "error"}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-2"))
        task = result.scalars().first()
        assert task.status == "failed"


@pytest.mark.asyncio
async def test_sync_timeout(test_session_factory, mock_deerflow):
    """run 状态 timeout → TASK failed."""
    await _create_running_task(test_session_factory, "task-3")
    mock_deerflow.get_run.return_value = {"status": "timeout"}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-3"))
        task = result.scalars().first()
        assert task.status == "failed"


@pytest.mark.asyncio
async def test_sync_interrupted(test_session_factory, mock_deerflow):
    """run 状态 interrupted → TASK cancelled."""
    await _create_running_task(test_session_factory, "task-4")
    mock_deerflow.get_run.return_value = {"status": "interrupted"}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-4"))
        task = result.scalars().first()
        assert task.status == "cancelled"


# ------------------------------------------------------------------
# C3: 同步错误分治（瞬时 / 资源不存在 / 未预期）
# ------------------------------------------------------------------


async def _load_task(session_factory, task_id: str) -> Task:
    """辅助函数：重新加载 Task（避免受 expire 影响）."""
    async with session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        return result.scalars().first()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        DeerFlowUnavailableError("DeerFlow Gateway error: status=503 path=/runs"),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timeout"),
        asyncio.TimeoutError(),
        OSError("network is unreachable"),
        OperationalError("SELECT 1", None, Exception("database is locked")),
    ],
    ids=[
        "gateway-5xx",
        "httpx-connect",
        "httpx-timeout",
        "asyncio-timeout",
        "oserror",
        "db-locked",
    ],
)
async def test_transient_sync_error_keeps_task_running(
    test_session_factory, mock_deerflow, exc
):
    """C3 回归：瞬时错误不得把在跑任务判死（旧实现一律写终态 failed）.

    网关重启/抖动/SQLite 写锁期间，任务在 DeerFlow 侧仍在正常执行；
    只应记日志并等下一轮重试，且不占用失败预算。
    """
    await _create_running_task(test_session_factory, "task-transient")
    mock_deerflow.get_run.side_effect = exc

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    # 远超失败阀值的轮次：瞬时错误永不判死
    for _ in range(_MAX_CONSECUTIVE_FAILURES + 3):
        await engine._sync_once()

    task = await _load_task(test_session_factory, "task-transient")
    assert task.status == "running"
    assert task.result_summary is None
    assert engine._sync_failures == {}


@pytest.mark.asyncio
async def test_transient_error_recovers_on_next_round(test_session_factory, mock_deerflow):
    """C3：瞬时错误后下一轮恢复 → 正常写入终态（重试策略真实生效）."""
    await _create_running_task(test_session_factory, "task-recover")
    mock_deerflow.get_run.side_effect = [
        DeerFlowUnavailableError("DeerFlow Gateway error: status=502 path=/runs"),
        {"status": "success"},
    ]
    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "补跑成功",
                    "additional_kwargs": {"run_id": "run-1"},
                }
            ]
        }
    }

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()
    assert (await _load_task(test_session_factory, "task-recover")).status == "running"

    await engine._sync_once()
    task = await _load_task(test_session_factory, "task-recover")
    assert task.status == "done"
    assert task.result_summary == "补跑成功"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        ThreadNotFoundError("Thread not found: status=404 path=/threads/x"),
        AgentNotFoundError("Agent not found: status=404 path=/agents/x"),
    ],
    ids=["thread-404", "agent-404"],
)
async def test_missing_upstream_resource_marks_failed(
    test_session_factory, mock_deerflow, exc
):
    """C3：上游资源不存在（404）→ 立即判死 failed（重试无意义）."""
    await _create_running_task(test_session_factory, "task-gone")
    mock_deerflow.get_run.side_effect = exc

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    task = await _load_task(test_session_factory, "task-gone")
    assert task.status == "failed"
    assert "Sync error" in task.result_summary
    # 已终态 → 失败计数清理，不遗留内存
    assert engine._sync_failures == {}


@pytest.mark.asyncio
async def test_unexpected_error_tolerated_until_threshold(test_session_factory, mock_deerflow):
    """C3：未预期错误达 _MAX_CONSECUTIVE_FAILURES 才判死（阀值不再是死代码）."""
    await _create_running_task(test_session_factory, "task-weird")
    mock_deerflow.get_run.side_effect = RuntimeError("boom")

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)

    for round_no in range(1, _MAX_CONSECUTIVE_FAILURES):
        await engine._sync_once()
        task = await _load_task(test_session_factory, "task-weird")
        assert task.status == "running", f"第 {round_no} 轮不应判死"
        assert task.result_summary is None
        assert engine._sync_failures["task-weird"] == round_no

    await engine._sync_once()
    task = await _load_task(test_session_factory, "task-weird")
    assert task.status == "failed"
    assert "Sync error" in task.result_summary
    assert str(_MAX_CONSECUTIVE_FAILURES) in task.result_summary
    assert engine._sync_failures == {}


@pytest.mark.asyncio
async def test_unexpected_failure_counter_resets_after_successful_query(
    test_session_factory, mock_deerflow
):
    """C3：一次成功查询即清零计数（统计的是"连续"失败，不是累计）."""
    await _create_running_task(test_session_factory, "task-flaky")
    mock_deerflow.get_run.side_effect = [
        RuntimeError("boom"),
        RuntimeError("boom"),
        {"status": "running"},  # 非终态：不回写，但清零计数
        RuntimeError("boom"),
        RuntimeError("boom"),
    ]

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    for _ in range(5):
        await engine._sync_once()

    task = await _load_task(test_session_factory, "task-flaky")
    assert task.status == "running"
    assert engine._sync_failures["task-flaky"] == 2


def test_error_classification_tables_are_disjoint():
    """C3：资源不存在类不得同时属于瞬时类（否则 404 会无限重试）."""
    assert _DEAD_ERRORS
    assert _TRANSIENT_ERRORS
    for dead in _DEAD_ERRORS:
        assert not issubclass(dead, _TRANSIENT_ERRORS), dead


def test_max_consecutive_failures_constant_is_live_code():
    """C3 回归：_MAX_CONSECUTIVE_FAILURES 必须被引用（历史上定义了但从未使用）."""
    import app.services.sync_engine as sync_engine_module

    source = inspect.getsource(sync_engine_module)
    assert _MAX_CONSECUTIVE_FAILURES >= 2
    # 定义 + 至少一处使用
    assert source.count("_MAX_CONSECUTIVE_FAILURES") >= 2


@pytest.mark.asyncio
async def test_sync_multiple_tasks(test_session_factory, mock_deerflow):
    """多个 running 任务同时同步."""
    await _create_running_task(test_session_factory, "task-a", thread_id="t-a", run_id="r-a")
    await _create_running_task(test_session_factory, "task-b", thread_id="t-b", run_id="r-b")

    # task-a success, task-b error
    async def get_run_side_effect(thread_id, run_id):
        if thread_id == "t-a":
            return {"status": "success", "messages": [{"content": "done"}]}
        return {"status": "error"}

    mock_deerflow.get_run.side_effect = get_run_side_effect

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result_a = await session.execute(select(Task).where(Task.id == "task-a"))
        task_a = result_a.scalars().first()
        assert task_a.status == "done"

        result_b = await session.execute(select(Task).where(Task.id == "task-b"))
        task_b = result_b.scalars().first()
        assert task_b.status == "failed"


# ------------------------------------------------------------------
# 启停测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_start_stop(test_session_factory, mock_deerflow):
    """测试引擎启停."""
    engine = SyncEngine(test_session_factory, mock_deerflow, interval=0.1)

    await engine.start()
    assert engine._running is True
    assert engine._task is not None

    await engine.stop()
    assert engine._running is False


@pytest.mark.asyncio
async def test_sync_running_only(test_session_factory, mock_deerflow):
    """只同步 running 状态的任务，其他状态跳过."""
    # 创建不同状态的任务
    async with test_session_factory() as session:
        session.add(Task(
            id="task-running", kind="manual", executor="alice", status="running",
            input_text="running", thread_id="t1", run_id="r1", created_by="system",
        ))
        session.add(Task(
            id="task-done", kind="manual", executor="alice", status="done",
            input_text="done", thread_id="t2", run_id="r2", created_by="system",
        ))
        session.add(Task(
            id="task-failed", kind="manual", executor="alice", status="failed",
            input_text="failed", thread_id="t3", run_id="r3", created_by="system",
        ))
        await session.commit()

    mock_deerflow.get_run.return_value = {"status": "success", "messages": []}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    # 只调用了 running 任务的 get_run
    assert mock_deerflow.get_run.call_count == 1
    mock_deerflow.get_run.assert_called_with("t1", "r1")


# ------------------------------------------------------------------
# CF2：瞬时错误独立预算（超阈判死 / 成功清零）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_errors_exceed_budget_marks_failed(
    test_session_factory, mock_deerflow
):
    """CF2：瞬时错误超预算 → 判死 failed（网关长期不可达时任务不再永停 running）."""
    await _create_running_task(test_session_factory, "task-unreachable")
    mock_deerflow.get_run.side_effect = DeerFlowUnavailableError("gateway down")

    engine = SyncEngine(
        test_session_factory, mock_deerflow, interval=1.0, max_transient_failures=3
    )
    # 前 2 轮：仍 running，瞬时计数累积（不误判死）
    for round_no in (1, 2):
        await engine._sync_once()
        task = await _load_task(test_session_factory, "task-unreachable")
        assert task.status == "running", f"第 {round_no} 轮不应判死"
        assert engine._transient_failures["task-unreachable"] == round_no

    # 第 3 轮：超阈判死，result_summary 明确标注上游不可达
    await engine._sync_once()
    task = await _load_task(test_session_factory, "task-unreachable")
    assert task.status == "failed"
    assert task.result_summary == "Sync error: upstream unreachable"
    # 终态后瞬时计数被清理
    assert engine._transient_failures == {}


@pytest.mark.asyncio
async def test_transient_counter_resets_after_successful_query(
    test_session_factory, mock_deerflow
):
    """CF2：一次成功查询即清零瞬时计数（预算统计的是"连续"不可达）."""
    await _create_running_task(test_session_factory, "task-flaky-net")
    mock_deerflow.get_run.side_effect = [
        DeerFlowUnavailableError("down"),
        DeerFlowUnavailableError("down"),
        {"status": "running"},  # 恢复：清零计数
        DeerFlowUnavailableError("down"),
    ]
    engine = SyncEngine(
        test_session_factory, mock_deerflow, interval=1.0, max_transient_failures=3
    )
    await engine._sync_once()
    await engine._sync_once()
    assert engine._transient_failures["task-flaky-net"] == 2

    await engine._sync_once()  # 成功查询 → 清零
    assert "task-flaky-net" not in engine._transient_failures

    await engine._sync_once()  # 重新从 1 计，未达阈
    assert engine._transient_failures["task-flaky-net"] == 1
    task = await _load_task(test_session_factory, "task-flaky-net")
    assert task.status == "running"


# ------------------------------------------------------------------
# CF1：commit 失败触发 rollback，且不毒化同轮其它任务（独立短 session）
# ------------------------------------------------------------------


class _CommitFailOnceFactory:
    """包装真实 session factory：令**首个** commit 抛 OperationalError.

    模拟 SQLite 写锁：第一个任务的终态提交失败（归入瞬时错误分支），
    用于验证 CF1 —— 失败被独立短 session 物理隔离，不毒化同轮后续任务。
    """

    def __init__(self, inner):
        self._inner = inner
        self.commit_failures = 0
        self._fail_next = True

    def __call__(self):
        session = self._inner()
        real_commit = session.commit

        async def _commit():
            if self._fail_next:
                self._fail_next = False
                self.commit_failures += 1
                raise OperationalError(
                    "COMMIT", None, Exception("database is locked")
                )
            return await real_commit()

        session.commit = _commit
        return session


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_does_not_poison_next_task(
    test_session_factory, mock_deerflow
):
    """CF1：单任务 commit 失败（写锁）→ 回滚该任务，同轮后续健康任务仍成功终态.

    第一轮回归：共享 session 下 commit 失败不 rollback → 脏对象带入下一任务
    → PendingRollbackError → 健康任务被误判死。独立短 session 后物理隔离。
    """
    await _create_running_task(
        test_session_factory, "task-lock", thread_id="t-lock", run_id="r-lock"
    )
    await _create_running_task(
        test_session_factory, "task-ok", thread_id="t-ok", run_id="r-ok"
    )
    mock_deerflow.get_run.return_value = {"status": "success"}
    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {"type": "ai", "content": "完成", "additional_kwargs": {"run_id": "r-ok"}}
            ]
        }
    }

    factory = _CommitFailOnceFactory(test_session_factory)
    engine = SyncEngine(factory, mock_deerflow, interval=1.0)
    # 不得抛异常（失败被按任务隔离层吸收）
    await engine._sync_once()

    assert factory.commit_failures == 1  # 确有一次提交失败（否则用例无意义）

    # 首个任务：commit 失败被回滚 → 仍 running（下一轮重试），未被误判死
    task_lock = await _load_task(test_session_factory, "task-lock")
    assert task_lock.status == "running"
    # 后续健康任务：不受前一任务脏事务影响，正常写入终态
    task_ok = await _load_task(test_session_factory, "task-ok")
    assert task_ok.status == "done"
    assert task_ok.result_summary == "完成"


# ------------------------------------------------------------------
# CF5：_persist_failed 先 commit 成功再清计数 + stale 键裁剪
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_failed_commit_failure_keeps_counter(
    test_session_factory, mock_deerflow
):
    """CF5：_persist_failed 提交失败时不得清零计数（先 commit 成功、再 pop）.

    第一轮回归：先 pop 后 commit，提交失败时计数已归零 → 下一轮从头累计 →
    永远达不到判死阈值（无限重试）。
    """
    await _create_running_task(test_session_factory, "task-commit-fail")
    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    engine._sync_failures["task-commit-fail"] = _MAX_CONSECUTIVE_FAILURES

    async with test_session_factory() as session:
        async def _boom():
            raise OperationalError("COMMIT", None, Exception("database is locked"))

        session.commit = _boom
        with pytest.raises(OperationalError):
            await engine._persist_failed(session, "task-commit-fail", "Sync error: x")

    # 提交失败 → 计数保留到下一轮继续累计（未被清零）
    assert engine._sync_failures["task-commit-fail"] == _MAX_CONSECUTIVE_FAILURES


@pytest.mark.asyncio
async def test_stale_failure_counters_pruned_each_round(
    test_session_factory, mock_deerflow
):
    """CF5：任务离开 running 后，_sync_failures/_transient_failures 的 stale 键被裁剪."""
    await _create_running_task(test_session_factory, "task-left-running")
    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    engine._sync_failures["task-left-running"] = 1
    engine._transient_failures["task-left-running"] = 2
    # 一个"幽灵"键（对应早已不存在的任务）
    engine._sync_failures["task-ghost"] = 5

    # 把任务置为非 running（模拟已终态）
    async with test_session_factory() as session:
        task = (
            await session.execute(select(Task).where(Task.id == "task-left-running"))
        ).scalars().first()
        task.status = "done"
        await session.commit()

    mock_deerflow.get_run.return_value = {"status": "running"}
    await engine._sync_once()

    # stale 键（已离开 running 的任务 + 幽灵键）均被裁剪，杜绝单调增长的小泄漏
    assert "task-left-running" not in engine._sync_failures
    assert "task-left-running" not in engine._transient_failures
    assert "task-ghost" not in engine._sync_failures


# ------------------------------------------------------------------
# CF3：唤醒投递解耦到后台，_sync_once 不被限速阻塞
# ------------------------------------------------------------------


class _WakeDelegateStub:
    """最小 async_delegate_service：on_run_completed 时非阻塞入队唤醒."""

    def __init__(self, wake_engine: WakeEngine) -> None:
        self._wake_engine = wake_engine
        self.calls = 0

    async def on_run_completed(self, task: Task) -> None:
        self.calls += 1
        await self._wake_engine.wake(
            source_thread_id="parent-thread",
            source_agent_name="alice",
            result_summary=task.result_summary or "done",
            ticket_id=task.id,
            target_waker="bob",
        )


@pytest.mark.asyncio
async def test_sync_once_not_blocked_by_wake_rate_limit(
    test_session_factory, mock_deerflow
):
    """CF3 集成：多个 async_delegate 任务同时终态时，唤醒走后台队列，
    ``_sync_once`` 绝不在同步循环内等待限速窗口（旧实现队头阻塞最长 60s×N）.
    """
    for tid in ("ad-1", "ad-2"):
        async with test_session_factory() as session:
            session.add(
                Task(
                    id=tid,
                    kind="async_delegate",
                    executor="bob",
                    status="running",
                    input_text="x",
                    thread_id=f"th-{tid}",
                    run_id=f"run-{tid}",
                    created_by="system",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    mock_deerflow.get_run.return_value = {"status": "success"}
    mock_deerflow.get_thread_state.return_value = {"values": {"messages": []}}
    mock_deerflow.create_run = AsyncMock(return_value={"run_id": "wake-run"})

    # 小 max_per_minute + 长窗口 + 长超时：第二笔唤醒在旧实现会阻塞 ≤20s。
    wake_engine = WakeEngine(
        mock_deerflow,
        max_concurrency=1,
        max_per_minute=1,
        rate_window_seconds=30.0,
        throttle_wait_timeout_seconds=20.0,
        throttle_retry_budget=50,
        throttle_retry_delay_seconds=0.05,
    )
    await wake_engine.start()
    try:
        delegate = _WakeDelegateStub(wake_engine)
        engine = SyncEngine(
            test_session_factory,
            mock_deerflow,
            interval=1.0,
            async_delegate_service=delegate,
        )
        started = time.monotonic()
        await engine._sync_once()
        elapsed = time.monotonic() - started

        # 两个任务都触发了唤醒入队
        assert delegate.calls == 2
        # 同步循环远快于限速窗口（入队即返回，不被限速阻塞）
        assert elapsed < 2.0, f"_sync_once 被限速阻塞了 {elapsed:.2f}s"
        # 两个任务均已写入终态 done
        for tid in ("ad-1", "ad-2"):
            task = await _load_task(test_session_factory, tid)
            assert task.status == "done"
    finally:
        await wake_engine.stop()
