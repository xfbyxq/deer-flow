# Waker Team 伴生服务 P0 实施计划

> 状态：草稿 v0.2（对齐 [deerflow-waker-team-architecture.md](deerflow-waker-team-architecture.md) v0.2 评审修订结论）
> 日期：2026-09-09
> 上游依据：架构文档 §4.5/§4.6（MCP 鉴权与身份策略）、§5.4（sync delegate 与 timeout 边界）、§5.9（P0 功能裁剪）、§8（风险 #1/#2/#4/#7）、§9（P0 范围）、附录 A（契约表）
> 目标：P0 最小闭环可验收 —— Web UI 创建/复用 Waker → 人工派活 → 看板看到 run 状态/结果；delegate MCP 工具（sync）注册进 DeerFlow，调用者身份经 `headers_from_context` 可识别。

## 0. 执行摘要

| 项 | 内容 |
|---|---|
| 周期 | 1.5–2 周（M0 冒烟 2 天先行，风险前置；日历安排见 §9） |
| 交付物 | 独立仓库 `waker-team/`（FastAPI + SQLite/WAL + React/Vite 最小集 + Docker Compose 开发编排）；`extensions_config.json` 注册伴生服务 HTTP MCP server |
| 前置依赖 | DeerFlow 运行中（Gateway 可达）；`agents_api.enabled: true`（已启用）；服务账号已创建（§4.6 已定案，M0 验证程序化登录细节） |
| 已定决策（无需再确认） | 认证 = **服务账号**（§4.6，auth off 仅本地开发）；async 委派 = 引擎自建 + 唤醒 run（**P1**，P0 不实现）；MCP 授权 = 组内可见 + 敏感工具 `tool_groups`/Guardrail 兜底（§5.9 机制①，guardrail 按 agent 过滤已核实不支持，**不再冒烟**） |
| 里程碑 | M0 契约冒烟（决策回填）→ M1 骨架+客户端 → M2 Waker 管理 → M3 派活+看板 → M4 delegate MCP → P0 验收 |

**P0 功能裁剪**（架构 §5.9）：A1-A2（创建向导含 SOUL 协作规则模板注入、编辑/启停/删除）、C1-C4（人工派活/简易看板/任务操作中重试+取消/状态同步引擎）、E1（sync delegate：注册 + 调用者身份识别 + 超时降级）。不做：群组（统一走 default 组语义）、WakerFlow、调度 cron、async delegate（P1）、9-Tab 详情页（档案/能力两 Tab 够用）、试跑对话页（验收经 DeerFlow Web UI 触发员工 run）。

**验收员工（已定，S7 实测结论）**：现有 4 名 A 股员工（config.yaml `subagents.custom_agents`）是 **lead agent 委派子智能体角色池**，不在 agents_api 视角，无法直接作 Waker 执行者（S7 已实测：名单仅 `zww`）。验收执行者 = **M2 向导创建的 Custom Agent 版 A 股员工**（沿用 data-collector / market-analyst / stock-researcher / report-writer 同名，system_prompt 平移为 SOUL 雏形 + 协作规则模板，与 subagent 池同名共存无冲突）；向导创建/删除流程另用临时员工 `waker-smoke` 验证。详见 `m0-smoke/m0-decisions.md`。

## 1. M0 —— DeerFlow 契约冒烟（2 天，开发前必须完成）

目标：把架构 v0.2 中剩余"P0 冒烟验证项"全部实测，产出**决策回填表**（更新架构文档对应小节）。冒烟方式：`waker-team/scripts/smoke_deerflow.py`（httpx 脚本，逐项输出 PASS/FAIL + 证据），每项失败不阻塞其他项，全部完成后统一回填。

