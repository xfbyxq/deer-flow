import { test, expect } from '@playwright/test';
import { mockWakerTeamAPI } from './utils/mock-api';

test.beforeEach(async ({ page }) => {
  await mockWakerTeamAPI(page);
});

test.describe('Config Pages', () => {
  test('WakerDetailPage (/wakers/data-collector) renders with sections', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=data-collector').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=基础配置').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=技能 Skill').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=工具组').first()).toBeVisible({ timeout: 5000 });
  });

  test('Skill picker modal opens on button click', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    // Click the skill picker button
    await page.locator('button:has-text("从技能市场添加")').first().click();
    // Modal should appear - check for modal overlay or dialog
    await expect(page.locator('text=技能市场').first()).toBeVisible({ timeout: 5000 });
  });

  test('GroupConfigPage (/groups/deye/config) renders with sections', async ({ page }) => {
    await page.goto('/groups/deye/config');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=D-EYE').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=基础配置').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=成员管理').first()).toBeVisible({ timeout: 5000 });
  });

  test('SettingsPage has model selector, compact toggle, notification toggles', async ({ page }) => {
    await page.goto('/settings');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=模型').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=紧凑模式').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=通知').first()).toBeVisible({ timeout: 5000 });
  });
});
