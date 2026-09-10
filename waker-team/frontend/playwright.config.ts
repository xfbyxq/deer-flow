import { defineConfig } from '@playwright/test';

/**
 * Mock E2E config（pnpm test:e2e:mock）。
 *
 * 【项目规则】有真实环境时禁止使用 mock 数据：真实后端可达时必须用
 * pnpm test:e2e（real-backend）。本套件仅在没有真实环境时使用，
 * 通过 page.route 拦截 /api/** 只覆盖前端渲染/交互逻辑，
 * 无法发现前后端契约、持久化、时间格式、服务接线类真实 bug，
 * 其结论不得作为发布依据。
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  retries: 1,
  workers: 2,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'pnpm dev',
    port: 5173,
    reuseExistingServer: true,
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium', channel: 'chrome' } },
  ],
});