| # | 冒烟项 | 验证方法 | 判定标准（失败 → 回退） | 回填点 |
|---|--------|---------|-------------------------|--------|
| S1 | 服务账号程序化登录（§4.6 定案落地） | 服务账号邮箱+密码换 session cookie → 带 cookie 调 `GET /api/auth/me` → 对 agents/threads/runs 各执行一次读写（建后即删） | 登录成功；摸清 state-changing 路由对 CSRF 头的要求与豁免面；确认普通 user 角色即可用 agents/threads/runs（若需 admin 则升级账号角色并记录）；会话有效期与过期后 401 表现（→ 失败回退：auth off 仅限本地开发，不交付） | §4.6/附录 A |
| S2 | sync delegate 可用时长（`tool_call_timeout` 边界） | stub MCP 端点挂起 10s/30s/60s/90s/120s/300s，员工 run 内调用观测实际超时点 | 确认 `tool_call_timeout` 实际生效与超时错误形态；锁定 §4.5 该 server 配置值（草案 120s）与 sync 上限（默认取 ≤60s 秒级任务）；超时后工具报错对模型可读（→ 回退：sync 上限 = tool_call_timeout − 安全余量） | §4.5/§5.4/风险 #2 |
| S3 | MCP server 注册与工具可见性 | `extensions_config.json` 注册 stub HTTP MCP server（普通工具，**非** task_toolsets）→ 观测热加载生效；前端 MCP 设置页/工具列表可见；员工 run 内 `tool_search` 可发现 | 热加载生效且工具可发现；`delegate_to_agent`/`query_group`/`notify_human` 命名与内置及现有 MCP 工具无冲突；工具变更不影响进行中 run（→ 回退：命名加前缀 + Gateway 重启窗口） | §4.5/§4.7 层 0/附录 A |
| S4 | run 终态语义 | `POST /runs` + `GET /runs/{id}`，覆盖：正常完成/LLM 错误/cancel/超时 | 明确各终态的 status 值、error 字段结构、结果/artifact 读取方式；同 thread 串行排队表现（→ 状态引擎按实测映射） | §5.8 状态同步引擎 |
| S5 | Idempotency-Key 行为 | 同 key 重复 POST /runs 两次 | 第二次返回首次 run 而非重跑；确认头名/位置与冲突错误码（→ 回退：引擎侧"同 thread 有 running 即复用"防重） | §5.8 幂等重发 |
| S6 | `context.secrets → headers_from_context`（鉴权地基） | run 请求带 `config.context.secrets`，stub MCP 端点打印收到的 header；再验证缺 secret 调用（`on_missing: deny`） | header 正确注入；缺失时工具调用被 deny 且报错可读；映射 header 与静态 header 的优先级符合文档（→ 回退：静态 header token + 组内约定，需同步修订 §4.5 主案） | §3.1.4/§4.5 |
| S7 | 现有员工可见性与枚举数据源（**已实测**） | ~~探测 agents_api 名单是否含 4 员工~~ → 实测结论：**仅 `zww`，4 员工为 subagent 池角色不在 agents_api**（`lead_agent/agent.py:911` load_agent_config 仅读 agents_api 存储）；models/skills 枚举端点已锁定；tool_groups **无枚举端点**（404） | 验收员工改为 Custom Agent 版（同名创建）；向导工具组选项从 config 侧读/静态源（→ 回填点不变） | §4.1.1/§12 #2 |
| S8 | Team Briefing 压缩风险 | 同 thread 连发 10+ 轮后查 state，首条 briefing 是否仍在上下文 | 结论 + 是否需要降级（briefing 快照写 group 共享文件、按需读取）（→ 影响 §5.4.2 注入格式定型） | §5.4.2 |

**M0 产出**：`scripts/smoke_deerflow.py` + `docs/m0-decisions.md`（每项结论与代码证据）→ 回填架构文档后进入 M1。S1/S6/S7 三项是 M1/M2 的硬前置，S2/S4/S5 是 M3/M4 的硬前置。

## 2. M1 —— 服务骨架 + DeerFlow 客户端（2 天）

| 任务 | TDD 步骤 | 验证 |
|------|---------|------|
| T1.1 仓库骨架（pyproject/app 包/配置加载 `.env`：DEERFLOW_BASE_URL、服务账号凭据、MCP 端口；SQLite 开 WAL + busy_timeout） | 测试：配置解析（凭据仅从环境变量读，不入库不落日志） | `uv run pytest tests/test_config.py` |
| T1.2 DeerFlow 客户端封装（agents/threads/runs 方法 + **服务账号会话管理**：登录/cookie+CSRF 头注入/401 自动重登，按 S1 实测结论：cookie jar 维持 access_token、写请求回读 csrf_token 注入 `X-CSRF-Token`、不发 Origin 头、401 重登一次重试、会话 7 天）+ 统一错误映射 | 测试：httpx MockTransport 断言 URL/头/错误映射/重登时序 | `uv run pytest tests/test_deerflow_client.py` |
| T1.3 健康检查 `GET /api/health`（DeerFlow 连通性 + 会话有效性探测） | 测试：DeerFlow 不可达/会话失效 → degraded | curl 本地验证 |
| T1.4 基础 DB（SQLAlchemy：WAKER + TASK 两表最小集 + init 迁移） | 测试：建表/读写/迁移可重入 | `uv run pytest tests/test_db.py` |

