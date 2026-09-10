/**
 * 用户旅程 + 回归测试（真实后端，无任何 mock）。
 *
 * 每条用例对应 2026-09-10 深度排查修复的真实 bug 或一条完整业务闭环；
 * 这些用例在 mock E2E 时代无法通过——它们依赖真实契约、真实持久化与服务接线：
 *
 * - 时间显示：naive-UTC 被按本地时区解析 → 全部时间偏 8 小时
 * - 任务抽屉：运行详情链接指向 WakerTeam 自身而非 DeerFlow 网关
 * - Waker 详情：MCP 连接器添加/移除不持久化（刷新即丢）
 * - 群组配置：群技能/群描述/SOP 是纯前端假 UI（后端 API 存在但未接线）
 * - 调度：默认表单提交必 422（前端 target_type 含后端不接受的 waker）
 * - 调度触发：SchedulerService 未注入依赖 → 触发只留 pending 不派发
 * - Flow 编辑器：POST /flows/{id}/run 与后端路由不一致 → 运行永远 404
 * - 删除：带运行历史的调度/Flow（含关联数据的群组/员工）删除 500（FK 未清理）
 *
 * 另含：console error 断言（React key 警告类问题）、ISO 时区契约断言。
 */

import { test, expect } from '@playwright/test';
import {
  apiFetch,
  getApiBase,
  getTestPrefix,
  trackConsoleErrors,
  expectNoConsoleErrors,
  expectIsoWithTimezone,
  sqliteSweepTestData,
  waitForReplyOrNotice,
  skipIfReplyFailureNotice,
} from './helper';

const P = getTestPrefix();
const ALICE = `${P}alice`;

/* ═══════════════════════════════════════════════
   1. 看板：派活链路 + 时间显示回归
   ═══════════════════════════════════════════════ */

test.describe('看板（真实任务链路）', () => {
  test('派活任务出现且相对时间正确显示（非偏移 8 小时）+ 时间戳带时区', async ({ page }) => {
    const tracker = trackConsoleErrors(page);

    const task = await apiFetch('/tasks', {
      method: 'POST',
      body: JSON.stringify({ executor: ALICE, input_text: '[e2e] 看板时间回归任务' }),
    });
    expect(task.status).toBe('running');
    expectIsoWithTimezone(task.created_at, 'task.created_at');

    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const row = page.locator('tr', { hasText: '[e2e] 看板时间回归任务' });
    await expect(row).toBeVisible({ timeout: 10000 });
    // 回归：naive-UTC 被本地时区解析时这里会显示 “8小时前”
    await expect(row).toContainText(/刚刚|分钟前/);

    expectNoConsoleErrors(tracker);

    await apiFetch(`/tasks/${task.id}/cancel`, { method: 'POST' }).catch(() => {});
    sqliteSweepTestData();
  });

  test('任务抽屉的「查看运行详情」指向 DeerFlow 网关（非 WakerTeam 自身）', async ({ page }) => {
    const task = await apiFetch('/tasks', {
      method: 'POST',
      body: JSON.stringify({ executor: ALICE, input_text: '[e2e] 链接回归任务' }),
    });
    expect(task.thread_id, '任务应关联 DeerFlow thread').toBeTruthy();

    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await page.locator('tr', { hasText: '[e2e] 链接回归任务' }).click();

    const drawer = page.locator('.drawer-panel');
    await expect(drawer).toBeVisible();
    const link = drawer.locator('a', { hasText: '查看运行详情' });
    await expect(link).toBeVisible();

    const href = (await link.getAttribute('href')) ?? '';
    expect(href).toContain(`thread=${task.thread_id}`);
    // 回归：曾经使用 window.location.origin（=5173），链接点到看板自身
    expect(href.startsWith('http://localhost:5173')).toBe(false);
    expect(href).toMatch(/^http:\/\/localhost:2026\//);

    await apiFetch(`/tasks/${task.id}/cancel`, { method: 'POST' }).catch(() => {});
    sqliteSweepTestData();
  });
});

/* ═══════════════════════════════════════════════
   2. Waker 详情：MCP 连接器持久化回归
   ═══════════════════════════════════════════════ */

test.describe('Waker 详情（MCP 持久化）', () => {
  test('添加 MCP → 刷新仍在 → 移除 → 刷新不再', async ({ page }) => {
    const tracker = trackConsoleErrors(page);

    await page.goto(`/wakers/${ALICE}`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('h3', { hasText: 'MCP 连接器' })).toBeVisible();

    // 添加 browser-use（监听 PUT 完成，避免断言竞态；确认后弹窗应关闭）
    await page.getByRole('button', { name: '+ 添加连接器' }).click();
    await page.getByRole('button', { name: /browser-use/ }).click();
    const putDone = page.waitForResponse(
      (res) => res.url().includes(`/api/wakers/${ALICE}`) && res.request().method() === 'PUT',
    );
    await page.getByRole('button', { name: /确认添加/ }).click();
    await putDone;
    await expect(page.locator('.modal-panel')).not.toBeVisible();
    await expect(page.locator('body')).toContainText('browser-use');

    // 持久化断言（API 层）
    let waker = await apiFetch(`/wakers/${ALICE}`);
    expect(Object.keys(waker.mcp_connectors ?? {})).toContain('browser-use');

    // 刷新后仍在（回归：曾经只改前端 state，刷新即丢）
    await page.reload();
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('body')).toContainText('browser-use');

    // 移除并刷新
    const mcpRow = page.locator('div.flex.items-center.gap-3', { hasText: 'browser-use' });
    const putDone2 = page.waitForResponse(
      (res) => res.url().includes(`/api/wakers/${ALICE}`) && res.request().method() === 'PUT',
    );
    await mcpRow.getByRole('button', { name: '移除' }).click();
    await putDone2;
    await page.reload();
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    waker = await apiFetch(`/wakers/${ALICE}`);
    expect(Object.keys(waker.mcp_connectors ?? {})).not.toContain('browser-use');

    expectNoConsoleErrors(tracker);
  });
});

