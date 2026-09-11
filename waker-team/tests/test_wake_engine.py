"""WakeEngine 后台投递测试（CF3 / CF4 / CF16 / CF18）.

覆盖第二轮修复包 A 的核心：唤醒投递已从 SyncEngine 的串行轮询循环解耦到
后台队列 + worker 池。测试聚焦：
- wake() 非阻塞入队（限速窗口不在调用方线程内等待）；
- worker 池真正并发（wake_max_concurrency 生效）；
- 惰性启动 / start-stop 幂等；
- 有界队列满 → WakeQueueFullError 背压信号；
- 限速超时后台重排重试，最终投递或经 on_failed(WakeThrottledError) 上报；
- 真实投递失败经 on_failed(WakeDeliveryError) 上报；
- 异常语义分离契约（WakeThrottledError 是 WakeDeliveryError 子类）；
- CF16 忙等下限、CF18 per-process 文档。
"""

import asyncio
import inspect
import time

import pytest

import app.services.wake_engine as wake_module
from app.services.wake_engine import (
    WakeDeliveryError,
    WakeEngine,
    WakeQueueFullError,
    WakeThrottledError,
)


class _RecordingClient:
    """记录 create_run 调用数与在途并发峰值的假 DeerFlow 客户端."""

    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay
        self.calls = 0
        self.in_flight = 0
        self.peak = 0
        self.bodies: list[dict] = []

    async def create_run(self, *, thread_id, body, idempotency_key=None):
        self.calls += 1
        self.bodies.append(body)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
        finally:
            self.in_flight -= 1
        return {"run_id": f"wake-run-{self.calls}"}


