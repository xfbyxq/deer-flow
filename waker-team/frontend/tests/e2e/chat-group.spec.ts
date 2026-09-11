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

  // C4：新建群会话 messages 返回 [] → 发消息 → 页面不崩、无应用级 console error
  test('新建群会话（空 messages）发消息不崩溃且无 console error', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      const text = msg.text();
      // 忽略浏览器对 4xx/5xx 网络响应的 "Failed to load resource" 提示（非应用错误）
      if (!/Failed to load resource/i.test(text)) consoleErrors.push(text);
    });
    const pageErrors: string[] = [];
    page.on('pageerror', (err) => pageErrors.push(err.message));

    // progress 端点基础 mock 未实现（404）；补一个 200 {active:false}，
    // 消除轮询期网络噪声，聚焦断言“应用无 console error”。
    await page.route('**/api/conversations/*/progress', (route) =>
      route.fulfill({ json: { active: false } }),
    );

    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // 新建会话 → messages 返回 []（空会话）
    await page.locator('button[title="新建会话"]').first().click();

    const textarea = page.locator('textarea[placeholder*="输入消息"]').first();
    await expect(textarea).toBeVisible({ timeout: 5000 });
    await textarea.fill('这是新会话的第一条消息');
    await page.locator('button[title="发送"]').first().click();

    // 页面不崩：用户消息渲染 + composer 仍可用
    await expect(page.locator('text=这是新会话的第一条消息').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('textarea[placeholder*="输入消息"]').first()).toBeVisible();

    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });

  // CF8：单条 waker 消息带 meta.clarifications 两张卡 → 两张都渲染 → 答完两张才触发一次 run
  test('单条消息多澄清（meta.clarifications）：两张卡都渲染，答完全部才触发一次 run', async ({ page }) => {
    const card1 = {
      version: 1, kind: 'human_input_request', source: 'ask_clarification',
      request_id: 'clarification:mock-single-msg-1', tool_call_id: 'call-m1',
      clarification_type: 'approach_choice', question: '第一个问题：选择调研方向',
      input_mode: 'choice_with_other',
      options: [
        { id: 'a1', label: '方向 甲', value: '方向 甲' },
        { id: 'a2', label: '方向 乙', value: '方向 乙' },
      ],
    };
    const card2 = {
      version: 1, kind: 'human_input_request', source: 'ask_clarification',
      request_id: 'clarification:mock-single-msg-2', tool_call_id: 'call-m2',
      clarification_type: 'suggestion', question: '第二个问题：选择交付形式',
      input_mode: 'choice_with_other',
      options: [
        { id: 'b1', label: '交付 PPT', value: '交付 PPT' },
        { id: 'b2', label: '交付 报告', value: '交付 报告' },
      ],
    };

    // 单条 waker 消息：meta.clarifications=[card1,card2]，meta.clarification=card1（CONTRACT-CLARIFICATIONS）
    const msgs: Array<{
      id: string; conversation_id: string; role: string; waker_id: string | null;
      content_json: string | null; created_at: string | null;
    }> = [
      {
        id: 'msg-multi-single', conversation_id: 'conv-deye-1', role: 'waker', waker_id: 'zhangweiwei',
        content_json: JSON.stringify({
          text: '需要确认两件事：',
          meta: { clarifications: [card1, card2], clarification: card1 },
        }),
        created_at: '2025-01-06T02:01:00Z',
      },
    ];
    const posts: Array<{ defer_reply?: boolean; content_json?: unknown }> = [];
    let runResults = 0;

    // 覆盖 conv-deye-1 的 messages 端点（后注册优先于基础 mock），有状态模拟回答入库与 run 触发
    await page.route('**/api/conversations/conv-deye-1/messages*', async (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: msgs });
      }
      const body = (route.request().postDataJSON() ?? {}) as {
        defer_reply?: boolean; content_json?: unknown;
      };
      posts.push(body);
      const answerMsg = {
        id: `msg-ans-${posts.length}`, conversation_id: 'conv-deye-1', role: 'user', waker_id: null,
        content_json: body.content_json ? JSON.stringify(body.content_json) : null,
        created_at: new Date().toISOString(),
      };
      msgs.push(answerMsg);
      // 仅非 defer（最后一张回答）才追加一条 run 结果，模拟“触发一次 run”
      if (body.defer_reply !== true) {
        runResults += 1;
        msgs.push({
          id: `msg-run-${runResults}`, conversation_id: 'conv-deye-1', role: 'waker', waker_id: 'zhangweiwei',
          content_json: JSON.stringify({ text: '（模拟运行结果）已根据全部回答继续处理' }),
          created_at: new Date().toISOString(),
        });
      }
      return route.fulfill({ status: 201, json: answerMsg });
    });

    await page.goto('/chat/group/deye');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // 两张卡都渲染
    const cards = page.locator('[data-testid="clarification-card"]');
    await expect(cards).toHaveCount(2, { timeout: 5000 });
    await expect(cards.nth(0).locator('text=第一个问题：选择调研方向')).toBeVisible();
    await expect(cards.nth(1).locator('text=第二个问题：选择交付形式')).toBeVisible();

    // 回答第一张 → defer_reply=true（不触发 run），卡1已答、卡2仍可交互
    await cards.nth(0).locator('button', { hasText: '方向 甲' }).click();
    await expect(cards.nth(0).locator('text=已回答')).toBeVisible({ timeout: 5000 });
    expect(posts.length).toBe(1);
    expect(posts[0].defer_reply).toBe(true);
    expect(runResults).toBe(0);
    await expect(cards.nth(1).locator('button', { hasText: '交付 PPT' })).toBeVisible();

    // 回答第二张（最后一张）→ 无 defer_reply，触发一次 run
    await cards.nth(1).locator('button', { hasText: '交付 PPT' }).click();
    await expect(cards.nth(1).locator('text=已回答')).toBeVisible({ timeout: 5000 });
    await expect(
      page.locator('text=（模拟运行结果）已根据全部回答继续处理').first(),
    ).toBeVisible({ timeout: 5000 });

    expect(posts.length).toBe(2);
    expect(posts[1].defer_reply).toBeFalsy();
    expect(runResults).toBe(1); // 恰好触发一次 run
  });
});