/* ═══════════════════════════════════════════════
   3. 群组配置：群技能 / 描述 / SOP / 转让负责人
   ═══════════════════════════════════════════════ */

test.describe('群组配置（真实持久化）', () => {
  let groupId: string;

  test.beforeAll(async () => {
    const groups = await apiFetch('/groups');
    const g = groups.find((x: { name: string }) => x.name.startsWith(P));
    if (!g) throw new Error('seeded group not found');
    groupId = g.id;
  });

  test('群技能：添加 → API 落库 → 刷新仍在 → 移除 → 刷新不再', async ({ page }) => {
    const tracker = trackConsoleErrors(page);
    await page.goto(`/groups/${groupId}/config`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: '+ 添加群技能' }).click();
    await page.getByRole('button', { name: /jira-analyzer/ }).click();
    const skillPostDone = page.waitForResponse(
      (res) =>
        res.url().includes(`/api/groups/${groupId}/skills`) &&
        res.request().method() === 'POST',
    );
    await page.getByRole('button', { name: /确认添加/ }).click();
    await skillPostDone;
    await expect(page.locator('.modal-panel')).not.toBeVisible();
    await expect(page.locator('body')).toContainText('jira-analyzer');

    // API 层持久化（回归：曾经只改前端 state）
    let skills = await apiFetch(`/groups/${groupId}/skills`);
    expect(skills.map((s: { skill_name: string }) => s.skill_name)).toContain('jira-analyzer');

    await page.reload();
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('body')).toContainText('jira-analyzer');

    // 移除
    const skillRow = page.locator('div.flex.items-center.gap-3', { hasText: 'jira-analyzer' });
    const skillDelDone = page.waitForResponse(
      (res) =>
        res.url().includes(`/api/groups/${groupId}/skills/`) &&
        res.request().method() === 'DELETE',
    );
    await skillRow.getByRole('button', { name: '移除' }).click();
    await skillDelDone;
    await page.reload();
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    skills = await apiFetch(`/groups/${groupId}/skills`);
    expect(skills.map((s: { skill_name: string }) => s.skill_name)).not.toContain('jira-analyzer');

    expectNoConsoleErrors(tracker);
  });

  test('群描述与 SOP 选择持久化', async ({ page }) => {
    const tracker = trackConsoleErrors(page);
    const descText = `[e2e] 群描述 ${Date.now()}`;

    await page.goto(`/groups/${groupId}/config`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // 群描述：失焦触发保存
    const descBox = page.locator('textarea[placeholder="描述该群组的目标..."]');
    await descBox.fill(descText);
    await descBox.blur();

    // SOP：切换为“数据分析流程”
    await page.locator('button', { hasText: '数据分析流程' }).click();

    // API 契约
    let group = await apiFetch(`/groups/${groupId}`);
    expect(group.description).toBe(descText);
    expect(group.sop_id).toBe('data-analysis');

    // 刷新后仍生效
    await page.reload();
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('textarea[placeholder="描述该群组的目标..."]')).toHaveValue(descText);
    await expect(page.locator('button', { hasText: '数据分析流程' })).toContainText('当前生效');

    // 恢复默认（standard）
    await page.locator('button', { hasText: '标准工作流' }).click();
    group = await apiFetch(`/groups/${groupId}`);
    expect(group.sop_id).toBe('standard');

    expectNoConsoleErrors(tracker);
  });

  test('转让负责人走真实 transfer-leader 接口并可持久化', async ({ page }) => {
    // 确保 alice 是成员（transfer 要求目标为成员）
    await apiFetch(`/groups/${groupId}/members`, {
      method: 'POST',
      body: JSON.stringify({ waker_id: ALICE, role: 'leader' }),
    }).catch(() => {});

    await page.goto(`/groups/${groupId}/config`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // 转让给 bob
    const bobRow = page.locator('div.flex.items-center.gap-3', { hasText: `${P}bob` });
    await bobRow.getByRole('button', { name: '转让负责人' }).click();
    await page.getByRole('button', { name: '确认转让' }).click();

    await expect
      .poll(async () => (await apiFetch(`/groups/${groupId}`)).leader_waker_id, {
        message: 'leader 应转让给 bob',
      })
      .toBe(`${P}bob`);

    // 转回 alice
    const aliceRow = page.locator('div.flex.items-center.gap-3', { hasText: `${P}alice` });
    await aliceRow.getByRole('button', { name: '转让负责人' }).click();
    await page.getByRole('button', { name: '确认转让' }).click();
    await expect
      .poll(async () => (await apiFetch(`/groups/${groupId}`)).leader_waker_id, {
        message: 'leader 应转回 alice',
      })
      .toBe(ALICE);
  });
});