## 3. M2 —— Waker 管理（2 天）

| 任务 | TDD 步骤 | 验证 |
|------|---------|------|
| T2.1 `GET/POST /api/wakers`（代理 agents_api + WAKER 本地台账；name 校验 `^[A-Za-z0-9-]+$`；POST 时同步 S7 确认的枚举数据源；**附"从 subagent 池平移"创建辅助**：读 config.yaml `subagents.custom_agents` 条目 → system_prompt 转 SOUL 雏形预填向导） | 测试：创建成功 → DeerFlow 侧 agents_api 可查回；name 非法 422 | `uv run pytest tests/test_wakers.py` |
| T2.2 创建时注入 SOUL 协作规则模板（§5.4.2 感知层①落地；模板常量：我是谁/协作边界/何时可委派/委派指令写法/红线，含"组共享记忆"边界说明） | 测试：POST 后读回 agents SOUL 含模板片段；对已有员工 PUT 编辑可补注模板 | 同上 |
| T2.3 `PUT/DELETE /api/wakers/{name}`（启停字段本地管理；删除前检查 TASK 引用与进行中 run） | 测试：有进行中任务时删除 409；停用后不可被派活 | 同上 |
| T2.4 前端：员工列表页 + 创建/编辑向导（表单字段 = §4.1.1 映射表；SOUL 文本域 + 模板预览） | 手工验证 | `pnpm dev` 页面走查 |

## 4. M3 —— 人工派活 + 看板（3 天）

| 任务 | TDD 步骤 | 验证 |
|------|---------|------|
| T3.1 `POST /api/tasks`（执行者存在且 enabled 校验；组装 Team Briefing → input 首条消息：任务上下文 + 可用协作 Waker 快照，格式按 S8 结论） | 测试：input 首条含 briefing 标记；执行者不存在/停用 404/409 | `uv run pytest tests/test_tasks.py` |
| T3.2 run 启动与幂等（Idempotency-Key = task_id；thread = task_id 派生确定性 UUID；`configurable.agent_name` 注入） | 测试：同 task 重发只建一个 run（按 S5 结论） | 同上 |
| T3.3 状态同步引擎（后台任务轮询 `GET /runs/{id}` 10s → 回写 TASK.status，按 S4 终态映射；失败计数与退避） | 测试：mock run 状态序列 → 断言 TASK 迁移路径含 failed/cancelled | `uv run pytest tests/test_sync_engine.py` |
| T3.4 任务操作：retry（新 run 新幂等键）/ cancel（先 cancel DeerFlow run 再回写状态） | 测试：两操作状态迁移与并发安全 | 同上 |
| T3.5 `GET /api/board`（status counts + 列表 + 分页 + `waker=`/`status=` 筛选） | 测试：聚合正确 | `uv run pytest tests/test_board.py` |
| T3.6 前端：派活对话框 + 看板页（泳道 + 详情抽屉含输入/结果摘要/run 深链 + retry/cancel 按钮） | 手工 E2E：向 data-collector 派活 → 看板 pending→running→done | `pnpm dev` 走查 |

## 5. M4 —— delegate MCP（sync）（1.5 天）

| 任务 | TDD 步骤 | 验证 |
|------|---------|------|
| T4.1 MCP 端点（FastAPI + MCP Python SDK，仅监听 loopback/内网）：`query_group`（default 组：成员+enabled+最近状态）+ `delegate_to_agent`（sync：建 TASK(kind=delegate)+新 thread+run → 轮询回收） | 测试：直连 MCP 端点模拟调用 → 建任务并回收结果摘要 | `uv run pytest tests/test_mcp_tools.py` |
| T4.2 调用者身份识别（S6 结论落地）：解析 `headers_from_context` 注入的调用者标识 → 与 run 上下文核对；目标 Waker 必须 enabled；缺 header/伪造 → 403 且报错可读 | 测试：缺 header / 伪造 header / 目标停用 → 403/409 | 同上 |
| T4.3 sync 超时降级（按 S2 结论）：超阈值不硬错，返回 ticket + 提示"后台继续，看板可查"；TASK 保持 running 由引擎继续轮询至终态（**P0 不做唤醒 run**，唤醒属 P1 async 语义） | 测试：mock 慢 run → 返回 ticket 结构且 TASK 终态正确 | 同上 |
| T4.4 注册进 DeerFlow：`extensions_config.json` 增 `waker-team` server（`headers_from_context` + `tool_call_timeout` 按 S2/S3 结论） | 手工验证：员工 run 内可列出/调用工具（S3 结论复核）；DeerFlow 前端 MCP 页可见 | 日志 + UI |

