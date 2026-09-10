"""统一的 DeerFlow run status → TASK status 映射.

基于 M0 S4 冒烟结论:
- success → done
- error / timeout → failed
- interrupted → cancelled (DeerFlow cancel 后终态为 interrupted)
- pending / running → 对应 pending / running
- 未知状态 → failed (防御式)
"""

RUN_TO_TASK_STATUS: dict[str, str] = {
    "success": "done",
    "error": "failed",
    "timeout": "failed",
    "interrupted": "cancelled",
    "pending": "pending",
    "running": "running",
}


def map_run_to_task_status(run_status: str) -> str:
    """将 DeerFlow run 状态映射为 TASK 状态.

    未知状态防御式映射为 ``"failed"``.
    """
    return RUN_TO_TASK_STATUS.get(run_status, "failed")