/* ═══════════════════════════════════════════════
   4. 调度：创建（默认值即合法）+ 触发派发回归
   ═══════════════════════════════════════════════ */

test.describe('调度（真实触发）', () => {
  test('默认表单直接创建成功（无 422）→ 触发 → 执行历史 running 且派发任务', async ({ page }) => {
    const tracker = trackConsoleErrors(page);
    const name = `${P}调度-${Date.now()}`;

    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: '+ 创建调度' }).click();
    const dlg = page.locator('.fixed.inset-0 .max-w-md');
    await dlg.locator('input[placeholder="例如: 每日报告"]').fill(name);
    await dlg.locator('input[placeholder="0 9 * * *"]').fill('0 9 * * *');
    // 目标类型默认“员工任务”，无需改动；目标 ID 填 alice
    await expect(dlg.locator('select').nth(1)).toHaveValue('task');
    await dlg.locator('input[placeholder="员工名称（如 zww）"]').fill(ALICE);
    await dlg.getByRole('button', { name: '创建' }).click();

    // 回归：曾经默认 target_type=waker 直接 422
    await expect(dlg).not.toBeVisible({ timeout: 10000 });
    const row = page.locator('tr', { hasText: name });
    await expect(row).toBeVisible();

    // 触发（等待 trigger 请求完成，避免断言竞态）
    const triggerDone = page.waitForResponse(
      (res) =>
        /\/api\/schedules\/[^/]+\/trigger$/.test(new URL(res.url()).pathname) &&
        res.request().method() === 'POST',
    );
    await row.getByRole('button', { name: '触发' }).click();
    await triggerDone;

    // 展开执行历史
    await row.locator('td').first().click();
    await expect(page.locator('body')).toContainText('执行历史');

    // API 契约：触发必须真正派发（回归前：pending 且 task_id 为空）
    const schedules = await apiFetch('/schedules');
    const sched = schedules.find((s: { name: string }) => s.name === name);
    expect(sched, `schedule '${name}' 应存在`).toBeTruthy();
    expect(sched.target_type, `schedule 快照: ${JSON.stringify(sched)}`).toBe('task');
    await expect
      .poll(
        async () => {
          const runs = await apiFetch(`/schedules/${sched.id}/runs`);
          return runs[0]
            ? `${runs[0].status}|task=${runs[0].task_id ?? 'null'}|err=${runs[0].error_message ?? 'null'}`
            : 'no-runs';
        },
        { timeout: 15000, message: '触发后应真正派发（期望 running|task=<id>）' },
      )
      .toMatch(/^running\|task=/);
    const runs = await apiFetch(`/schedules/${sched.id}/runs`);
    expectIsoWithTimezone(runs[0].triggered_at, 'schedule_run.triggered_at');

    expectNoConsoleErrors(tracker);
  });
});

/* ═══════════════════════════════════════════════
   5. Flow：编辑器运行全链路（回归 404）
   ═══════════════════════════════════════════════ */