## 6. P0 验收（1 天，端到端剧本）

**剧本 A — 员工管理向导**（验证 A1-A2）：
1. 向导创建临时员工 `waker-smoke`（含 SOUL 协作规则模板注入，读回 agents 侧确认片段生效）；
2. 编辑其能力白名单 → 停用 → 删除（删除被引用检查拦截一次后再清理重删成功）。

**剧本 B — 人工派活 + 看板**（验证 C1-C4，执行者 = Custom Agent 版员工）：
1. 看板向 Custom Agent 版 `data-collector` 派活："用 akshare 拉取 600519 近 5 日日线，保存到 stock-data 并输出数据摘要"；
2. 看板观察 pending → running → done；详情抽屉含输入/结果摘要/run 深链；取消路径另派一条并 cancel 验证。

**剧本 C — sync delegate**（验证 E1）：
1. DeerFlow Web UI 以 Custom Agent 版 `report-writer` 发起 run，指示其调用 `delegate_to_agent(target=data-collector, instruction="返回 600519 最新收盘价", sync=true)`；
2. 工具消息返回 B 的结果摘要；`report-writer` 上下文可见；看板出现 kind=delegate 任务卡并达 done。

**剧本 D — 失败路径**：停用 Custom Agent 版 `data-collector` 后派活被拒；伪造 MCP header 调用被 403（T4.2 复核）。

**验收标准（DoD）**：
- [ ] §5.9 P0 裁剪范围（A1-A2/C1-C4/E1）全部可用，无空按钮/空操作；
- [ ] M0 冒烟 S1-S8 全部有结论且已回填架构文档；
- [ ] 停 DeerFlow 时看板标记 degraded 而非假成功（健康检查覆盖）；
- [ ] 审计最小集：创建/派活/取消/委派均有记录（落库即可，无审计 UI）；
- [ ] 服务账号凭据仅存在于环境变量，代码与日志无明文。

## 7. 明确不做（P0 边界）

Group 多组、WakerFlow、调度 cron、9-Tab 详情页、async delegate（唤醒 run）、委派失控防护（§5.4.3，P1）、知识库、审计 UI（仅日志落库）——均留 P1/P2。

## 8. 风险提醒

- **S1/S6 是鉴权地基**：服务账号程序化登录或 `headers_from_context` fail-closed 任一不达预期，都直接影响 M1 客户端与 T4.2 设计；两者各有回退（auth off 仅本地 / 静态 token），但回退需同步修订架构文档 §4.5/§4.6，勿静默偏离；
- **S7 已有实测结论（不再阻塞）**：现有 4 员工不在 agents_api → 执行者改为 M2 向导创建的 Custom Agent 版员工（同名）；M2 任务范围增加"从 config.yaml `subagents.custom_agents` 平移 system_prompt → SOUL 雏形"的创建辅助；
- **extensions_config.json 改动会触发 DeerFlow MCP 配置 mtime 重载**：冒烟与开发期间集中修改、避免频繁扰动进行中 run（task_toolsets 类改动才需重启，本 P0 不使用）；
- **sync delegate 依赖真实模型行为**：剧本 C 中 `report-writer` 是否主动调用 delegate 工具取决于模型决策——必要时在指令中显式要求并给足上下文（Team Briefing/试跑消息内写明工具名与参数示例）。

## 9. 里程碑日历（工作日，含缓冲）

| 天 | 内容 |
|---|---|
| D1-D2 | M0 冒烟（顺序：S1→S7→S6 优先，S2/S4/S5/S8 随后；S3 与 T4.4 共用 stub） |
| D3-D4 | M1 骨架 + 客户端（依赖 S1/S6 结论） |
| D5-D6 | M2 Waker 管理（依赖 S7 结论） |
| D7-D8 | M3 派活 + 看板后端与 UI（依赖 S2/S4/S5） |
| D9-D10 | M4 delegate MCP + 注册联调（依赖 S2/S3/S6） |
| D10-D11 | P0 验收剧本 A-D + 回填架构文档 + 计划收尾 |
