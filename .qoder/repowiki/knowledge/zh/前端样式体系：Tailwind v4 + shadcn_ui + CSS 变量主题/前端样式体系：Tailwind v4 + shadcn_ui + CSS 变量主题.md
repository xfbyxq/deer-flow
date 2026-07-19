---
kind: frontend_style
name: 前端样式体系：Tailwind v4 + shadcn/ui + CSS 变量主题
category: frontend_style
scope:
    - '**'
source_files:
    - frontend/src/styles/globals.css
    - frontend/postcss.config.js
    - frontend/components.json
    - frontend/src/components/theme-provider.tsx
    - frontend/src/app/layout.tsx
    - frontend/package.json
---

## 1. 系统与方法论
- 样式框架：Tailwind CSS v4（通过 `@tailwindcss/postcss` 插件集成），采用 CSS-first 配置方式，不再依赖独立的 `tailwind.config.js`。
- UI 组件库：基于 shadcn/ui（style: "new-york"、baseColor: "neutral"、CSS Variables 模式）生成并维护在 `src/components/ui/`，配合 Radix UI 原语与 `class-variance-authority` / `clsx` / `tailwind-merge` 组合类名。
- 主题系统：使用 `next-themes` 提供亮/暗主题切换，根布局中通过 `ThemeProvider attribute="class"` 将主题状态注入到 `<html>` 的 class 上；首页强制 dark 模式。
- 动画与动效：引入 `tw-animate-css` 作为基础动画集，并在全局 CSS 中通过 `@theme` 扩展自定义 keyframes（fade-in、bouncing、skeleton-entrance、aurora、shine 等）。
- 数学公式渲染：通过 KaTeX + rehype-katex 在 Markdown 内容中渲染 LaTeX，并对 `.katex-display` 做滚动条与行高优化。
- 文档站点：Nextra + nextra-theme-docs 提供 `/docs` 文档界面，与主应用共享同一套 Tailwind 主题变量。

## 2. 关键文件与包
- 样式入口与主题变量：
  - `frontend/src/styles/globals.css` — Tailwind v4 入口、CSS 变量色板、dark 覆盖、全局 base layer、动画与工具类。
  - `frontend/postcss.config.js` — 仅启用 `@tailwindcss/postcss`。
- shadcn/ui 配置与注册表：
  - `frontend/components.json` — style/new-york、RSC/TSX、iconLibrary=lucide、aliases 指向 `@/components`、`@/components/ui` 等。
- 主题 Provider：
  - `frontend/src/components/theme-provider.tsx` — 基于 `next-themes`，首页强制 dark。
- 根布局挂载：
  - `frontend/src/app/layout.tsx` — 引入 KaTeX CSS、全局样式、ThemeProvider、I18nProvider。
- 依赖清单：
  - `frontend/package.json` — tailwindcss 4.x、@tailwindcss/postcss、tw-animate-css、shadcn/ui 生态、next-themes、lucide-react、motion、sonner 等。

## 3. 架构与约定
- 设计令牌集中化：所有颜色、圆角、字体、动画均通过 CSS 变量（`--primary`、`--background`、`--radius-*`、`--animate-*`）暴露，light/dark 两套变量在 `:root` 与 `.dark` 下分别定义，组件只消费语义化 token。
- Tailwind v4 扫描策略：通过 `@source` 与 `@source inline(...)` 显式声明需要扫描的类名片段（heading、spacing、list、text、code、blockquote、link、table、image、hr、general、shadcn colors），避免全量扫描带来的性能问题。
- 组件分层：
  - `src/components/ui/*`：原子级 shadcn 组件（Button、Card、Dialog、Tabs、Tooltip 等），遵循 `cva` + `cn` 合并类名的统一写法。
  - `src/components/workspace/*`、`src/components/ai-elements/*`、`src/components/landing/*`：业务领域组件，复用 `@/components/ui` 与 CSS 变量。
  - `src/components/docs/*`：Nextra 文档专用 UI。
- 响应式与容器：通过 `container-md` 自定义层与 `--container-width-*` CSS 变量控制内容宽度断点，结合 Tailwind 内置断点实现多屏适配。
- 国际化与主题联动：`layout.tsx` 同时注入 I18nProvider 与 ThemeProvider，确保语言与主题在页面级一致生效。

## 4. 开发者应遵守的规则
- 新增样式优先使用 Tailwind 原子类；若需重复使用的复合样式，封装为 `@layer components` 中的自定义类或提取为 `@/components/ui` 下的新组件。
- 颜色、圆角、阴影等视觉值一律通过 CSS 变量消费，禁止在组件中硬编码 hex/rgb 值；如需新色，先在 `globals.css` 的 `:root` / `.dark` 中补充变量，再在 `@theme` 中映射到 Tailwind 命名空间。
- 新增动画请在 `@theme` 中定义 `--animate-*` 与对应 `@keyframes`，并通过 `animate-*` 类引用，不要直接在组件里写内联 keyframes。
- 主题切换统一走 `next-themes`，不要在组件内直接操作 `document.documentElement.classList`。
- 使用 shadcn/ui 组件时，通过 `cva` 的 `variants` 与 `cn()` 组合类名，保持与现有 `ui/` 组件一致的 API 风格。
- 对第三方库样式（如 KaTeX、streamdown）仅在 `globals.css` 中做最小范围覆盖，避免污染全局。
- 文档站点的样式应与主应用共享同一套 CSS 变量，不得在 Nextra 主题之外另起一套色板。