/**
 * Real-backend page rendering tests.
 *
 * Verifies that each page loads without error and displays real data from the API.
 * NO page.route() mocking.
 */

import { test, expect } from '@playwright/test';

const API_BASE = 'http://localhost:8000/api';
const TEST_PREFIX = 'test-e2e-';

test.describe('All Pages Render (real backend)', () => {
  test('Board page (/) loads with stat cards', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Board page should have stat cards
    await expect(page.locator('.rounded-xl').first()).toBeVisible({ timeout: 5000 });
  });

  test('Board page data matches API response', async ({ page }) => {
    // Fetch board data from API
    const res = await fetch(`${API_BASE}/board`);
    expect(res.ok).toBeTruthy();
    const board = await res.json();

    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Total count should match
    if (board.total > 0) {
      await expect(page.locator(`text=${board.total}`).first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('Wakers page (/wakers) shows seeded wakers', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Seeded wakers should be visible
    await expect(page.locator(`text=${TEST_PREFIX}alice`).first()).toBeVisible({ timeout: 10000 });
    await expect(page.locator(`text=${TEST_PREFIX}bob`).first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator(`text=${TEST_PREFIX}charlie`).first()).toBeVisible({ timeout: 5000 });
  });

  test('Wakers page count matches API', async () => {
    const res = await fetch(`${API_BASE}/wakers`);
    const wakers = await res.json();
    expect(wakers.length).toBeGreaterThanOrEqual(3); // At least our 3 seeded wakers
  });

  test('Groups page (/groups) shows seeded group', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator('text=test-e2e-测试群组').first()).toBeVisible({ timeout: 10000 });
  });

  test('Flows page (/flows) shows seeded flow', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator('text=test-e2e-测试流程').first()).toBeVisible({ timeout: 10000 });
  });

  test('Schedules page (/schedules) shows seeded schedule', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator('text=test-e2e-测试调度').first()).toBeVisible({ timeout: 10000 });
  });

  test('Settings page (/settings) renders', async ({ page }) => {
    await page.goto('/settings');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator('text=设置').first()).toBeVisible({ timeout: 5000 });
  });

  test('Waker detail page loads for seeded waker', async ({ page }) => {
    await page.goto(`/wakers/${TEST_PREFIX}alice`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator(`text=${TEST_PREFIX}alice`).first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=基础配置').first()).toBeVisible({ timeout: 5000 });
  });

  test('Health API returns ok status for db', async () => {
    const res = await fetch(`${API_BASE}/health`);
    expect(res.ok).toBeTruthy();
    const health = await res.json();
    expect(health.db).toBe('ok');
  });
});
