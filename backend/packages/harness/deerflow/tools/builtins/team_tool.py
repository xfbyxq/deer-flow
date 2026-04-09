"""用于顺序调用多个子智能体的团队工具。"""

import asyncio
import logging
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langgraph.config import get_stream_writer
from langgraph.typing import ContextT

from deerflow.agents.lead_agent.prompt import get_skills_prompt_section
from deerflow.agents.thread_state import ThreadState
from deerflow.config.teams_config import get_team_config, list_teams
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.executor import SubagentStatus, cleanup_background_task, get_background_task_result

logger = logging.getLogger(__name__)


@tool("invoke_team", parse_docstring=True)
async def invoke_team_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    prompt: str,
    team_name: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> str:
    """调用专业子智能体团队来完成复杂任务。

    团队允许多个专业智能体协同工作来完成复杂任务。
    每个团队成员发挥其专业特长，以产生更好的结果。

    可用团队（在 config.yaml 中配置）：
    - novel-writing-team：一个用于小说创作的三专业智能体团队：
      * story-planner：创作故事概念、情节和角色设计
      * content-writer：撰写实际的故事情节内容
      * proofreader：审核内容错误和一致性

    何时使用此工具：
    - 需要多种专业技能的复杂任务
    - 创意任务，如写故事、文章或内容
    - 受益于顺序迭代的任务

    何时不使用此工具：
    - 简单的单步任务（请使用 task 工具）
    - 只需要一种专业技能的任务

    Args:
        runtime: 工具运行时上下文
        description: 任务的简短描述（3-5 个字）
        prompt: 团队的详细任务描述
        team_name: 要调用的团队名称
        tool_call_id: 工具调用ID（自动注入）

    Returns:
        团队执行的摘要和结果
    """
    available_teams = list_teams()

    if not available_teams:
        return "错误：未配置任何团队。请在 config.yaml 中配置团队。"

    if team_name not in available_teams:
        available = ", ".join(available_teams)
        return f"错误：未知团队 '{team_name}'。可用团队：{available}"

    team_config = get_team_config(team_name)
    if not team_config:
        return f"错误：未找到团队 '{team_name}'"

    if not team_config.members:
        return f"错误：团队 '{team_name}' 未配置成员"

    available_subagent_names = get_available_subagent_names()

    for member in team_config.members:
        if member not in available_subagent_names:
            return f"错误：团队成员 '{member}' 不可用。可用智能体：{available_subagent_names}"

    thread_id = runtime.thread_id
    trace_id = f"team-{team_name}-{description[:20]}"

    writer = get_stream_writer()
    writer({"type": "team_started", "team_name": team_name, "description": description})

    context = prompt
    results = []

    for i, agent_name in enumerate(team_config.members):
        agent_config = get_subagent_config(agent_name)
        if agent_config is None:
            return f"错误：未找到智能体 '{agent_name}' 的配置"

        logger.info(f"[trace={trace_id}] 正在执行团队成员 {agent_name} ({i+1}/{len(team_config.members)})")

        writer({"type": "team_agent_started", "agent_name": agent_name, "step": i + 1, "total": len(team_config.members)})

        from deerflow.agents.lead_agent.agent import get_primary_model_name
        from deerflow.runtime.runs.manager import RunManager
        from deerflow.tools import get_available_tools

        run_manager = RunManager.get_instance()
        run = run_manager.get_run(thread_id)
        parent_model = get_primary_model_name(run) if run else None

        tools = get_available_tools(model_name=parent_model, subagent_enabled=False)

        executor = SubagentExecutor(
            config=agent_config,
            tools=tools,
            parent_model=parent_model,
            sandbox_state=runtime.state.sandbox if hasattr(runtime.state, "sandbox") else None,
            thread_data=runtime.state.data if hasattr(runtime.state, "data") else None,
            thread_id=thread_id,
            trace_id=trace_id,
        )

        agent_prompt = f"上一轮上下文：\n{context}\n\n---\n\n您的任务：{prompt}"

        skills_section = get_skills_prompt_section()
        if skills_section:
            executor.config.system_prompt = executor.config.system_prompt + "\n\n" + skills_section

        task_id = executor.run(agent_prompt)

        max_poll_count = (agent_config.timeout_seconds + 60) // 5

        for poll_count in range(max_poll_count):
            await asyncio.sleep(5)

            result = get_background_task_result(task_id)
            if result is None:
                continue

            if result.status == SubagentStatus.COMPLETED:
                context = result.result or ""
                results.append({"agent": agent_name, "result": result.result, "status": "completed"})
                writer({"type": "team_agent_completed", "agent_name": agent_name, "result": result.result})
                logger.info(f"[trace={trace_id}] 智能体 {agent_name} 已完成")
                break
            elif result.status == SubagentStatus.FAILED:
                error_msg = result.error or "未知错误"
                results.append({"agent": agent_name, "error": error_msg, "status": "failed"})
                writer({"type": "team_agent_failed", "agent_name": agent_name, "error": error_msg})
                return f"错误：团队成员 '{agent_name}' 执行失败：{error_msg}"
            elif result.status == SubagentStatus.TIMED_OUT:
                results.append({"agent": agent_name, "error": "超时", "status": "timed_out"})
                writer({"type": "team_agent_timed_out", "agent_name": agent_name})
                return f"错误：团队成员 '{agent_name}' 执行超时"
        else:
            results.append({"agent": agent_name, "error": "等待结果超时", "status": "timed_out"})
            return f"错误：等待智能体 '{agent_name}' 结果超时"

        cleanup_background_task(task_id)

    final_result = context if context else "团队任务已完成，无输出"
    writer({"type": "team_completed", "team_name": team_name, "final_result": final_result})

    summary = f"## 团队 '{team_name}' 执行摘要\n\n"
    for r in results:
        summary += f"- **{r['agent']}**：{r['status']}\n"

    summary += f"\n---\n\n{final_result}"

    return summary