test.describe('Flow 运行（真实链路）', () => {
  test('编辑器点击运行 → 跳转运行详情页（不再 404/alert）', async ({ page }) => {
    const tracker = trackConsoleErrors(page);

    const flow = await apiFetch('/flows', {
      method: 'POST',
      body: JSON.stringify({
        name: `${P}运行验证-${Date.now()}`,
        group_id: 'default',
        definition_json: {
          version: 1,
          nodes: [
            { key: 'step1', type: 'waker_task', waker: ALICE, instruction: '[e2e] flow 运行节点', depends_on: [] },
          ],
        },
      }),
    });

    await page.goto(`/flows/${flow.id}`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
    await expect(page.locator('.react-flow')).toBeVisible();
    // 节点渲染 waker 子标签（证明 definition_json 已加载到画布）
    await expect(page.locator('.react-flow')).toContainText(ALICE);

    await page.getByRole('button', { name: '▶ 运行' }).click();

    // 回归：修复前 POST /api/flows/{id}/run 404（错误路由），修复后应跳转到运行详情页
    await page.waitForURL(/\/flow-runs\/[^/?#]+/, { timeout: 15000 });
    const runId = page.url().match(/flow-runs\/([^/?#]+)/)?.[1];
    expect(runId).toBeTruthy();

    await expect(page.locator('body')).toContainText('运行详情');

    const run = await apiFetch(`/flow-runs/${runId}`);
    expect(run.flow_def_id).toBe(flow.id);
    expectIsoWithTimezone(run.started_at, 'flow_run.started_at');

    expectNoConsoleErrors(tracker);

    // cleanup：取消运行 + 删除 Flow（后者回归：带运行记录的 Flow 删除不再 500）
    await apiFetch(`/flow-runs/${runId}/cancel`, { method: 'POST' }).catch(() => {});
    await apiFetch(`/flows/${flow.id}`, { method: 'DELETE' }).catch(() => {});
    sqliteSweepTestData();
  });
});

/* ═══════════════════════════════════════════════
   6. 直聊：消息持久化 + 会话状态标签回归
   ═══════════════════════════════════════════════ */

test.describe('直聊（消息持久化）', () => {
  test('发送消息 → 刷新仍在（会话持久化）', async ({ page }) => {
    const tracker = trackConsoleErrors(page);
    const msgText = `[e2e] 聊天持久化 ${Date.now()}`;

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });

    await page.locator('textarea[placeholder*="输入消息"]').fill(msgText);
    await page.locator('button[title="发送"]').click();
    await expect(page.locator('body')).toContainText(msgText, { timeout: 10000 });

    // API 契约：消息已持久化（轮询等待，消除乐观渲染与落库的竞态）
    let convId = '';
    await expect
      .poll(
        async () => {
          const convs = (await apiFetch(`/wakers/${ALICE}/conversations`)) as Array<{ id: string }>;
          convId = convs[0]?.id ?? '';
          if (!convId) return false;
          const msgs = (await apiFetch(`/conversations/${convId}/messages`)) as Array<{
            content_json: string | null;
          }>;
          return msgs.some((m) => (m.content_json ?? '').includes(msgText));
        },
        { timeout: 10000, message: '消息应持久化到 API' },
      )
      .toBe(true);
    const msgs = (await apiFetch(`/conversations/${convId}/messages`)) as Array<{
      created_at: string | null;
    }>;
    expectIsoWithTimezone(msgs[0].created_at, 'message.created_at');

    // waker 回复渲染链路：以 content_json={"text": ...} 存储的 waker 消息必须显示（历史 bug：空白气泡）
    const wakerText = `[e2e] waker 回复渲染 ${Date.now()}`;
    await apiFetch(`/conversations/${convId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ role: 'waker', waker_id: ALICE, content_json: { text: wakerText } }),
    });

    // 刷新后 user 消息与 waker 回复均仍在（真实持久化 + 渲染）
    await page.reload();
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('body')).toContainText(msgText, { timeout: 10000 });
    await expect(page.locator('body')).toContainText(wakerText, { timeout: 10000 });
    await expect(page.locator('div.direct-immersive').first()).toBeVisible();

    expectNoConsoleErrors(tracker);
    sqliteSweepTestData();
  });

  test('发送消息后 waker 自动回复（真实 DeerFlow run + 前端轮询）', async ({ page }) => {
    test.setTimeout(220_000); // 真实 LLM run 较慢，且需区分环境 LLM 故障
    // 独立新建会话，避免与其他用例的会话历史串扰
    const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
    await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 自动回复验证：收到请回复一句话');
    await page.locator('button[title="发送"]').click();

    // 真实链路：后端在 DeerFlow 上发起 waker run，回复异步写回；
    // 前端轮询（3s）应自动把回复带到页面（无需刷新），此实现验证不可用 mock 替代。
    const reply = await waitForReplyOrNotice(conv.id, 150_000);
    skipIfReplyFailureNotice(reply); // 环境 LLM 故障 → 跳过（非产品回归）
    expect(reply, '等待超时：未收到 waker 回复').not.toBeNull();
    expect(reply!.role).toBe('waker');
    expect(reply!.text.length).toBeGreaterThan(10);

    // UI 轮询应自动展示回复气泡
    await expect(page.locator('div.direct-immersive').last()).toBeVisible({ timeout: 30000 });

    // 会话标题应写回（DeerFlow TitleMiddleware 生成），不再是"未命名会话"
    await expect
      .poll(
        async () => {
          const convs = await apiFetch(`/wakers/${ALICE}/conversations`);
          const c = convs.find((x: { id: string }) => x.id === conv.id);
          return c?.title ?? '';
        },
        { timeout: 20000, message: '会话标题应写回（修复未命名会话）' },
      )
      .not.toBe('');
    sqliteSweepTestData();
  });

  test('发送后发送按钮进入运行态（停止），点击停止取消真实 run 并恢复', async ({ page }) => {
    test.setTimeout(150_000);
    // 独立新建会话，确保发送后立即进入等待（无历史回复干扰）
    const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
    await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 停止运行验证：请做一次详细调研');
    await page.locator('button[title="发送"]').click();

    // 用户消息发出后：发送按钮进入运行态（停止 / 实心正方形图标）
    const stopBtn = page.locator('button[title="停止"]');
    await expect(stopBtn).toBeVisible({ timeout: 10000 });
    await expect(stopBtn.locator('rect')).toBeVisible();

    // 等待真实 run 进入进行中，确保停止命中的是运行中的 run
    try {
      await expect
        .poll(
          async () => {
            const prog = (await apiFetch(`/conversations/${conv.id}/progress`)) as { active: boolean };
            return prog.active;
          },
          { timeout: 30000, message: '真实 run 应处于进行中' },
        )
        .toBe(true);
    } catch {
      // 环境 LLM 故障兜底：run 未能启动/保持运行则跳过（非产品回归）
      const notice = await waitForReplyOrNotice(conv.id, 5000);
      skipIfReplyFailureNotice(notice);
      throw new Error('真实 run 未进入进行中且无环境故障提示');
    }

    // 点击停止：取消真实 DeerFlow run → 写「已停止」提示 → 按钮恢复发送态
    await stopBtn.click();
    await expect(page.locator('button[title="发送"]')).toBeVisible({ timeout: 30000 });
    await expect(page.locator('body')).toContainText('已停止本次回复', { timeout: 15000 });

    // API 契约：停止提示已持久化，且没有 waker 回复写入
    const msgs = (await apiFetch(`/conversations/${conv.id}/messages`)) as Array<{
      role: string;
      content_json: string | null;
    }>;
    expect(msgs.some((m) => (m.content_json ?? '').includes('已停止本次回复'))).toBe(true);
    expect(msgs.some((m) => m.role === 'waker')).toBe(false);

    sqliteSweepTestData();
  });

  test('澄清卡片：结构化消息渲染卡片，选项回答持久化并触发新 run', async ({ page }) => {
    test.setTimeout(120_000);
    // 新建会话 + 预置一条带结构化澄清 meta 的 waker 消息
    // （等价于 DeerFlow ask_clarification 经 chat_reply 写回的形态）
    const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    await apiFetch(`/conversations/${conv.id}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        role: 'waker',
        waker_id: ALICE,
        content_json: {
          text: '好的，请先确认一下方向：',
          meta: {
            clarification: {
              version: 1,
              kind: 'human_input_request',
              source: 'ask_clarification',
              request_id: 'clarification:e2e-1',
              tool_call_id: 'call-e2e-1',
              clarification_type: 'approach_choice',
              question: '请选择调研方向',
              input_mode: 'choice_with_other',
              options: [
                { id: 'option-1', label: '方向 A：市场分析', value: '方向 A：市场分析' },
                { id: 'option-2', label: '方向 B：竞品研究', value: '方向 B：竞品研究' },
              ],
            },
          },
        },
      }),
    });

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });

    // 新建会话在列表首位（updated_at desc）→ 默认打开；澄清卡片渲染
    const card = page.locator('[data-testid="clarification-card"]');
    await expect(card).toBeVisible({ timeout: 10000 });
    await expect(card.locator('text=请选择调研方向')).toBeVisible();

    // 点击选项回答 → 回答消息展示 + 卡片转已答
    await card.locator('button', { hasText: '方向 A：市场分析' }).click();
    await expect(page.locator('text=回答澄清').first()).toBeVisible({ timeout: 10000 });
    await expect(card.locator('text=已回答')).toBeVisible({ timeout: 10000 });

    // API 契约：回答消息持久化（主 UI 同款文案 + 结构化 meta）
    await expect
      .poll(
        async () => {
          const msgs = (await apiFetch(`/conversations/${conv.id}/messages`)) as Array<{
            role: string;
            content_json: string | null;
          }>;
          return msgs.some(
            (m) =>
              (m.content_json ?? '').includes('For your clarification') &&
              (m.content_json ?? '').includes('方向 A：市场分析'),
          );
        },
        { timeout: 10000, message: '回答消息应持久化' },
      )
      .toBe(true);

    // 回答作为用户消息触发真实 run（后端调度回复）：运行启动后停止回收资源
    await expect
      .poll(
        async () => {
          const prog = (await apiFetch(`/conversations/${conv.id}/progress`)) as {
            active: boolean;
          };
          return prog.active;
        },
        { timeout: 30000, message: '回答后应触发新 run' },
      )
      .toBe(true);
    await apiFetch(`/conversations/${conv.id}/stop`, { method: 'POST' }).catch(() => {});

    sqliteSweepTestData();
  });

  test('多澄清聚合：答完全部卡片才触发一次真实 run', async ({ page }) => {
    test.setTimeout(120_000);
    // 新建会话 + 预置两张未答澄清卡（多澄清并存场景）
    const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    for (const i of [1, 2]) {
      await apiFetch(`/conversations/${conv.id}/messages`, {
        method: 'POST',
        body: JSON.stringify({
          role: 'waker',
          waker_id: ALICE,
          content_json: {
            meta: {
              clarification: {
                version: 1,
                kind: 'human_input_request',
                source: 'ask_clarification',
                request_id: `clarification:e2e-multi-${i}`,
                tool_call_id: `call-e2e-m${i}`,
                clarification_type: 'approach_choice',
                question: `请选择方向 ${i}`,
                input_mode: 'choice_with_other',
                options: [
                  { id: 'option-1', label: `方向 ${i} 选项一`, value: `方向 ${i} 选项一` },
                  { id: 'option-2', label: `方向 ${i} 选项二`, value: `方向 ${i} 选项二` },
                ],
              },
            },
          },
        }),
      });
    }

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });

    const cards = page.locator('[data-testid="clarification-card"]');
    await expect(cards).toHaveCount(2, { timeout: 10000 });

    // 回答第一张：仅入库（延迟触发）——无运行态，且后端无进行中 run
    await cards.first().locator('button', { hasText: '方向 1 选项一' }).click();
    await expect(cards.first().locator('text=已回答')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('button[title="停止"]')).not.toBeVisible();
    await page.waitForTimeout(1500);
    const progIdle = (await apiFetch(`/conversations/${conv.id}/progress`)) as {
      active: boolean;
    };
    expect(progIdle.active, '答完第一张不应触发 run').toBe(false);

    // 回答第二张（最后一张）：触发一次真实 run
    await cards.nth(1).locator('button', { hasText: '方向 2 选项一' }).click();
    await expect
      .poll(
        async () => {
          const prog = (await apiFetch(`/conversations/${conv.id}/progress`)) as {
            active: boolean;
          };
          return prog.active;
        },
        { timeout: 30000, message: '答完全部卡片应触发 run' },
      )
      .toBe(true);
    await apiFetch(`/conversations/${conv.id}/stop`, { method: 'POST' }).catch(() => {});

    // API 契约：两条回答均持久化（同一次处理携带全部回答）
    const msgs = (await apiFetch(`/conversations/${conv.id}/messages`)) as Array<{
      content_json: string | null;
    }>;
    expect(
      msgs.filter((m) => (m.content_json ?? '').includes('For your clarification')).length,
    ).toBe(2);

    sqliteSweepTestData();
  });

  test('waker 对话中可调用团队工具获取同事列表（回归 waker_identity 注入）', async ({ page }) => {
    test.setTimeout(220_000);
    // 确保 bob 存在且启用（其他 spec 可能变更 seeded 员工状态），保证同事列表含非执行者
    await apiFetch(`/wakers/${P}bob`).catch(async () => {
      await apiFetch('/wakers', {
        method: 'POST',
        body: JSON.stringify({
          name: `${P}bob`,
          description: 'E2E测试员工Bob',
          soul: '你是测试助手Bob。',
          tool_groups: ['web'],
          skills: [],
          max_concurrent_tasks: 2,
        }),
      });
    });
    await apiFetch(`/wakers/${P}bob/toggle`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: true }),
    });

    // 独立新建会话
    const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
    await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 请调用工具查询团队成员列表，只回复成员名字');
    await page.locator('button[title="发送"]').click();

    // 真实链路：waker run 携 waker_identity 调用 waker-team MCP 工具；
    // 回归背景：缺失该凭据时 MCP 拦截器 fail-closed，工具被拒（无法获取同事列表）。
    const reply = await waitForReplyOrNotice(conv.id, 150_000);
    skipIfReplyFailureNotice(reply); // 环境 LLM 故障 → 跳过（非产品回归）
    expect(reply, '等待超时：未收到查同事的回复').not.toBeNull();
    expect(reply!.role).toBe('waker');
    // 同事列表应包含 seeded 员工（bob 不可能是执行者 alice 自己）；且不得混入思考段
    expect(reply!.text).toContain(`${P}bob`);
    expect(reply!.text).not.toContain('</think>');

    // UI 也应渲染出回复气泡
    await expect(page.locator('div.direct-immersive').last()).toBeVisible({ timeout: 30000 });

    sqliteSweepTestData();
  });

  test('直聊页可新建会话（列表即时出现并切换）', async ({ page }) => {
    const before = (await apiFetch(`/wakers/${ALICE}/conversations`)) as Array<{
      id: string;
      group_id: string | null;
    }>;

    // 内容隔离契约：直聊列表只含直聊会话（group_id 为 null），不含群会话
    expect(
      before.every((c) => c.group_id === null),
      '直聊列表不应混入群会话（内容隔离）',
    ).toBe(true);

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });

    // 点击左侧列表头的「＋ 新建」
    await page.locator('button[title="新建会话"]').first().click();

    // API 层：会话数 +1
    await expect
      .poll(
        async () => ((await apiFetch(`/wakers/${ALICE}/conversations`)) as Array<unknown>).length,
        { timeout: 10000, message: '新建会话后列表应 +1' },
      )
      .toBe(before.length + 1);

    // UI 层：左侧会话面板显示新的会话总数（排除全局侧边栏 aside）
    await expect(page.locator('aside', { hasText: '对话任务' })).toContainText(
      `${before.length + 1} 个会话`,
      { timeout: 10000 },
    );
    sqliteSweepTestData();
  });

  test('等待期展示回复进度（思考/工具步骤快照）', async ({ page }) => {
    test.setTimeout(220_000);
    const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
    await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 进度快照验证：收到请回复一句话');
    await page.locator('button[title="发送"]').click();

    // API：运行期间 progress.active=true（快照已注册）
    await expect
      .poll(
        async () =>
          ((await apiFetch(`/conversations/${conv.id}/progress`)) as { active: boolean }).active,
        { timeout: 30000, message: '运行期间 progress.active 应为 true' },
      )
      .toBe(true);

    // UI：等待区展示进度（含"已进行 X"耗时）
    await expect(page.locator('body')).toContainText('已进行', { timeout: 15000 });

    // 等回复 → 进度归位 inactive
    const reply = await waitForReplyOrNotice(conv.id, 150_000);
    skipIfReplyFailureNotice(reply);
    expect(reply, '等待超时：未收到回复').not.toBeNull();
    await expect
      .poll(
        async () =>
          ((await apiFetch(`/conversations/${conv.id}/progress`)) as { active: boolean }).active,
        { timeout: 15000, message: '回复完成后 progress.active 应为 false' },
      )
      .toBe(false);

    sqliteSweepTestData();
  });
});

