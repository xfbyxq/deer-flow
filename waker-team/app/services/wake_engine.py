"""唤醒 run 构造与投递.

当异步委派完成后，在源 agent 的 thread 上发起唤醒 run，
注入目标 agent 的结果摘要，让源 agent 感知委派结果。
"""

import logging
import uuid

from app.deerflow.client import DeerFlowClient, build_run_configuration

logger = logging.getLogger(__name__)


class WakeDeliveryError(Exception):
    """唤醒 run 投递失败."""

    pass


class WakeEngine:
    """构造并投递唤醒 run."""

    def __init__(self, deerflow_client: DeerFlowClient) -> None:
        self._client = deerflow_client

    async def wake(
        self,
        source_thread_id: str,
        source_agent_name: str,
        result_summary: str,
        ticket_id: str = "",
        target_waker: str = "",
    ) -> str:
        """在 A 的 thread 上发起唤醒 run，注入 B 的结果摘要。

        Parameters
        ----------
        source_thread_id:
            源 agent（发起委派的 agent）的 DeerFlow thread id。
        source_agent_name:
            源 agent 名称（用于 configurable.agent_name）。
        result_summary:
            目标 agent 的执行结果摘要。
        ticket_id:
            异步委派 ticket id（用于追溯）。
        target_waker:
            目标 waker 名（用于消息内容）。

        Returns
        -------
        唤醒 run 的 run_id。

        Raises
        ------
        WakeDeliveryError
            投递失败时抛出。
        """
        wake_input = self.build_wake_input(
            result_summary=result_summary,
            ticket_id=ticket_id,
            target_waker=target_waker,
        )

        run_body = {
            "input": wake_input,
            "config": build_run_configuration(source_agent_name),
        }

        try:
            run_resp = await self._client.create_run(
                thread_id=source_thread_id,
                body=run_body,
                idempotency_key=f"wake-{ticket_id}-{uuid.uuid4().hex[:8]}",
            )
            run_id = run_resp.get("run_id")
            if not run_id:
                raise WakeDeliveryError(
                    f"DeerFlow create_run returned no run_id for wake-{ticket_id}"
                )
            logger.info(
                "Wake run delivered: thread=%s run=%s ticket=%s",
                source_thread_id, run_id, ticket_id,
            )
            return run_id
        except WakeDeliveryError:
            raise
        except Exception as exc:
            raise WakeDeliveryError(
                f"Failed to deliver wake run for ticket {ticket_id}: {exc}"
            ) from exc

    def build_wake_input(
        self,
        result_summary: str,
        ticket_id: str = "",
        target_waker: str = "",
    ) -> dict:
        """构造唤醒 run 的 input payload.

        格式参考 Team Briefing 注入方式：以 user message 注入委派结果通知。
        """
        content = (
            f"[异步委派结果通知]\n"
            f"ticket_id: {ticket_id}\n"
            f"执行者: {target_waker}\n"
            f"结果摘要:\n{result_summary}\n"
            f"---\n"
            f"请根据以上结果继续你的工作。"
        )
        return {
            "messages": [
                {"role": "user", "content": content},
            ]
        }
