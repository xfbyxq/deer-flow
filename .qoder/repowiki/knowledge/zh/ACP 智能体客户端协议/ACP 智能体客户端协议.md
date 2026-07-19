---
kind: external_dependency
name: ACP 智能体客户端协议
slug: agent-client-protocol
category: external_dependency
category_hints:
    - sdk_real_api
    - client_constraint
scope:
    - '**'
source_files:
    - backend/packages/harness/deerflow/config/acp_config.py
    - backend/packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py
---

### ACP 智能体客户端协议
- ACP 是 DeerFlow 用于调用外部独立智能体进程的协议，版本号为 `2026-03-24`
- 通过 `invoke_acp_agent` 工具让 DeerFlow 智能体能够调用 Claude Code、Codex 等外部 ACP 兼容智能体
- 每个 ACP 智能体在独立的线程工作空间中运行，路径结构为 `{base_dir}/users/{user_id}/threads/{thread_id}/acp-workspace/`
- 支持 MCP Server 透传，自动将 DeerFlow 配置的 MCP 服务器传递给 ACP 智能体
- 权限控制：默认拒绝所有权限请求，可通过 `auto_approve_permissions: true` 配置自动批准
- 标准 CLI（如 `claude`、`codex`）不直接支持 ACP，需要使用 ACP 适配器（如 `@zed-industries/claude-agent-acp`）
- 集成依赖：`acp` Python 包，通过 `uv sync` 安装项目依赖时自动获取