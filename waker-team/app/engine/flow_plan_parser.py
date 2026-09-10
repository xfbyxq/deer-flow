"""leader_plan 节点输出解析 — 提取子任务 JSON 数组."""

import json
import logging
import re

logger = logging.getLogger(__name__)


def parse_leader_plan_output(output_text: str) -> list[dict]:
    """解析 leader_plan 节点的输出，提取子任务 JSON 数组.

    容错策略（按优先级）：
    1. 直接 json.loads
    2. 提取 ```json ... ``` 代码块
    3. 查找第一个 ``[`` 和最后一个 ``]`` 之间的内容

    Parameters
    ----------
    output_text:
        leader_plan 节点的原始输出文本。

    Returns
    -------
    解析后的子任务列表；解析失败返回空列表。
    """
    if not output_text or not isinstance(output_text, str):
        return []

    text = output_text.strip()

    # 策略 1：直接 JSON 解析
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    # 策略 2：提取 ```json ... ``` 代码块
    code_block_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
    match = code_block_pattern.search(text)
    if match:
        try:
            result = json.loads(match.group(1).strip())
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, TypeError):
            pass

    # 策略 3：查找第一个 [ 和最后一个 ] 之间的内容
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        try:
            result = json.loads(text[first_bracket : last_bracket + 1])
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, TypeError):
            pass

    logger.warning("Failed to parse leader_plan output as JSON array")
    return []


def validate_subtasks(subtasks: list[dict], known_wakers: set[str]) -> list[str]:
    """校验子任务列表.

    每个子任务必须包含 ``waker`` 和 ``instruction`` 字段，
    且 ``waker`` 必须在 ``known_wakers`` 中。

    Parameters
    ----------
    subtasks:
        解析后的子任务列表。
    known_wakers:
        当前 group 中已知的 waker 名称集合。

    Returns
    -------
    错误消息列表；空列表表示校验通过。
    """
    errors: list[str] = []

    if not subtasks:
        errors.append("Subtask list is empty")
        return errors

    for i, subtask in enumerate(subtasks):
        if not isinstance(subtask, dict):
            errors.append(f"Subtask at index {i} is not an object")
            continue

        waker = subtask.get("waker")
        if not waker:
            errors.append(f"Subtask at index {i} missing required field 'waker'")
        elif waker not in known_wakers:
            errors.append(
                f"Subtask at index {i}: unknown waker '{waker}'. "
                f"Known wakers: {sorted(known_wakers)}"
            )

        instruction = subtask.get("instruction")
        if not instruction:
            errors.append(f"Subtask at index {i} missing required field 'instruction'")

    return errors
