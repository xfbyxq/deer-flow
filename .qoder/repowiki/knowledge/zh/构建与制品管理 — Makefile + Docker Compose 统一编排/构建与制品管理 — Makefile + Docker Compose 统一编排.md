---
kind: build_system
name: 构建与制品管理 — Makefile + Docker Compose 统一编排
category: build_system
scope:
    - '**'
source_files:
    - Makefile
    - backend/Makefile
    - frontend/Makefile
    - backend/Dockerfile
    - frontend/Dockerfile
    - docker/docker-compose.yaml
    - scripts/deploy.sh
    - scripts/serve.sh
    - backend/pyproject.toml
    - frontend/package.json
---

## 体系概览

DeerFlow 以**顶层 Makefile**为统一入口，通过一组 Bash 脚本（`scripts/serve.sh`、`scripts/deploy.sh`）和 Docker Compose 文件，将后端 FastAPI Gateway、前端 Next.js、Nginx 反向代理、Redis Stream Bridge 以及可选的 Kubernetes Sandbox Provisioner 统一编排到本地开发、容器化开发与生产三种运行模式。Python 依赖由 `uv` 管理（支持 workspace），Node 依赖由 `pnpm` 管理；Docker 镜像采用多阶段构建，按 dev/runtime 分离工具链与产物。

## 关键文件与职责

- **顶层 Makefile**：暴露 `setup / doctor / install / dev / start / up / down / docker-*` 等命令，封装环境检查、依赖安装、服务启停与 Docker 生命周期。
- **backend/Makefile**：后端子任务（`dev / test / lint / format / detect-blocking-io / migrate-rev`），直接调用 `uv run uvicorn` 与 `pytest`。
- **frontend/Makefile**：前端子任务（`install / build / dev / test / test-e2e / lint / format / build-static`），基于 `pnpm` 脚本。
- **backend/Dockerfile**：三阶段镜像（builder → dev → runtime），通过 `UV_EXTRAS` 控制可选依赖，默认启用 redis extra。
- **frontend/Dockerfile**：双目标（dev / prod），使用 corepack 固定 pnpm 版本并支持 NPM_REGISTRY 镜像。
- **docker/docker-compose.yaml**：定义 nginx / frontend / gateway / redis / provisioner 服务，默认端口 2026，支持环境变量注入与 volume 挂载。
- **scripts/deploy.sh**：生产部署主脚本，自动检测 sandbox 模式（local/aio/provisioner）、生成密钥、解析 UV_EXTRAS、按需追加 `docker-compose.dood.yaml` 覆盖。
- **scripts/serve.sh**：本地开发主脚本，负责端口抢占清理、依赖同步（含 extras 探测）、启动 Gateway/Next/Nginx 并等待就绪。
- **backend/pyproject.toml**：声明项目依赖、optional-dependencies（postgres/redis/discord）、dev dependency-groups、uv workspace 成员与源码映射。
- **frontend/package.json**：声明 Next.js 脚本、依赖与 packageManager 锁定版本。

## 架构与约定

1. **分层构建**
   - Python 侧：`uv sync --all-packages` 在 workspace 内传播 optional-dependencies，确保 `deerflow-harness[redis]` 等跨包生效。
   - Node 侧：`pnpm install --frozen-lockfile` 配合 `pnpm-workspace.yaml` 保证可重复安装。
   - Docker 侧：builder 阶段保留编译工具链，runtime 阶段仅拷贝已安装 venv 与 Node 运行时，镜像体积显著减小。

2. **沙箱模式驱动部署**
   - `deploy.sh` 从 `config.yaml` 解析 `sandbox.use` 与 `sandbox.provisioner_url`，自动选择 local/aio/provisioner 模式；aio 模式额外挂载宿主机 Docker socket（需显式开启）。

3. **配置与密钥管理**
   - 首次运行自动生成 `BETTER_AUTH_SECRET` 与 `DEER_FLOW_INTERNAL_AUTH_TOKEN` 并持久化至 `$DEER_FLOW_HOME`；`extensions_config.json` 不存在时创建空模板。
   - `UV_EXTRAS` 可从 `.env` 或 `config.yaml` 自动探测并转换为逗号分隔的 build-arg 列表。

4. **端口与进程治理**
   - 本地开发固定端口：Gateway 8001、Frontend 3000、Nginx 2026。
   - `serve.sh` 通过 `lsof`/`ss`/`netstat` 检测端口占用，并结合 git worktree 根路径精准回收同仓库进程，避免误杀外部服务。

5. **测试与质量门禁**
   - 后端：`pytest tests/` 为主，`tests/blocking_io/` 配合 Blockbuster 做阻塞 IO 静态扫描；`make detect-blocking-io` 输出 JSON 报告。
   - 前端：`rstest` 单元测试 + Playwright E2E（`test:e2e`）。
   - Lint/Format：Ruff（Python）+ ESLint/Prettier（TypeScript/JSX）。

## 开发者应遵循的规则

- **统一入口**：优先使用 `make setup`、`make dev`、`make up`、`make down`，不要直接调用底层 `uvicorn`/`next dev`/`docker compose`。
- **依赖变更**：新增 Python 可选功能请添加到 `backend/pyproject.toml` 的 `[project.optional-dependencies]` 并在 `detect_uv_extras.py` 白名单中注册；新增 Node 包请在 `frontend/package.json` 中声明。
- **镜像构建参数**：通过 `--build-arg UV_EXTRAS=...` 或 `.env` 中的 `UV_EXTRAS` 控制后端可选依赖；通过 `APT_MIRROR`、`UV_INDEX_URL`、`NPM_REGISTRY` 适配受限网络。
- **沙箱模式**：修改 `config.yaml` 的 `sandbox.*` 字段后重新执行 `make up`，脚本会自动拉起/移除 provisioner 并调整 Docker socket 挂载。
- **端口冲突**：若出现端口被占，先执行 `make stop` 再重试；跨 worktree 场景下 `serve.sh` 会提示并尝试回收。
- **密钥安全**：不要提交 `$DEER_FLOW_HOME/.better-auth-secret` 与 `.internal-auth-token`，它们由部署脚本自动生成并限制权限。
