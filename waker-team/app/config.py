from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


def _default_cors_origins() -> list[str]:
    """可信前端来源白名单（CORS，CF10 扩展）.

    ``allow_origins=["*"]`` 与 ``allow_credentials=True`` 组合等效于对任意来源
    开放带凭据跨域，因此这里显式列出实际前端来源（localhost 与 127.0.0.1
    各一份）：

    - ``5173``：waker-team 前端 Vite dev server（见 frontend/vite.config.ts）；
    - ``4173``：Vite preview（构建产物预览）；
    - ``3000``：DeerFlow 前端 Next.js dev server（直连伴生服务调试时）；
    - ``2026``：DeerFlow nginx 统一入口（经反向代理访问伴生服务时使用）。

    部署到其他域名时通过环境变量 ``CORS_ORIGINS`` 覆盖，**必须是 JSON 数组**
    （pydantic-settings 对 list[str] 的要求），例如
    ``CORS_ORIGINS='["https://team.example.com"]'``；不要填入 ``"*"``。
    写成裸 URL（如 ``CORS_ORIGINS=http://x``）会被容错地包装为单元素列表；
    非法格式在启动时报带修复指引的清晰错误，而非晦涩的 pydantic 堆栈。
    """
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:2026",
        "http://127.0.0.1:2026",
    ]


class Settings(BaseSettings):
    deerflow_base_url: str = "http://127.0.0.1:2026"
    service_email: str = ""
    service_password: str = ""
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8765
    database_url: str = "sqlite+aiosqlite:///./waker_team.db"

    # CORS 白名单（禁止 "*"，见 _default_cors_origins 说明）。
    # 环境变量覆盖格式：JSON 数组字符串，如 CORS_ORIGINS='["https://x"]'。
    # NoDecode：禁用 pydantic-settings 对 list[str] 的 JSON 预解析，把原始
    # 字符串交给下方 _parse_cors_origins 校验器，从而在 import 期给出友好
    # 报错（而非裸 URL 触发的晦涩 JSON 解析崩溃，CF10）。
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=_default_cors_origins
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        """容错解析 CORS_ORIGINS（CF10：避免 import 期晦涩崩溃）.

        ``get_settings()`` 在 ``app.main`` / ``app.mcp.server`` 的 import 期执行，
        环境变量格式错误会让 uvicorn 在 import 阶段直接崩溃且堆栈难懂。
        这里：JSON 数组正常解析；裸 URL 字符串容错包装为单元素列表；
        非法格式抛带修复指引的清晰错误。
        """
        import json

        if isinstance(value, str):
            text = value.strip()
            if not text:
                # 留空（如 .env.example 的 CORS_ORIGINS=）视为未设置 → 回退默认白名单。
                return _default_cors_origins()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "CORS_ORIGINS must be a JSON array of origin strings, "
                        'e.g. CORS_ORIGINS=\'["https://your.origin"]\' '
                        f"(parse error: {exc})"
                    ) from exc
                if not isinstance(parsed, list) or not all(
                    isinstance(item, str) for item in parsed
                ):
                    raise ValueError(
                        "CORS_ORIGINS must be a JSON array of origin strings, "
                        'e.g. CORS_ORIGINS=\'["https://your.origin"]\''
                    )
                return parsed
            # 容错：裸 origin 字符串（运维常写成 CORS_ORIGINS=http://x）
            if text.startswith(("http://", "https://")):
                return [text]
            raise ValueError(
                "CORS_ORIGINS must be a JSON array of origins "
                '(e.g. \'["https://your.origin"]\') or a single http(s) origin'
            )
        return value

    # 由本服务发起的 DeerFlow run 的 LangGraph 递归预算。
    # 默认 1000 与 DeerFlow Web UI 一致（Gateway 默认仅 100，长任务中模型多次
    # 重试工具容易撞上限导致 run 报 Recursion limit reached 而丢失回复）。
    # 注意：实际生效值受 Gateway 侧 max_recursion_limit clamp，超过上限会被截断；
    # 调小此值可显著降低单 run 成本上限。
    recursion_limit: int = 1000

    # 自动 wake run 护栏：批量任务同时到达终态时会瞬时拉起大量唤醒 run，
    # 且唤醒后 agent 可能继续委派（委派链深度上界见 DelegationGuard.MAX_DEPTH）。
    # 阈值按进程计（CF18）：Gateway（app.main）与 MCP Server（app.mcp.server）
    # 各自持有独立 WakeEngine 实例与独立计数，全局上界 = 阈值 × 进程数。
    # wake_max_concurrency: 后台投递 worker 池规模 = 同时在途 wake run 创建数
    #   上限（CF3 之后并发由 worker 池实现，限速跨 worker 统一）。
    # wake_max_per_minute: 滑动窗口（60s）内允许投递的 wake run 数上限。
    # wake_throttle_wait_timeout_seconds: 触发限速后的最长等待；超时后请求
    #   重新入队延后重试（本地背压，不改写任务终态）。
    wake_max_concurrency: int = 4
    wake_max_per_minute: int = 30
    wake_throttle_wait_timeout_seconds: float = 60.0

    # CF2：SyncEngine 瞬时错误（网关不可达/写锁等）的连续重试预算（轮数）。
    # 默认 30 轮 ≈ 5min @10s interval；超阈后任务置 failed
    # （result_summary="Sync error: upstream unreachable"），避免网关长期
    # 不可达时任务永久停在 running。成功同步一次即清零。
    sync_max_transient_failures: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
