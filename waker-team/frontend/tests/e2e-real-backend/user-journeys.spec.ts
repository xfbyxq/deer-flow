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
  test('发送消息 → 刷新仍在 → 会话标签显示「进行中」', async ({ page }) => {
    const tracker = trackConsoleErrors(page);
    const msgText = `[e2e] 聊天持久化 ${Date.now()}`;

    await page.goto(`/chat/direct/${ALICE}`);
    await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });

    await page.locator('textarea[placeholder*="输入消息"]').fill(msgText);
    await page.locator('button[title="发送"]').click();
    await expect(page.locator('body')).toContainText(msgText, { timeout: 10000 });

    // 回归：active 会话曾错误显示“已完成”
    await expect(page.locator('body')).toContainText('进行中');

    // API 契约：消息已持久化
    const convs = await apiFetch(`/wakers/${ALICE}/conversations`);
    expect(convs.length).toBeGreaterThan(0);
    const msgs = await apiFetch(`/conversations/${convs[0].id}/messages`);
    expect(msgs.some((m: { content_json: string | null }) => (m.content_json ?? '').includes(msgText))).toBe(true);
    expectIsoWithTimezone(msgs[0].created_at, 'message.created_at');

    // waker 回复渲染链路：以 content_json={"text": ...} 存储的 waker 消息必须显示（历史 bug：空白气泡）
    const wakerText = `[e2e] waker 回复渲染 ${Date.now()}`;
    await apiFetch(`/conversations/${convs[0].id}/messages`, {
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
    await expect(page.locator('div.direct-immersive').last()).toBeVisible({ timeout: 150000 });

    // API 复核：waker 回复已写入且非空
    await expect
      .poll(
        async () => {
          const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
          return msgs.filter(
            (m: { role: string; content_json: string | null }) =>
              m.role === 'waker' && (m.content_json ?? '').length > 10,
          ).length;
        },
        { timeout: 30000, message: 'waker 回复应写入会话' },
      )
      .toBeGreaterThan(0);
    sqliteSweepTestData();
  });

  test('waker 对话中可调用团队工具获取同事列表（回归 waker_identity 注入）', async ({ page }) => {
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
    await expect
      .poll(
        async () => {
          const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
          const wakerMsgs = msgs.filter(
            (m: { role: string; content_json: string | null }) =>
              m.role === 'waker' && (m.content_json ?? '').length > 10,
          );
          return wakerMsgs.length ? (wakerMsgs[wakerMsgs.length - 1].content_json ?? '') : '';
        },
        { timeout: 150000, message: '应收到查同事的 waker 回复' },
      )
      .toContain(`${P}bob`); // 同事列表应包含 seeded 员工（bob 不可能是执行者 alice 自己）

    // 回复不得混入模型思考段（<think>）
    const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
    const lastWaker = msgs.filter((m: { role: string }) => m.role === 'waker').pop();
    expect(lastWaker.content_json).not.toContain('</think>');

    // UI 也应渲染出回复气泡
    await expect(page.locator('div.direct-immersive').last()).toBeVisible({ timeout: 30000 });

    sqliteSweepTestData();
  });
});

/* ═══════════════════════════════════════════════
   7. 群聊：Leader 自动回复（真实链路）
   ═══════════════════════════════════════════════ */

test.describe('群聊（真实持久化）', () => {
  test('发送消息后群 Leader 自动回复（真实 DeerFlow run + 前端轮询）', async ({ page }) => {
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

    // 群聊回复渲染为白色卡片（.msg-waker）；等待其自动出现
    await expect(page.locator('div.msg-waker').last()).toBeVisible({ timeout: 150000 });

    // API 复核：Leader（alice）已回复
    await expect
      .poll(
        async () => {
          const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
          return msgs.filter((m: { role: string; waker_id: string | null }) => m.role === 'waker').length;
        },
        { timeout: 30000, message: '群 Leader 回复应写入会话' },
      )
      .toBeGreaterThan(0);
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
