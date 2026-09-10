# M0 冒烟决策记录（S1 + S7 先行）

> 日期：2026-09-09 ｜ 执行：`docs/companion/m0-smoke/smoke_deerflow.py`（v0.1，13/13 PASS）
> 环境：Docker 栈（nginx 2026 → Gateway）；认证开启；账号 `971762302@qq.com`（role=user）
> 回填目标：架构文档 §4.6/§4.1.1/§12 #2、P0 计划 §0/§1/§6

## S1 服务账号程序化登录 —— 结论：方案成立，封装要求已明确

| # | 验证点 | 结果 | 证据/结论 |
|---|--------|------|----------|
| S1.1 | providers 公开探测 | PASS | `GET /api/v1/auth/providers` → 200，`{"providers": []}`（纯本地账号、无 SSO，符合内网形态） |
| S1.2 | login/local | PASS | `POST /api/v1/auth/login/local`（**OAuth2 表单**：username=邮箱/password/remember_me）→ 200；响应同设 `access_token` + `csrf_token` 两个 cookie；`expires_in=604800`（**会话 7 天**）；`needs_setup=false` |
| S1.3 | me / 角色 | PASS | `GET /api/v1/auth/me` → 200；**当前 user 角色账号即可 agents/threads 全量 CRUD** → 服务账号无需 admin（§4.6 回填：普通 user 角色即可） |
| S1.4 | CSRF 负向 | PASS | 无 `X-CSRF-Token` 头的写请求 → **403** "CSRF token missing. Include X-CSRF-Token header."（double-submit 生效） |
| S1.5 | agents CRUD | PASS | `POST /api/agents` → 201（仅 name 必填）；`GET /api/agents/{name}` → 200；`DELETE` → 204 |
| S1.6 | threads CRUD | PASS | `POST /api/threads`（body `{}` 全默认）→ 200 + thread_id；读回 200；**DELETE /api/threads/{id} → 200（删除端点存在）** |

**对 DeerFlow 客户端封装（M1 T1.2）的硬要求**：
1. cookie jar 自动维持 `access_token`；每写请求从 jar 回读 `csrf_token` 注入 `X-CSRF-Token` 头（无头即 403，无豁免）；
2. 登录豁免 CSRF（`_AUTH_EXEMPT_PATHS`），且**无 Origin 头**的服务端客户端通过 auth origin 检查——封装不发 Origin 头；
3. 会话 7 天过期 → 401 后自动重登一次再重试原请求（T1.2 重登时序）；
4. agents 创建 `POST /api/agents`（非 `/{name}`），删除用 `DELETE /api/agents/{name}`。

## S7 现有员工可见性 —— 重大发现：两套 agent 体系并存

| # | 验证点 | 结果 | 结论 |
|---|--------|------|------|
| S7.1 | agents_api 名单 | **仅 1 个：`zww`** | 现有 4 名 A 股员工（config.yaml `subagents.custom_agents`）**不在 agents_api 视角** |
| S7.2 | 枚举端点 | `/api/models` ✅（含内网 SGLang 模型）、`/api/skills` ✅；`/api/models/list`、`/api/tool-groups`、`/api/tools/groups` 404 | models/skills 枚举端点锁定；**tool_groups 无枚举端点**（向导工具组选项需从 config 侧读或前端静态源） |

**两套体系（代码证据）**：
- **Custom Agent（agents_api）= Waker 的正确落点**：`lead_agent/agent.py:911` `load_agent_config(agent_name, user_id)` 从 agents_api 存储（users/{uid}/agents/{name}/）加载；有 SOUL.md、独立 run 入口（`configurable.agent_name`）；
- **`subagents.custom_agents`（config.yaml）= lead agent 委派子智能体角色池**：`task_tool.py` 说明"custom subagent types defined in config.yaml under subagents.custom_agents"，经 `deerflow.subagents.registry` 管理，**不是 assistant、agent_name 路由读不到**（load 不到 → agent_config=None，会静默当默认 lead agent 处理——比报错更危险）。

**对 P0 的影响与处理（已定建议）**：验收剧本 B/C 的执行者必须改为 **agents_api 中存在的 Custom Agent**。推荐：M2 向导阶段即创建 **Custom Agent 版 A 股员工**（沿用 `data-collector`/`market-analyst`/`stock-researcher`/`report-writer` 同名，system_prompt 平移为 SOUL 雏形 + 团队协作规则模板），与 subagent 池同名共存无冲突（两个名字空间互不相干）；P1 评估是否让 subagent 池逐步退役/由 Custom Agent 的 `allowed_subagents` 承接。

## 待办回填

- [x] 架构文档 §4.6：补充"user 角色即可 agents/threads CRUD；服务账号登录/CSRF/会话行为实测证据（S1）"；
- [x] 架构文档 §4.1.1/§12 #2：现有 A 股员工为 subagent 池角色，需经 agents_api 创建 Custom Agent 版后方可作 Waker（S7 结论）；
- [x] 架构文档附录 A：登录端点/CSRF 契约行更新；
- [x] P0 计划：S7 行判定已落地、验收剧本执行者改为 Custom Agent 版员工；
- [x] P0 计划 M1 T1.2：客户端封装要求（上述 4 条）随 S1 结论定型。

---

# M0 冒烟决策记录 第二批（S2-S6/S8，2026-09-09 晚）

> 执行：`stub_mcp_server.py` + `run_mcp_smoke.py`；真实 run 验证（模型 = 内网 Qwen3.8-Flash-Next-NVFP4）；完成后已清理（runner agent/threads 删除、extensions_config 还原、stub 停止）。
> 环境注记：冒烟期间宿主 UI 有并发大 run，gateway 日志出现一次 sqlite `disk I/O error`（UPDATE runs）与短暂 500/502——判定为环境级偶发（Docker 卷并发写），非冒烟结论污染；作为伴生服务 SQLite/WAL 健壮性设计的环境观察项记录。

