---
kind: configuration_system
name: DeerFlow 配置系统：YAML + Pydantic 分层加载与热重载
category: configuration_system
scope:
    - '**'
source_files:
    - backend/packages/harness/deerflow/config/app_config.py
    - config.example.yaml
    - backend/docs/CONFIGURATION.md
    - scripts/config-upgrade.sh
    - backend/app/gateway/config.py
---

## 1. 系统概览

DeerFlow 采用 **单一 YAML 配置文件 + Pydantic 强类型模型** 的配置体系，核心由 `backend/packages/harness/deerflow/config/app_config.py` 中的 `AppConfig` 驱动。所有运行时开关（模型、工具、沙箱、中间件、数据库、调度器、技能等）均通过一份 `config.yaml` 声明式管理，并通过环境变量 `$VAR` 引用注入敏感值。

## 2. 关键文件与包

- **配置入口与主模型**
  - `backend/packages/harness/deerflow/config/app_config.py` — `AppConfig` 根模型、路径解析、版本检查、环境变量展开、单例缓存与热重载
  - `backend/packages/harness/deerflow/config/` — 按子系统拆分的子配置模块（`model_config.py`、`sandbox_config.py`、`tool_config.py`、`skills_config.py`、`auth_config.py`、`database_config.py`、`scheduler_config.py`、`tracing_config.py`、`memory_config.py`、`guardrails_config.py`、`loop_detection_config.py`、`read_before_write_config.py`、`safety_finish_reason_config.py`、`token_budget_config.py`、`token_usage_config.py`、`title_config.py`、`summarization_config.py`、`subagents_config.py`、`stream_bridge_config.py`、`checkpointer_config.py`、`run_events_config.py`、`extensions_config.py`、`channel_connections_config.py`、`skill_evolution_config.py`、`tool_output_config.py`、`tool_search_config.py`、`agents_api_config.py`、`acp_config.py`、`reload_boundary.py`、`runtime_paths.py`、`paths.py`）
- **示例与文档**
  - `config.example.yaml` — 完整可运行的配置模板（含注释），带 `config_version: 18`
  - `backend/docs/CONFIGURATION.md` — 配置指南与安全说明
- **升级脚本**
  - `scripts/config-upgrade.sh` — 基于 `config_version` 的增量迁移与字段合并
- **Gateway 独立配置**
  - `backend/app/gateway/config.py` — FastAPI Gateway 自身的 `GatewayConfig`（host/port/docs），通过 `GATEWAY_*` 环境变量覆盖

## 3. 架构与设计约定

### 3.1 配置发现优先级
`AppConfig.resolve_config_path()` 严格按以下顺序定位 `config.yaml`：
1. 代码传入的 `config_path` 参数
2. 环境变量 `DEER_FLOW_CONFIG_PATH`
3. `DEER_FLOW_PROJECT_ROOT` 下的 `config.yaml`（若未设置则回退到当前工作目录）
4. 兼容旧 monorepo 位置的 `backend/config.yaml` / 仓库根 `config.yaml`

找不到任何文件时抛出 `FileNotFoundError`。

### 3.2 环境变量注入
`resolve_env_variables()` 递归遍历配置树，将形如 `$OPENAI_API_KEY` 的字符串替换为对应环境变量值；缺失变量会直接抛错，避免静默使用空值。

### 3.3 版本化与自动升级
- 每个 `config.yaml` 顶部有 `config_version` 字段，启动时与同目录 `config.example.yaml` 的版本比较并告警。
- `make config-upgrade` 调用 `scripts/config-upgrade.sh`，执行：
  - 按版本区间运行文本级迁移（例如 `src.*` → `deerflow.*`）
  - 递归合并示例中新增字段（保留用户已有值）
  - 生成 `.yaml.bak` 备份后写回

### 3.4 模块化子配置
`AppConfig` 聚合了 30+ 个 Pydantic 子模型，每个子模块负责一个领域（模型、工具、沙箱、鉴权、数据库、事件存储、调度器、记忆、Guardrail、Loop 检测、Read-before-Write、安全终止拦截、Token 预算/用量、标题生成、摘要、子 Agent、流桥、Checkpointer、Run Events、Extensions、Channel Connections、Skill Evolution、Tool Output/搜索、Agents API、ACP Agents）。这种“一域一文件”的结构使配置 schema 随功能扩展而自然增长。

### 3.5 单例缓存与热重载
- `get_app_config()` 返回进程内全局缓存的 `AppConfig` 实例，并在底层文件 mtime 或内容 SHA256 签名变化时自动重新加载。
- 提供 `reload_app_config()`、`reset_app_config()`、`set_app_config()` 以及基于 `ContextVar` 的 `push/pop_current_app_config()` 栈式覆盖，用于测试与运行时切换。

### 3.6 名称索引优化
在 `_build_name_indexes` 验证阶段构建 `models_by_name`、`tools_by_name`、`tool_groups_by_name` 字典，使 `get_model_config` / `get_tool_config` / `get_tool_group_config` 从 O(n) 扫描降为 O(1)。

### 3.7 Gateway 独立配置
`backend/app/gateway/config.py` 中的 `GatewayConfig` 仅控制网关监听地址、端口与 OpenAPI 文档开关，通过 `GATEWAY_HOST` / `GATEWAY_PORT` / `GATEWAY_ENABLE_DOCS` 环境变量覆盖，与 DeerFlow 应用配置解耦。

## 4. 开发者规范

- **新增配置项**：在 `config.example.yaml` 中添加注释化的示例条目，并在 `backend/packages/harness/deerflow/config/` 下创建对应的 Pydantic 子配置类，然后在 `AppConfig` 中引用。
- **环境变量引用**：统一使用 `$VAR_NAME` 语法，不要硬编码密钥；缺失变量会在加载时报错以便快速发现。
- **版本升级**：修改配置 schema 时递增 `config.example.yaml` 的 `config_version`，并在 `scripts/config-upgrade.sh` 的 `MIGRATIONS` 字典中编写文本替换规则。
- **默认值策略**：对可选段使用 `default_factory=...`，对必填段保持无默认值以在启动期报错；对可能为 null 的整段（被注释掉后 PyYAML 解析为 None）依赖 `_drop_null_config_sections` 回落到默认。
- **热重载**：生产环境应避免频繁修改 `config.yaml`；如需动态调整，优先使用支持运行时更新的子系统（如 Guardrail、Loop Detection 等通过 `load_xxx_config_from_dict` 暴露的模块）。
- **Gateway 配置**：网关自身行为通过 `GATEWAY_*` 环境变量控制，不要混入 DeerFlow 应用配置。
