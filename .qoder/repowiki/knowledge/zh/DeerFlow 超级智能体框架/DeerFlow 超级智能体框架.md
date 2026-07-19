---
kind: external_dependency
name: DeerFlow 超级智能体框架
slug: deer-flow
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
source_files:
    - backend/pyproject.toml
    - config.yaml
    - backend/packages/harness/deerflow/runtime/user_context.py
---

### DeerFlow 超级智能体框架
- DeerFlow 是一个基于 LangGraph 和 LangChain 的开源超级智能体框架，支持子智能体编排、记忆系统、沙箱执行和可扩展技能
- 核心特性包括：会话目标管理、多智能体协作、文件系统访问、上下文工程、长期记忆等
- 支持多种部署模式：本地开发、Docker 开发/生产、Kubernetes 集群
- 内置 ACP（Agent Client Protocol）支持，可调用外部独立智能体进程
- 提供 Web UI、TUI 终端界面和 Python 嵌入式客户端三种使用方式
- 配置通过 config.yaml 管理，支持动态热重载
- 认证和用户隔离通过 ContextVar 实现，默认用户 ID 为 "default"