## S2 HTTP transport 调用等待边界 —— 结论：sync 上限 60s 安全；tool_call_timeout 对 http 无效

| 验证 | 结果 | 结论 |
|---|---|---|
| m0_sleep(10) run | success（总耗时 16s） | 等待 >10s 正常 |
| m0_sleep(65) run | success（总耗时 76s） | 等待 ≥65s 正常（上限更高，未继续二分） |
| 配置位 | `tool_call_timeout` 对 http/SSE **被忽略**（tools.py:939 warning "configure HTTP/SSE transport-level timeouts instead"，仅 stdio 生效） | **架构 §4.5 配置片段需修正**：删去 http server 的 `tool_call_timeout` 行 |

**回填 §4.5/§5.4**：HTTP MCP 工具调用无 DeerFlow 侧超时配置位，实际等待由 SDK/传输层默认决定（实测 ≥65s）；sync delegate 上限 60s 安全；超时降级仍按 §5.4（返回 ticket）兜底。

## S3 MCP 注册/热加载/工具可见性 —— 结论：成立，含两个部署要点

| 验证 | 结果 |
|---|---|
| extensions_config.json mtime 热加载 | **生效**（touch 后 ~10s 内工具可发现可调用；无需重启） |
| 工具可见性 | 模型 run 内可见 `m0-stub_m0_echo` 并成功调用（stub 日志 CallToolRequest 200） |
| 命名 | 工具名 = `{server}_{tool}` 前缀（m0-stub_m0_echo），与现有 4 server 无冲突 |
| **部署要点 1** | stub 绑 `127.0.0.1` 时容器 `host.docker.internal` **连接拒绝** → 工具发现被静默跳过，模型回复"工具不可用"（无显式报错）——伴生服务 MCP 端点须绑 `0.0.0.0`/内网可达地址 |
| **部署要点 2** | `GET /api/mcp/config` 为 admin-only（user 角色 403）→ 伴生服务（服务账号=user）不需要该 API；热加载验证以 run 内工具可见性为准 |

## S4 run 终态语义 —— 结论：状态引擎映射依据已齐

- `GET /runs/{id}` 终态记录字段：status/stop_reason/llm_call_count/message_count/token 统计（total_input/output/lead_agent/subagent/middleware）/created_at/updated_at/assistant_id；
- 正常完成 → status=`success`；**cancel 端点（POST 202）后终态 = `interrupted`（非 cancelled）** → 回填 §5.8：引擎映射 run=interrupted → TASK=cancelled（人工取消）或 failed(interrupted)；
- LLM 错误/timeout 终态未在本批覆盖（需构造成本高），状态引擎按枚举值防御式处理（未知终态 → failed(unknown)）。

## S5 Idempotency-Key —— 结论：成立（与测试背书一致）

同 thread 同 header `Idempotency-Key` 两次 POST /runs → **复用同一 run_id**（第二次不重跑）；作用域 thread+user（test_thread_run_idempotency.py 已背书）。伴生服务 T3.2 以 Idempotency-Key=task_id 实现幂等发起的方案成立。

## S6 context.secrets → headers_from_context —— 结论：双向实证，主案成立

| 场景 | 结果 |
|---|---|
| run 带 `config.context.secrets.waker_identity` 调工具 | **成功执行**（stub 收到 CallToolRequest 200） |
| run 不带 secrets 调同一工具 | **被拒**，错误消息精确可读：`MCP server 'm0-stub' needs request-scoped credential(s) waker_identity. Send them in config.context.secrets, or set this server's headers_from_context.on_missing to 'passthrough'` |

模型侧对比亦清晰（"上一轮带凭据成功、本轮被拒"）。**§3.1.4/§4.5 信任模型主案验证成立**：调用者身份来自伴生服务写入的 secrets，缺失即 deny，无静默降级。

## S8 Team Briefing 保留性 —— 结论：P0 场景安全，无需降级

summarization.enabled=true 环境下，同 thread 首条注入 briefing 标记（B8FINGERPRINT）+ 连续 **11 轮**短对话（~24 条消息）：briefing **完整保留**且被模型**每轮持续引用**（索引 2-23 均含指纹），无摘要替换发生。压缩阈值高于该量级；P0 任务 thread（单 run/短对话）无降级需求；§5.4.2 注入格式定型（首条 input 注入 briefing）。

## M0 总结论（8/8 完成）

| 项 | 结论 |
|---|---|
| S1 服务账号登录/CSRF/权限 | PASS（user 角色即可 agents/threads CRUD；7 天会话；双提交无豁免） |
| S2 sync 等待边界 | PASS（≥65s；tool_call_timeout 仅 stdio → §4.5 修正） |
| S3 MCP 注册/可见性 | PASS（热加载；绑 0.0.0.0 要点） |
| S4 run 终态 | PASS（success/interrupted(cancel)；字段清单） |
| S5 Idempotency-Key | PASS（thread+user 作用域复用） |
| S6 secrets→headers | PASS（正向成功 + fail-closed deny，错误可读） |
| S7 员工名单/枚举 | PASS（两套体系；Custom Agent 版员工方案） |
| S8 briefing 保留 | PASS（11 轮保留且被引用） |

**架构文档待修正点（已/将同步）**：§4.5 tool_call_timeout 行删除；§5.8 interrupted 映射。M0 完成 → 可进入 M1（T1.1-T1.4 前置全部就绪）。
