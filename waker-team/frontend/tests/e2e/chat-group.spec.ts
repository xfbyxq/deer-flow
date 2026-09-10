import { test, expect } from '@playwright/test';
import { mockWakerTeamAPI } from './utils/mock-api';

test.beforeEach(async ({ page }) => {
  await mockWakerTeamAPI(page);
});

test.describe('Group Chat Page', () => {
  test('navigates to /chat/group/deye and shows group name', async ({ page }) => {
    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=D-EYE').first()).toBeVisible();
  });

  test('shows "群组协作" badge', async ({ page }) => {
    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=群组协作').first()).toBeVisible();
  });

  test('message list renders with mock group messages', async ({ page }) => {
    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    // Check for message content from mock data
    await expect(page.locator('text=定制我的协作团队').first()).toBeVisible({ timeout: 5000 });
  });

  test('composer textarea is visible', async ({ page }) => {
    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('textarea[placeholder*="输入消息"]').first()).toBeVisible({ timeout: 5000 });
  });
});
