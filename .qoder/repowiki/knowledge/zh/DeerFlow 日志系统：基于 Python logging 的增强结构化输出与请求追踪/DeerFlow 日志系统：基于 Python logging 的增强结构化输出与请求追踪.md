---
kind: logging_system
name: DeerFlow 日志系统：基于 Python logging 的增强结构化输出与请求追踪
category: logging_system
scope:
    - '**'
source_files:
    - backend/packages/harness/deerflow/logging_config.py
    - backend/packages/harness/deerflow/config/app_config.py
    - backend/app/gateway/app.py
    - backend/app/gateway/trace_middleware.py
    - backend/tests/test_logging_config.py
---

## 1. 使用的系统与框架
- 核心依赖：Python 标准库 `logging`，未引入 loguru / structlog 等第三方日志框架。
- 增强能力：通过自定义 Filter + Formatter 在每条记录中注入当前请求的 trace_id，并支持 text/json 两种增强格式。
- 配置来源：`config.yaml` 中的 `log_level` 与 `logging.enhance.*` 字段驱动日志级别与增强开关。

## 2. 关键文件与包
- `backend/packages/harness/deerflow/logging_config.py` — TraceContextFilter、JsonTraceFormatter、TraceTextFormatter、configure_logging 入口。
- `backend/packages/harness/deerflow/config/app_config.py` — LoggingConfig/LoggingEnhanceConfig 模型、apply_logging_level、is_trace_correlation_enabled。
- `backend/app/gateway/app.py` — FastAPI lifespan 中调用 configure_logging(startup_config) 完成启动期初始化。
- `backend/app/gateway/trace_middleware.py` — TraceMiddleware 绑定 request-level trace id 并通过 X-Trace-Id 响应头回写。
- `backend/tests/test_logging_config.py` — 验证增强模式下 trace_id 注入行为。
- `backend/debug.py` — 开发调试入口，独立 basicConfig 后复用 apply_logging_level。

## 3. 架构与设计约定
- 启动期一次性配置：Gateway lifespan 加载 AppConfig 后调用 configure_logging，仅在此时安装/移除 trace filter 与 formatter；运行时修改 `logging.enhance.enabled` 不会热生效（reload_boundary 将其标记为 restart-required）。
- 根处理器保护：_ensure_root_handler 保证 root 至少有一个 StreamHandler，避免外部代码覆盖导致丢失输出。
- 级别隔离策略：apply_logging_level 只设置 `deerflow` 和 `app` 两个 logger 层级，以及“降低”root handler level，不主动提升第三方库（uvicorn/sqlalchemy 等）的阈值。
- 追踪上下文：TraceMiddleware 从请求头读取/生成 trace id，放入 ContextVar；TraceContextFilter 在格式化前将 `record.trace_id` 写入 LogRecord；JsonTraceFormatter 额外输出 timestamp/logger/level/exc_info/stack_info 等结构化字段。
- 双格式支持：enhance.format=text 时在文本行追加 `[trace_id=...]`；format=json 时整条记录以 JSON 对象输出，便于 ELK/Loki 解析。
- 异步队列透传：memory queue/updater 在入队/出队时显式传递 deerflow_trace_id，并在消费端用 request_trace_context 恢复上下文，确保后台任务也能带上 trace_id。

## 4. 开发者应遵循的规则
- 获取 logger：统一使用 `logger = logging.getLogger(__name__)`，不要直接操作 root logger。
- 日志级别：通过 `config.yaml` 的 `log_level` 控制，不要在业务代码里硬编码 setLevel；需要临时调低可在 debug 脚本或测试夹具中设置。
- 结构化字段：不要手动拼 message 字符串塞入 trace_id；让 TraceContextFilter 自动注入。如需额外字段，优先扩展 JsonTraceFormatter 或在上层中间件/工具中打点。
- 增强模式开关：仅在启动期通过 AppConfig.logging.enhance.enabled 启用；运行时修改不会热切换 formatter/filter，需重启进程。
- 异常堆栈：使用 `logger.exception(...)` 而非 `logger.error(..., exc_info=True)`，保持语义一致。
- 第三方库日志：不要直接改 uvicorn/sqlalchemy 等模块的 logger level；通过 `log_level` 调整 deerflow/app 层级即可。
- 后台任务追踪：跨线程/队列的任务应在提交时携带 deerflow_trace_id，并在执行处用 `request_trace_context(trace_id)` 包裹，以便 TraceContextFilter 正确注入。
- 测试断言：参考 test_logging_config.py，在增强模式下断言输出包含 `[trace_id=...]` 或 JSON 字段 trace_id。