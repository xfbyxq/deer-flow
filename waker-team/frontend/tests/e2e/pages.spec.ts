import { test, expect } from '@playwright/test';
import { mockWakerTeamAPI } from './utils/mock-api';

test.beforeEach(async ({ page }) => {
  await mockWakerTeamAPI(page);
});

test.describe('All Pages Render', () => {
  test('Board page (/) renders with stat cards and table', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    // Board page should have stat cards
    await expect(page.locator('.rounded-xl').first()).toBeVisible({ timeout: 5000 });
    // Check for task count number (from mock: total=8)
    await expect(page.locator('text=8').first()).toBeVisible({ timeout: 5000 });
  });

  test('Wakers page (/wakers) renders with waker list', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=data-collector').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=xiaoxi').first()).toBeVisible({ timeout: 5000 });
  });

  test('Groups page (/groups) renders with group list', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=D-EYE 项目组').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=质量保障组').first()).toBeVisible({ timeout: 5000 });
  });

  test('Flows page (/flows) renders with flow list', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=每日质量报告').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=停滞单扫描流程').first()).toBeVisible({ timeout: 5000 });
  });

  test('Schedules page (/schedules) renders with schedule list', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=每日质量看板').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=Jira 异常单扫描').first()).toBeVisible({ timeout: 5000 });
  });

  test('Settings page (/settings) renders with config sections', async ({ page }) => {
    await page.goto('/settings');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=设置').first()).toBeVisible({ timeout: 5000 });
  });
});