/* ═══════════════════════════════════════════════
   7. 群聊：Leader 自动回复（真实链路）
   ═══════════════════════════════════════════════ */

test.describe('群聊（真实持久化）', () => {
  test('发送消息后群 Leader 自动回复（真实 DeerFlow run + 前端轮询）', async ({ page }) => {
    test.setTimeout(220_000);
    const groups = await apiFetch('/groups');
    const g = groups.find((x: { name: string }) => x.name.startsWith(P));
    expect(g, 'seeded 群组应存在').toBeTruthy();
    expect(g.leader_waker_id, 'seeded 群组应有 Leader').toBe(ALICE);

    // 独立新建群会话
    const conv = await apiFetch(`/groups/${g.id}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    await page.goto(`/chat/group/${g.id}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
    await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 群聊自动回复验证：Leader 收到请回复');
    await page.locator('button[title="发送"]').click();

    const reply = await waitForReplyOrNotice(conv.id, 150_000);
    skipIfReplyFailureNotice(reply); // 环境 LLM 故障 → 跳过（非产品回归）
    expect(reply, '等待超时：未收到群 Leader 回复').not.toBeNull();
    expect(reply!.role).toBe('waker');

    // 群聊回复渲染为白色卡片（.msg-waker）
    await expect(page.locator('div.msg-waker').last()).toBeVisible({ timeout: 30000 });
    sqliteSweepTestData();
  });

  test('右侧任务/设置面板默认隐藏，可通过头部按钮展开/收起', async ({ page }) => {
    const tracker = trackConsoleErrors(page);
    const groups = await apiFetch('/groups');
    const g = groups.find((x: { name: string }) => x.name.startsWith(P));
    expect(g, 'seeded 群组应存在').toBeTruthy();

    await page.goto(`/chat/group/${g.id}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });

    // 默认隐藏：面板内的「群设置」tab 不应存在
    await expect(page.getByRole('button', { name: '群设置', exact: true })).not.toBeVisible();

    // 点击头部按钮展开面板
    await page.getByRole('button', { name: '任务 / 设置', exact: true }).click();
    await expect(page.getByRole('button', { name: '群设置', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '任务', exact: true })).toBeVisible();

    // 再次点击收起
    await page.getByRole('button', { name: '任务 / 设置', exact: true }).click();
    await expect(page.getByRole('button', { name: '群设置', exact: true })).not.toBeVisible();

    expectNoConsoleErrors(tracker);
  });

  test('群聊页可新建会话（头部按钮）', async ({ page }) => {
    const groups = await apiFetch('/groups');
    const g = groups.find((x: { name: string }) => x.name.startsWith(P));
    expect(g, 'seeded 群组应存在').toBeTruthy();
    const before = (await apiFetch(`/groups/${g.id}/conversations`)) as Array<unknown>;

    await page.goto(`/chat/group/${g.id}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });

    // 点击头部「＋ 新建会话」
    await page.locator('button[title="新建会话"]').first().click();

    // API 层：会话数 +1
    await expect
      .poll(
        async () => ((await apiFetch(`/groups/${g.id}/conversations`)) as Array<unknown>).length,
        { timeout: 10000, message: '新建会话后列表应 +1' },
      )
      .toBe(before.length + 1);
    sqliteSweepTestData();
  });

  test('运行状态条：真实任务运行中显示头像与计数，点击头像打开员工详情抽屉', async ({ page }) => {
    test.setTimeout(150_000);
    const tracker = trackConsoleErrors(page);
    const groups = await apiFetch('/groups');
    const g = groups.find((x: { name: string }) => x.name.startsWith(P));
    expect(g, 'seeded 群组应存在').toBeTruthy();

    // 真实派活一个任务给成员（起真实 DeerFlow run → status=running）
    let task: { id: string; status: string } | null = null;
    try {
      task = await apiFetch('/tasks', {
        method: 'POST',
        body: JSON.stringify({
          executor: `${P}bob`,
          input_text: '[e2e] 状态条验证：请输出一段较长的分析结果',
          group_id: g.id,
        }),
      });
    } catch {
      test.skip(true, 'DeerFlow 网关不可用，无法创建真实任务（非产品回归）');
    }
    expect(task!.status).toBe('running');

    // activity API 契约：任务出现在活动列表（运行中）
    const activity = (await apiFetch(`/groups/${g.id}/activity`)) as {
      active: boolean;
      items: Array<{
        waker: string;
        kind: string;
        status: string;
        task_id?: string;
        title: string;
        elapsed: number;
      }>;
    };
    expect(activity.active).toBe(true);
    const item = activity.items.find((i) => i.task_id === task!.id);
    expect(item, '新任务应出现在活动列表').toBeTruthy();
    expect(item!.waker).toBe(`${P}bob`);
    expect(item!.kind).toBe('member_task');
    expect(item!.status).toBe('running');
    expect(item!.title).toContain('状态条验证');
    expect(item!.elapsed).toBeGreaterThanOrEqual(0);

    // UI：打开群聊页 → 运行状态条可见（含 Waker 计数文案）
    await page.goto(`/chat/group/${g.id}`);
    const bar = page.locator('[data-testid="running-wakers-bar"]');
    await expect(bar).toBeVisible({ timeout: 15000 });
    await expect(bar).toContainText('个 Waker 正在运行或排队');

    // 点击目标头像（按任务标题精确匹配）→ 员工详情抽屉打开
    const avatarBtn = bar.locator('button[title*="状态条验证"]');
    await expect(avatarBtn).toBeVisible({ timeout: 10000 });
    await avatarBtn.click();
    const drawer = page.locator('[data-testid="waker-activity-drawer"]');
    await expect(drawer).toBeVisible({ timeout: 10000 });
    await expect(drawer).toContainText(`${P}bob`);
    await expect(drawer).toContainText('当前任务');
    await expect(drawer).toContainText('状态条验证');

    // 关闭抽屉
    await drawer.locator('button[title="关闭"]').click();
    await expect(drawer).not.toBeVisible();

    // 取消任务 → 该任务从活动列表消失（状态条收敛）
    await apiFetch(`/tasks/${task!.id}/cancel`, { method: 'POST' });
    await expect
      .poll(
        async () => {
          const a = (await apiFetch(`/groups/${g.id}/activity`)) as {
            items: Array<{ task_id?: string }>;
          };
          return a.items.some((i) => i.task_id === task!.id);
        },
        { timeout: 15000, message: '取消后任务应从活动列表消失' },
      )
      .toBe(false);

    expectNoConsoleErrors(tracker);
    sqliteSweepTestData();
  });

  test('leader_post 清单消息：落库后群聊页渲染，刷新仍在（含 @成员）', async ({ page }) => {
    const tracker = trackConsoleErrors(page);
    const groups = await apiFetch('/groups');
    const g = groups.find((x: { name: string }) => x.name.startsWith(P));
    expect(g, 'seeded 群组应存在').toBeTruthy();

    // 独立新建群会话（列表按 updated_at 降序，最新会话在首位 → 页面自动打开）
    const conv = await apiFetch(`/groups/${g.id}/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    const marker = `[e2e] 清单 ${Date.now()}`;
    await apiFetch(`/conversations/${conv.id}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        role: 'waker',
        waker_id: ALICE,
        content_json: {
          text: `${marker}：@${P}bob 请完成数据核对，今天 18:00 前同步本群。`,
          meta: { kind: 'leader_post', mentions: [`${P}bob`], partial: true },
        },
      }),
    });

    await page.goto(`/chat/group/${g.id}`);
    await expect(page.getByText(marker).first()).toBeVisible({ timeout: 15000 });

    // 刷新后仍在（真实持久化）
    await page.reload();
    await expect(page.getByText(marker).first()).toBeVisible({ timeout: 15000 });

    expectNoConsoleErrors(tracker);
    sqliteSweepTestData();
  });
});

