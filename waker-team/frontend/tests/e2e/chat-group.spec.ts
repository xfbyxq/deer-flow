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

  test('sending switches the send button to running (stop) state; stopping restores it', async ({ page }) => {
    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const textarea = page.locator('textarea[placeholder*="输入消息"]').first();
    await expect(textarea).toBeVisible({ timeout: 5000 });
    await textarea.fill('请群里协作完成一份调研');
    await page.locator('button[title="发送"]').first().click();

    // 用户消息发出后：发送按钮变为运行中状态（实心正方形 / 停止）
    const stopBtn = page.locator('button[title="停止"]').first();
    await expect(stopBtn).toBeVisible({ timeout: 5000 });

    // 点击停止：显示「已停止」提示，按钮恢复发送态
    await stopBtn.click();
    await expect(page.locator('button[title="发送"]').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=已停止本次回复').first()).toBeVisible({ timeout: 5000 });
  });

  test('group clarification card renders; option answer closes it', async ({ page }) => {
    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // 展开「任务 / 设置」面板 → 切换到群澄清测试会话
    await page.getByRole('button', { name: /任务 \/ 设置/ }).click();
    await page.locator('button', { hasText: '群澄清测试' }).first().click();

    const card = page.locator('[data-testid="clarification-card"]');
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(card.locator('text=本次评审采用哪个版本')).toBeVisible();

    // 点击选项 → 回答消息展示 + 卡片已答
    await card.locator('button', { hasText: 'v1.2 候选版' }).click();
    await expect(page.locator('text=回答澄清').first()).toBeVisible({ timeout: 5000 });
    await expect(card.locator('text=已回答')).toBeVisible({ timeout: 5000 });
  });
});
