import { test, expect } from '@playwright/test';
import { mockWakerTeamAPI } from './utils/mock-api';

test.beforeEach(async ({ page }) => {
  await mockWakerTeamAPI(page);
});

test.describe('Direct Chat Page', () => {
  test('navigates to /chat/direct/xiaoxi and shows waker name', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=xiaoxi').first()).toBeVisible();
  });

  test('shows "1v1 私聊" badge', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=1v1 私聊').first()).toBeVisible();
  });

  test('message list renders with mock messages', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    // Check for message content from mock data
    await expect(page.locator('text=完善一下小析').first()).toBeVisible({ timeout: 5000 });
  });

  test('user messages appear right-aligned', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    // User messages use flex justify-end
    const userMsg = page.locator('div.flex.justify-end').first();
    await expect(userMsg).toBeVisible({ timeout: 5000 });
  });

  test('waker messages appear with content', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    // Waker messages have direct-immersive class
    const wakerMsg = page.locator('div.direct-immersive').first();
    await expect(wakerMsg).toBeVisible({ timeout: 5000 });
  });

  test('composer textarea is visible', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('textarea[placeholder*="输入消息"]').first()).toBeVisible({ timeout: 5000 });
  });

  test('typing and sending a message adds it to the list', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const textarea = page.locator('textarea[placeholder*="输入消息"]').first();
    await expect(textarea).toBeVisible({ timeout: 5000 });

    await textarea.fill('你好，请帮我分析一下数据');

    // Click send button (title="发送")
    await page.locator('button[title="发送"]').first().click();

    // The message should appear in the list
    await expect(page.locator('text=你好，请帮我分析一下数据').first()).toBeVisible({ timeout: 5000 });
  });
});