/* ═══════════════════════════════════════════════
   7. 侧边栏即时刷新 + 契约断言
   ═══════════════════════════════════════════════ */

test.describe('侧边栏与契约', () => {
  test('创建员工后侧边栏团队列表即时出现（无需整页刷新）', async ({ page }) => {
    const name = `${P}sidebar-${Date.now()}`;

    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: '+ 创建员工' }).click();
    const dlg = page.locator('.modal-panel');
    await dlg.locator('input[placeholder="例如: researcher"]').fill(name);
    await dlg.locator('input[placeholder="简要描述该员工的职责"]').fill('E2E sidebar refresh');
    await dlg.locator('textarea[placeholder="输入 SOUL 模板内容..."]').fill('你是测试助手。');
    await dlg.getByRole('button', { name: '保存' }).click();
    await expect(dlg).not.toBeVisible({ timeout: 10000 });

    // 回归：侧边栏曾经只在挂载时拉取数据，新建后保持过期
    await expect(page.locator('aside.sidebar').getByText(name)).toBeVisible({ timeout: 10000 });

    await apiFetch(`/wakers/${name}`, { method: 'DELETE' }).catch(() => {});
  });

  test('API 时间契约：核心接口时间戳全部带时区', async () => {
    const wakers = await apiFetch('/wakers');
    for (const w of wakers as Array<{ name: string; created_at: string | null }>) {
      expectIsoWithTimezone(w.created_at, `wakers[${w.name}].created_at`);
    }
    const tasks = await apiFetch('/tasks');
    for (const t of tasks.items as Array<{ id: string; created_at: string | null; updated_at: string | null }>) {
      expectIsoWithTimezone(t.created_at, `tasks[${t.id}].created_at`);
      expectIsoWithTimezone(t.updated_at, `tasks[${t.id}].updated_at`);
    }
    const groups = await apiFetch('/groups');
    for (const g of groups as Array<{ id: string; created_at: string | null }>) {
      expectIsoWithTimezone(g.created_at, `groups[${g.id}].created_at`);
    }
    const schedules = await apiFetch('/schedules');
    for (const s of schedules as Array<{ id: string; next_run_at: string | null }>) {
      expectIsoWithTimezone(s.next_run_at, `schedules[${s.id}].next_run_at`);
    }
  });

  test('删除回归：带执行历史的调度可直接删除（204，非 500）', async () => {
    const sched = await apiFetch('/schedules', {
      method: 'POST',
      body: JSON.stringify({
        name: `${P}删除验证-${Date.now()}`,
        group_id: 'default',
        cron_expression: '0 9 * * *',
        target_type: 'task',
        target_id: ALICE,
      }),
    });
    await apiFetch(`/schedules/${sched.id}/trigger`, { method: 'POST' });

    const resp = await fetch(`${getApiBase()}/schedules/${sched.id}`, { method: 'DELETE' });
    expect(resp.status).toBe(204);
    sqliteSweepTestData();
  });
});
