---
kind: dependency_management
name: 多语言依赖管理：uv + pnpm 双栈锁定与可选依赖策略
category: dependency_management
scope:
    - '**'
source_files:
    - backend/pyproject.toml
    - backend/packages/harness/pyproject.toml
    - backend/uv.lock
    - frontend/package.json
    - frontend/pnpm-lock.yaml
    - frontend/.npmrc
    - backend/.python-version
    - Makefile
---

## 系统概览

DeerFlow 采用**双包管理器 + 双锁文件**的跨语言依赖管理方案：后端使用 **uv（PEP 582/631）**，前端使用 **pnpm v9 lockfile**。两者均通过 `pyproject.toml` / `package.json` 声明版本范围，并配合精确的 `uv.lock` / `pnpm-lock.yaml` 实现可复现构建。

### Python 侧（backend）

- **工作区结构**：`backend/pyproject.toml` 定义 workspace，成员包含 `packages/harness`（核心框架包），通过 `[tool.uv.workspace]` 和 `[tool.uv.sources]` 以本地路径解析 `deerflow-harness`，避免发布到 PyPI 即可互相引用。
- **Python 版本约束**：`.python-version = 3.12` 与 `requires-python = ">=3.12"` 双重锁定最低版本；`uv.lock` 的 `resolution-markers` 覆盖 3.12–3.14、win32/non-win32 全组合，确保多平台一致性。
- **索引源**：`[tool.uv] index-url = "https://pypi.org/simple"`，未配置私有镜像或 `UV_INDEX_URL`，默认走官方 PyPI。
- **可选依赖分层**：
  - `project.optional-dependencies`：运行时可选能力（`postgres`、`redis`、`discord`）。
  - `dependency-groups.dev`：仅开发期使用的包（pytest、ruff、textual、redis 等），不污染生产安装。
  - `harness/pyproject.toml` 中进一步将 TUI、Redis 桥、Postgres 驱动拆为独立 extra，Docker 镜像按需选择。

### Node.js 侧（frontend）

- **包管理器**：`packageManager: "pnpm@10.26.2"` 强制 pnpm 版本；`pnpm-lock.yaml` 记录每个包的精确版本号及 peerDependency 解析树。
- **公共提升**：`.npmrc` 中 `public-hoist-pattern[]=*eslint*` 与 `*prettier*` 将 Lint/格式化工具提升到顶层 node_modules，减少重复安装。
- **版本策略**：依赖声明普遍使用 `^` 语义化范围（如 `next ^16.2.6`、`react ^19.0.0`），由 pnpm 在 lockfile 中固化实际解析结果。

### 编排与 CI 集成

- 根 `Makefile` 统一入口：`make install` → `cd frontend && pnpm install`；后端通过 `uv sync`（参考 `scripts/docker.sh`、`docker-compose*.yaml` 中的 uv 调用）完成工作区同步。
- Docker 构建阶段直接复用 lockfile，保证容器内依赖与本地一致。

## 关键文件

| 文件 | 作用 |
|---|---|
| `backend/pyproject.toml` | 后端主包声明、workspace 成员、dev dependency groups、uv 索引源 |
| `backend/packages/harness/pyproject.toml` | 核心框架包 deerflow-harness 的依赖与 optional extras |
| `backend/uv.lock` | Python 依赖解析快照（含多平台 resolution markers） |
| `frontend/package.json` | 前端依赖与脚本、pnpm 版本要求 |
| `frontend/pnpm-lock.yaml` | 前端依赖精确解析树 |
| `frontend/.npmrc` | pnpm public hoist 规则 |
| `backend/.python-version` | 指定 Python 3.12 |
| `Makefile` | 顶层安装入口（触发 pnpm install） |

## 架构约定与最佳实践

1. **禁止裸 `pip install`**：所有新增 Python 依赖必须写入 `pyproject.toml`（runtime）或 `dependency-groups.dev`（仅开发），然后运行 `uv lock` 更新锁文件。
2. **可选依赖优先用 extra**：对非核心运行时能力（Postgres、Redis、TUI、Ollama 等）使用 `[project.optional-dependencies]` 拆分，避免增大基础镜像体积。
3. **Workspace 内部包不发布**：`deerflow-harness` 通过 `{ workspace = true }` 引用，不在 PyPI 上发布，保持单仓内联开发。
4. **前端依赖范围使用 `^`**：允许小版本自动升级，但通过 `pnpm-lock.yaml` 锁定具体版本，CI 中比较 lockfile diff 即可发现变更。
5. **无私有注册表**：当前未配置任何私有 PyPI/NPM 镜像或 `GOPRIVATE`，全部依赖来自官方源；若引入企业私有包，需在对应工具的 config 中补充认证信息。

## 开发者应遵循的规则

- 添加 Python 依赖 → 编辑 `backend/pyproject.toml` → `uv lock` → 提交 `uv.lock`。
- 添加 Node 依赖 → `pnpm add <pkg>`（会自动更新 `package.json` 与 `pnpm-lock.yaml`）。
- 不要手动编辑 lock 文件；如需调整版本范围，修改声明文件后重新生成锁。
- 仅在 `dependency-groups.dev` 中添加测试/开发工具，勿混入 `dependencies`。
- 新增可选功能时优先创建新的 extra，并在 Dockerfile / compose 中显式安装对应标记。