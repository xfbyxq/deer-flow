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

  test('sending switches the send button to running (stop) state; stopping restores it', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const textarea = page.locator('textarea[placeholder*="输入消息"]').first();
    await expect(textarea).toBeVisible({ timeout: 5000 });
    await textarea.fill('请帮我做一次长任务');
    await page.locator('button[title="发送"]').first().click();

    // 用户消息发出后：发送按钮变为运行中状态（实心正方形 / 停止）
    const stopBtn = page.locator('button[title="停止"]').first();
    await expect(stopBtn).toBeVisible({ timeout: 5000 });

    // 点击停止：显示「已停止」提示，按钮恢复发送态
    await stopBtn.click();
    await expect(page.locator('button[title="发送"]').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=已停止本次回复').first()).toBeVisible({ timeout: 5000 });
  });

  test('clarification card renders with options; clicking an option answers and closes it', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // 切换到澄清测试会话（消息仅含未答澄清）
    await page.locator('button', { hasText: '澄清测试-选项' }).first().click();

    const card = page.locator('[data-testid="clarification-card"]');
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(card.locator('text=请选择调研方向')).toBeVisible();
    await expect(card.locator('text=不同方向的侧重点不同')).toBeVisible();
    await expect(card.locator('button', { hasText: '方向 A：市场分析' })).toBeVisible();

    // 点击选项 → 回答消息展示 + 卡片转为已答态
    await card.locator('button', { hasText: '方向 A：市场分析' }).click();
    await expect(page.locator('text=回答澄清').first()).toBeVisible({ timeout: 5000 });
    await expect(card.locator('text=已回答')).toBeVisible({ timeout: 5000 });
    // 已答后选项按钮不再展示
    await expect(card.locator('button', { hasText: '方向 B：竞品研究' })).not.toBeVisible();
  });

  test('clarification form validates required fields and submits', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.locator('button', { hasText: '澄清测试-表单' }).first().click();

    const card = page.locator('[data-testid="clarification-card"]');
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(card.locator('text=请补充调研参数')).toBeVisible();

    // 必填校验：直接提交 → 报错
    await card.getByRole('button', { name: '提交' }).click();
    await expect(card.locator('text=请填写必填项')).toBeVisible();

    // 填写主题（必填）+ 选择地域 → 提交成功
    await card.locator('input[type="text"]').fill('消费级 3D 打印市场');
    await card.locator('select').selectOption('中国大陆');
    await card.getByRole('button', { name: '提交' }).click();
    await expect(page.locator('text=回答澄清').first()).toBeVisible({ timeout: 5000 });
    await expect(card.locator('text=已回答')).toBeVisible({ timeout: 5000 });
  });

  test('multiple clarifications: answers defer until all answered, then processed once', async ({ page }) => {
    await page.goto('/chat/direct/xiaoxi');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // 切换到多澄清会话（两张未答卡）
    await page.locator('button', { hasText: '多澄清测试' }).first().click();

    const cards = page.locator('[data-testid="clarification-card"]');
    await expect(cards).toHaveCount(2);
    // 提示条：还有 2 个待回答
    await expect(page.locator('text=还有 2 个澄清问题待回答')).toBeVisible();

    // 回答第一张（非最新卡）：精确关闭第一张；未触发处理（无停止按钮）
    await cards.first().locator('button', { hasText: '市场分析方向' }).click();
    await expect(cards.first().locator('text=已回答')).toBeVisible({ timeout: 5000 });
    await expect(cards.nth(1).locator('text=已回答')).not.toBeVisible();
    await expect(page.locator('button[title="停止"]')).not.toBeVisible();
    await expect(page.locator('text=还有 1 个澄清问题待回答')).toBeVisible();

    // 回答第二张（最后一张）：触发一次处理（进入运行态）
    await cards.nth(1).locator('button', { hasText: '竞品研究方向' }).click();
    await expect(page.locator('button[title="停止"]')).toBeVisible({ timeout: 5000 });
    // 提示条消失（已全部回答）
    await expect(page.locator('text=个澄清问题待回答')).not.toBeVisible();
  });
});
