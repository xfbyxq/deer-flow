"""leader_plan JSON 解析测试."""

import pytest

from app.engine.flow_plan_parser import parse_leader_plan_output, validate_subtasks


# ------------------------------------------------------------------
# 1. 直接 JSON 解析
# ------------------------------------------------------------------


def test_parse_direct_json():
    """直接 JSON 数组解析."""
    text = '[{"waker": "alice", "instruction": "do something"}]'
    result = parse_leader_plan_output(text)
    assert len(result) == 1
    assert result[0]["waker"] == "alice"


# ------------------------------------------------------------------
# 2. markdown code block 提取
# ------------------------------------------------------------------


def test_parse_markdown_code_block():
    """从 markdown 代码块提取 JSON."""
    text = """
Here is the plan:

```json
[
  {"waker": "bob", "instruction": "review code"},
  {"waker": "alice", "instruction": "write tests"}
]
```

Please proceed.
"""
    result = parse_leader_plan_output(text)
    assert len(result) == 2
    assert result[0]["waker"] == "bob"
    assert result[1]["waker"] == "alice"


# ------------------------------------------------------------------
# 3. 混杂文本中提取 JSON 数组
# ------------------------------------------------------------------


def test_parse_mixed_text():
    """从混杂文本中提取 JSON 数组."""
    text = """
Based on the analysis, here are the subtasks:
[{"waker": "charlie", "instruction": "deploy"}, {"waker": "dave", "instruction": "monitor"}]
These tasks should be executed in order.
"""
    result = parse_leader_plan_output(text)
    assert len(result) == 2
    assert result[0]["waker"] == "charlie"


# ------------------------------------------------------------------
# 4. 非法 JSON 返回空列表
# ------------------------------------------------------------------


def test_parse_invalid_json_returns_empty():
    """非法 JSON 返回空列表."""
    text = "This is just plain text without any JSON"
    result = parse_leader_plan_output(text)
    assert result == []


def test_parse_empty_string_returns_empty():
    """空字符串返回空列表."""
    assert parse_leader_plan_output("") == []
    assert parse_leader_plan_output(None) == []


# ------------------------------------------------------------------
# 5. 空数组校验失败
# ------------------------------------------------------------------


def test_validate_empty_subtasks():
    """空数组校验失败."""
    errors = validate_subtasks([], {"alice", "bob"})
    assert len(errors) > 0
    assert "empty" in errors[0].lower()


# ------------------------------------------------------------------
# 6. 缺少 waker 字段校验失败
# ------------------------------------------------------------------


def test_validate_missing_waker():
    """缺少 waker 字段校验失败."""
    subtasks = [{"instruction": "do something"}]
    errors = validate_subtasks(subtasks, {"alice"})
    assert any("waker" in e.lower() for e in errors)


# ------------------------------------------------------------------
# 7. 未知 waker 校验失败
# ------------------------------------------------------------------


def test_validate_unknown_waker():
    """未知 waker 校验失败."""
    subtasks = [{"waker": "unknown_person", "instruction": "do something"}]
    errors = validate_subtasks(subtasks, {"alice", "bob"})
    assert any("unknown" in e.lower() for e in errors)


# ------------------------------------------------------------------
# 8. 合法子任务校验通过
# ------------------------------------------------------------------


def test_validate_valid_subtasks():
    """合法子任务校验通过."""
    subtasks = [
        {"waker": "alice", "instruction": "write code"},
        {"waker": "bob", "instruction": "review code"},
    ]
    errors = validate_subtasks(subtasks, {"alice", "bob"})
    assert errors == []


# ------------------------------------------------------------------
# 9. 缺少 instruction 字段校验失败
# ------------------------------------------------------------------


def test_validate_missing_instruction():
    """缺少 instruction 字段校验失败."""
    subtasks = [{"waker": "alice"}]
    errors = validate_subtasks(subtasks, {"alice"})
    assert any("instruction" in e.lower() for e in errors)