def _wake_kwargs(ticket_id: str, **overrides):
    base = {
        "source_thread_id": f"thread-{ticket_id}",
        "source_agent_name": "alice",
        "result_summary": "结果",
        "ticket_id": ticket_id,
        "target_waker": "bob",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# CF3：wake() 非阻塞入队（核心）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_is_non_blocking_under_rate_limit():
    """CF3 核心：限速窗口内 wake() 立即返回（入队），绝不在调用方内等待限速窗口.

    第一轮回归：限速等待嵌在串行轮询循环内，队头阻塞最长 60s×N。
    """
    client = _RecordingClient()
    engine = WakeEngine(
        client,
        max_concurrency=1,
        max_per_minute=1,
        rate_window_seconds=30.0,
        throttle_wait_timeout_seconds=20.0,
        throttle_retry_budget=50,
        throttle_retry_delay_seconds=0.05,
    )
    first_delivered = asyncio.Event()

    async def _on_first(run_id: str) -> None:
        first_delivered.set()

    await engine.start()
    try:
        # 第一笔正常投递（占据限速窗口的唯一额度）
        await engine.wake(**_wake_kwargs("a", on_delivered=_on_first))
        await asyncio.wait_for(first_delivered.wait(), timeout=2.0)
        assert client.calls == 1

        # 第二笔触发限速（窗口 30s）——但 wake() 必须立即返回，不阻塞调用方
        started = time.monotonic()
        delivery_id = await engine.wake(**_wake_kwargs("b"))
        elapsed = time.monotonic() - started

        assert delivery_id  # 返回本地 delivery_id
        assert elapsed < 0.5, f"wake() 被限速阻塞了 {elapsed:.2f}s（应非阻塞入队）"
        # 第二笔此刻仍被限速卡在后台，尚未真正 create_run
        assert client.calls == 1
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_worker_delivers_and_invokes_on_delivered():
    """后台 worker 消费队列 → create_run → on_delivered(run_id) 回调."""
    client = _RecordingClient()
    engine = WakeEngine(client, max_concurrency=2, max_per_minute=0)
    delivered: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def _on_delivered(run_id: str) -> None:
        if not delivered.done():
            delivered.set_result(run_id)

    await engine.start()
    try:
        await engine.wake(**_wake_kwargs("tk", on_delivered=_on_delivered))
        run_id = await asyncio.wait_for(delivered, timeout=2.0)
        assert run_id == "wake-run-1"
        assert client.calls == 1
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_wake_before_start_lazily_launches_workers():
    """CF3：wake() 在 start() 之前被调用 → 惰性拉起 worker（兼容 mcp/server 构造点）."""
    client = _RecordingClient()
    engine = WakeEngine(client, max_concurrency=1, max_per_minute=0)
    delivered = asyncio.Event()

    async def _on_delivered(run_id: str) -> None:
        delivered.set()

    # 故意不调用 start()
    await engine.wake(**_wake_kwargs("lazy", on_delivered=_on_delivered))
    await asyncio.wait_for(delivered.wait(), timeout=2.0)
    assert engine.worker_count >= 1
    assert client.calls == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_start_stop_are_idempotent():
    """start()/stop() 幂等：重复调用不报错、不重复拉起 worker."""
    client = _RecordingClient()
    engine = WakeEngine(client, max_concurrency=3, max_per_minute=0)

    await engine.start()
    await engine.start()  # 幂等
    assert engine.worker_count == 3

    await engine.stop()
    await engine.stop()  # 幂等
    assert engine.worker_count == 0


@pytest.mark.asyncio
async def test_worker_pool_caps_concurrent_create_run():
    """CF3：wake_max_concurrency 真正生效 —— 同时在途 create_run <= worker 数."""
    client = _RecordingClient(delay=0.02)
    engine = WakeEngine(client, max_concurrency=2, max_per_minute=0)
    all_delivered = asyncio.Event()
    done_count = 0

    async def _on_delivered(run_id: str) -> None:
        nonlocal done_count
        done_count += 1
        if done_count == 6:
            all_delivered.set()

    await engine.start()
    try:
        for i in range(6):
            await engine.wake(**_wake_kwargs(f"tk-{i}", on_delivered=_on_delivered))
        await asyncio.wait_for(all_delivered.wait(), timeout=3.0)
        assert client.calls == 6
        assert client.peak <= 2
    finally:
        await engine.stop()


# ------------------------------------------------------------------
# CF3：有界队列满 → 背压信号
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_full_raises_wake_queue_full_error():
    """CF3：有界队列满且有界等待超时 → WakeQueueFullError（背压），不阻塞限速窗口时长."""
    client = _RecordingClient()
    engine = WakeEngine(
        client,
        max_concurrency=1,
        max_per_minute=0,
        queue_maxsize=1,
        queue_put_timeout_seconds=0.05,
    )
    # 阻止 worker 消费（模拟 worker 全忙 / 队列积压），令队列真正填满。
    engine._stopping = True

    started = time.monotonic()
    await engine.wake(**_wake_kwargs("fill"))  # 填满 maxsize=1
    with pytest.raises(WakeQueueFullError):
        await engine.wake(**_wake_kwargs("overflow"))
    elapsed = time.monotonic() - started
    # 仅短暂有界等待（queue_put_timeout），绝非限速窗口级阻塞
    assert elapsed < 1.0
    assert client.calls == 0  # worker 未消费，从未真正投递


# ------------------------------------------------------------------
# CF4：限速重排后台重试 / 预算耗尽上报 WakeThrottledError
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_throttle_requeues_in_background_and_eventually_delivers():
    """CF3/CF4：限速超时 → 后台重排重试，窗口滑出后最终投递成功（不改任务终态）."""
    client = _RecordingClient()
    engine = WakeEngine(
        client,
        max_concurrency=1,
        max_per_minute=1,
        rate_window_seconds=0.25,
        throttle_wait_timeout_seconds=0.02,
        throttle_retry_budget=20,
        throttle_retry_delay_seconds=0.05,
    )
    delivered: list[str] = []
    second_done = asyncio.Event()

    async def _on_delivered(run_id: str) -> None:
        delivered.append(run_id)
        if len(delivered) == 2:
            second_done.set()

    async def _on_failed(exc: Exception) -> None:
        pytest.fail(f"背压重试不应最终失败: {exc}")

    await engine.start()
    try:
        await engine.wake(**_wake_kwargs("a", on_delivered=_on_delivered, on_failed=_on_failed))
        # 第二笔会先被限速，随后在后台重排、窗口滑出后投递成功
        started = time.monotonic()
        await engine.wake(**_wake_kwargs("b", on_delivered=_on_delivered, on_failed=_on_failed))
        assert time.monotonic() - started < 0.2  # 入队非阻塞
        await asyncio.wait_for(second_done.wait(), timeout=3.0)
        assert client.calls == 2
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_throttle_exhausts_budget_reports_throttled_error():
    """CF4：限速重试预算耗尽 → on_failed(WakeThrottledError)（本地背压，非真实失败）."""
    client = _RecordingClient()
    engine = WakeEngine(
        client,
        max_concurrency=1,
        max_per_minute=1,
        rate_window_seconds=30.0,
        throttle_wait_timeout_seconds=0.02,
        throttle_retry_budget=1,
        throttle_retry_delay_seconds=0.02,
    )
    first_delivered = asyncio.Event()
    failed: asyncio.Future[Exception] = asyncio.get_running_loop().create_future()

    async def _on_first(run_id: str) -> None:
        first_delivered.set()

    async def _on_failed(exc: Exception) -> None:
        if not failed.done():
            failed.set_result(exc)

    await engine.start()
    try:
        await engine.wake(**_wake_kwargs("a", on_delivered=_on_first))
        await asyncio.wait_for(first_delivered.wait(), timeout=2.0)

        await engine.wake(**_wake_kwargs("b", on_failed=_on_failed))
        exc = await asyncio.wait_for(failed, timeout=3.0)
        assert isinstance(exc, WakeThrottledError)
        assert "throttled" in str(exc)
        assert client.calls == 1  # 第二笔被护栏拦下，未拉起 run
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_real_delivery_error_reported_via_on_failed():
    """真实上游投递失败（create_run 报错）→ on_failed(WakeDeliveryError)."""

    class _FailingClient:
        async def create_run(self, *, thread_id, body, idempotency_key=None):
            raise RuntimeError("upstream 500")

    engine = WakeEngine(_FailingClient(), max_concurrency=1, max_per_minute=0)
    failed: asyncio.Future[Exception] = asyncio.get_running_loop().create_future()

    async def _on_failed(exc: Exception) -> None:
        if not failed.done():
            failed.set_result(exc)

    await engine.start()
    try:
        await engine.wake(**_wake_kwargs("boom", on_failed=_on_failed))
        exc = await asyncio.wait_for(failed, timeout=2.0)
        assert isinstance(exc, WakeDeliveryError)
        assert not isinstance(exc, WakeThrottledError)  # 真实失败，非背压
    finally:
        await engine.stop()


# ------------------------------------------------------------------
# 契约与文档（CF4 异常分离 / CF16 忙等下限 / CF18 per-process）
# ------------------------------------------------------------------


def test_wake_throttled_is_subclass_of_delivery_error():
    """跨包契约：WakeThrottledError 子类化 WakeDeliveryError（兼容旧 except 调用点）."""
    assert issubclass(WakeThrottledError, WakeDeliveryError)
    assert issubclass(WakeQueueFullError, WakeDeliveryError)
    # 三者语义可区分
    assert WakeThrottledError is not WakeDeliveryError
    assert WakeQueueFullError is not WakeThrottledError


def test_rate_slot_wait_has_defensive_floor():
    """CF16：_acquire_rate_slot 对 wait 加防御性下限，杜绝忙等烧 CPU."""
    source = inspect.getsource(wake_module.WakeEngine._acquire_rate_slot)
    assert "max(wait, 0.001)" in source
    # 脆弱的忙等分支不得复活
    assert "if wait <= 0:" not in source
    assert "if wait<=0:" not in source


def test_module_documents_per_process_thresholds():
    """CF18：模块/类 docstring 显式声明阈值为 per-process 语义 + worker 池并发."""
    doc = (wake_module.__doc__ or "") + (wake_module.WakeEngine.__doc__ or "")
    assert "per-process" in doc or "按进程计" in doc
    assert "worker" in doc
    # 全局上界 = 阈值 × 进程数
    assert "进程数" in doc


def test_wake_engine_docstring_mentions_delegation_depth_bound():
    """护栏语义不丢失：docstring 声明委派链深度上界由 DelegationGuard 约束."""
    doc = (wake_module.__doc__ or "") + (wake_module.WakeEngine.__doc__ or "")
    assert "MAX_DEPTH" in doc
