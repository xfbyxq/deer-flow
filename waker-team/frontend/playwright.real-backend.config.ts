import { defineConfig } from '@playwright/test';

/**
 * Playwright config for real-backend E2E tests —— 主测试入口（pnpm test:e2e）。
 *
 * 这些测试连接真实后端 API（localhost:8000，经由 Vite 代理或直连），
 * 不做任何 mock 拦截：所有 CRUD / 运行 / 调度都落到真实 SQLite 与 DeerFlow 网关。
 *
 * 之所以以真实后端为主入口：mock E2E 只能验证“前端逻辑自洽”，无法发现契约不一致、
 * 持久化丢失、时间格式错误、接线错误等真实 bug（参见本仓库 2026-09 排查记录）。
 *
 * 服务启动由 webServer 自动完成（已运行的实例会被复用）。
 */
export default defineConfig({
  testDir: './tests/e2e-real-backend',
  timeout: 60000,         // Real API calls are slower than mock
  retries: 1,
  workers: 1,             // Single worker — tests share database state
  globalSetup: './tests/e2e-real-backend/setup.ts',
  globalTeardown: './tests/e2e-real-backend/teardown.ts',
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  webServer: [
    {
      // 真实后端（waker-team FastAPI，真实 SQLite + DeerFlow 网关）
      command:
        'bash -c "cd .. && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"',
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: true,
      timeout: 60000,
    },
    {
      command: 'pnpm dev',
      port: 5173,
      reuseExistingServer: true,
      timeout: 60000,
    },
  ],
  projects: [
    { name: 'real-backend', use: { browserName: 'chromium', channel: 'chrome' } },
  ],
});
