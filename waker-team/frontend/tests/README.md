# WakerTeam 前端测试指南

## 核心规则（强制）

**有真实环境时，禁止使用 mock 数据。** 只要真实后端（8000）/前端（5173）/DeerFlow 网关可达，
测试与验证必须走真实系统（真实 API、真实数据库、真实 Agent run），不得用 mock 结果替代任何
契约、持久化或行为验证。Mock 仅允许在真实环境不可用的场景（无后端的纯前端渲染 smoke）中使用，
且其结论不得作为发布依据。

## 测试分层

| 命令 | 套件 | 定位 |
| --- | --- | --- |
| `pnpm test:e2e`（= `test:e2e:real`） | `tests/e2e-real-backend/` | **默认主入口**：真实后端（localhost:8000 真实 SQLite + 真实 DeerFlow 网关），无任何 mock |
| `pnpm test:e2e:mock` | `tests/e2e/` | 仅当无真实环境时使用：`page.route` 拦截全部 `/api/**`，只覆盖前端渲染/交互逻辑，不作契约依据 |
| `pnpm test:unit` | `tests/unit/` | vitest 单元测试 |

## 为什么以真实后端为主入口

2026-09 的深度排查显示：**mock E2E 只能验证"前端逻辑自洽"，无法发现真实 bug**。
当时 52 条 real-backend 用例全绿、mock 全绿，但系统仍有一批真实缺陷，因为 mock 与真实契约存在系统性偏差：

- mock 时间戳带 `Z`，真实后端返回 naive-UTC（`+00:00`）→ 全站时间偏移 8 小时；
- mock 对所有请求"永远成功"→ 真实的后端 422（调度 target_type）、404（运行路由）、500（外键/序列化）全被掩盖；
- mock 只改内存数组 → 真实系统中"只改前端 state 不落库"（MCP 连接器、群技能）无法暴露；
- mock 不校验服务装配 → 调度触发接线缺失（只留 pending）无法暴露。

因此：**发布前必须跑 `pnpm test:e2e`（real-backend）**；mock 套件用于快速回归前端渲染逻辑，不作为契约依据。

## real-backend 套件约定

- 服务由 Playwright `webServer` 自动拉起（已运行的 8000/5173 实例会被复用）。
- 测试数据统一使用 `test-` 前缀（waker/群组/Flow/调度）或 `[e2e]` 前缀的输入文本。
- 每个用例结束时校验 **console 无 error**（`/api/` 404 必抓；dev-server 静态资源抖动忽略）与**失败请求列表**；
- 时间戳字段统一断言**带时区后缀**（`expectIsoWithTimezone`）；
- 持久化类用例必须 **刷新后复查** + **API 复查**（double-check）；
- setup / teardown 双层清扫（API 删除 + SQLite 兜底删除 tasks/conversations/runs），
  用例运行期间创建的未登记产物也会在 teardown 被前缀清扫，杜绝残留累积。

## 如何新增一条"能发现真 bug"的用例

1. 以真实用户旅程写用例（不要只断言"页面能打开"）；
2. 断言真实契约：HTTP 状态、响应体字段、时间格式、刷新后的持久化、派生数据一致性；
3. 涉及写操作的交互，使用 `waitForResponse` 等待请求完成，避免断言竞态；
4. 结尾调用 `expectNoConsoleErrors(tracker)`；
5. 修 bug 时先补一条能复现该 bug 的用例（回归测试），再修实现。
