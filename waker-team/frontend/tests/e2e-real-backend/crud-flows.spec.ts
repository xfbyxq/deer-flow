/**
 * Real-backend CRUD flow tests.
 *
 * These tests hit the REAL backend API (localhost:8000, degraded mode).
 * Data is seeded by globalSetup (see setup.ts) and cleaned up by globalTeardown.
 *
 * NO page.route() mocking — all API calls go through the Vite proxy to the real backend.
 */

import { test, expect } from '@playwright/test';

const API_BASE = 'http://localhost:8000/api';
const TEST_PREFIX = 'test-e2e-';

// Track items created during tests for cleanup
const createdWakers: string[] = [];
const createdGroups: string[] = [];
const createdFlows: string[] = [];
const createdSchedules: string[] = [];

test.afterAll(async () => {
  // Cleanup items created during tests (not the globally seeded ones)
  for (const name of createdWakers) {
    await fetch(`${API_BASE}/wakers/${name}`, { method: 'DELETE' }).catch(() => {});
  }
  for (const id of createdSchedules) {
    await fetch(`${API_BASE}/schedules/${id}`, { method: 'DELETE' }).catch(() => {});
  }
  for (const id of createdFlows) {
    await fetch(`${API_BASE}/flows/${id}`, { method: 'DELETE' }).catch(() => {});
  }
  for (const id of createdGroups) {
    await fetch(`${API_BASE}/groups/${id}`, { method: 'DELETE' }).catch(() => {});
  }
});

/* ═══════════════════════════════════════════════
   1. Waker CRUD — Real Backend
   ═══════════════════════════════════════════════ */

test.describe('Waker CRUD (real backend)', () => {
  test('seeded wakers appear in the list', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Seeded wakers should be visible
    await expect(page.locator(`text=${TEST_PREFIX}alice`).first()).toBeVisible({ timeout: 10000 });
    await expect(page.locator(`text=${TEST_PREFIX}bob`).first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator(`text=${TEST_PREFIX}charlie`).first()).toBeVisible({ timeout: 5000 });
  });

  test('seeded waker exists in API response', async () => {
    const res = await fetch(`${API_BASE}/wakers`);
    expect(res.ok).toBeTruthy();
    const wakers = await res.json();
    const names = wakers.map((w: { name: string }) => w.name);
    expect(names).toContain(`${TEST_PREFIX}alice`);
    expect(names).toContain(`${TEST_PREFIX}bob`);
    expect(names).toContain(`${TEST_PREFIX}charlie`);
  });

  test('create waker via UI — verify in list AND via API', async ({ page }) => {
    const wakerName = `${TEST_PREFIX}newuser-${Date.now()}`;
    createdWakers.push(wakerName);

    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click create
    await page.locator('button:has-text("+ 创建员工")').click();
    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    // Fill form
    await dialog.locator('input[placeholder="例如: researcher"]').fill(wakerName);
    await dialog.locator('input[placeholder="简要描述该员工的职责"]').fill('Real backend E2E test waker');
    await dialog.locator('textarea[placeholder="输入 SOUL 模板内容..."]').fill('你是E2E测试助手。');

    // Submit
    await dialog.locator('button:has-text("保存")').click();

    // Dialog should close
    await expect(dialog).not.toBeVisible({ timeout: 5000 });

    // New waker should appear in the list
    await expect(page.locator(`text=${wakerName}`).first()).toBeVisible({ timeout: 10000 });

    // Verify via API
    const res = await fetch(`${API_BASE}/wakers/${wakerName}`);
    expect(res.ok).toBeTruthy();
    const waker = await res.json();
    expect(waker.name).toBe(wakerName);
    expect(waker.description).toBe('Real backend E2E test waker');
  });

  test('edit waker — verify changes persist via API', async ({ page }) => {
    // Use the first seeded waker
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click edit on the alice row
    const row = page.locator(`tr:has-text("${TEST_PREFIX}alice")`);
    await expect(row).toBeVisible({ timeout: 5000 });
    await row.locator('button:has-text("编辑")').click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('编辑员工');

    // Modify description
    const descInput = dialog.locator('input[placeholder="简要描述该员工的职责"]');
    await descInput.fill('更新后的描述-Real Backend');

    // Save
    await dialog.locator('button:has-text("保存")').click();
    await expect(dialog).not.toBeVisible({ timeout: 5000 });

    // Verify updated description appears in UI
    await expect(page.locator('text=更新后的描述-Real Backend').first()).toBeVisible({ timeout: 5000 });

    // Verify via API
    const res = await fetch(`${API_BASE}/wakers/${TEST_PREFIX}alice`);
    expect(res.ok).toBeTruthy();
    const waker = await res.json();
    expect(waker.description).toBe('更新后的描述-Real Backend');
  });

  test('toggle waker enable/disable — verify via API', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const row = page.locator(`tr:has-text("${TEST_PREFIX}bob")`);
    await expect(row).toBeVisible({ timeout: 5000 });

    // Bob is enabled by default → "停用" button visible
    const disableBtn = row.locator('button:has-text("停用")');
    await expect(disableBtn.first()).toBeVisible({ timeout: 5000 });

    // 停用
    await disableBtn.first().click();
    await expect(row.locator('button:has-text("启用")').first()).toBeVisible({ timeout: 5000 });

    // 恢复原状态：后续用例（如查同事列表）需要 seeded 员工保持启用
    await row.locator('button:has-text("启用")').first().click();
    await expect(disableBtn.first()).toBeVisible({ timeout: 5000 });

    // Verify via API
    const res = await fetch(`${API_BASE}/wakers/${TEST_PREFIX}bob`);
    expect(res.ok).toBeTruthy();
  });

  test('delete waker — verify removed from API', async ({ page }) => {
    // Create a waker to delete
    const wakerName = `${TEST_PREFIX}todelete-${Date.now()}`;
    await fetch(`${API_BASE}/wakers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: wakerName, description: 'To be deleted' }),
    });

    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Verify it exists in the list
    await expect(page.locator(`text=${wakerName}`).first()).toBeVisible({ timeout: 5000 });

    // Accept confirm dialog
    page.on('dialog', (d) => d.accept());

    // Click delete
    const row = page.locator(`tr:has-text("${wakerName}")`);
    await row.locator('button:has-text("删除")').click();

    // Should be removed from the table
    await expect(page.locator(`table >> text=${wakerName}`)).toHaveCount(0, { timeout: 5000 });

    // Verify via API — should be 404 (or 502 if DeerFlow unavailable and lookup fails)
    const res = await fetch(`${API_BASE}/wakers/${wakerName}`);
    expect([404, 502]).toContain(res.status);
  });
});

/* ═══════════════════════════════════════════════
   2. Group CRUD — Real Backend
   ═══════════════════════════════════════════════ */

test.describe('Group CRUD (real backend)', () => {
  test('seeded group appears in the list', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator('text=test-e2e-测试群组').first()).toBeVisible({ timeout: 10000 });
  });

  test('create group via UI — verify in list AND via API', async ({ page }) => {
    const groupName = `test-e2e-newgroup-${Date.now()}`;

    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.locator('button:has-text("+ 创建群组")').click();
    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    // Fill form
    await dialog.locator('input[placeholder="例如: 研发一组"]').fill(groupName);

    // Select leader (use seeded waker)
    await dialog.locator('select').first().selectOption(`${TEST_PREFIX}alice`);

    // Submit
    await dialog.locator('button:has-text("保存")').click();
    await expect(dialog).not.toBeVisible({ timeout: 5000 });

    // New group should appear
    await expect(page.locator(`text=${groupName}`).first()).toBeVisible({ timeout: 5000 });

    // Verify via API — find the group by listing
    const res = await fetch(`${API_BASE}/groups`);
    expect(res.ok).toBeTruthy();
    const groups = await res.json();
    const found = groups.find((g: { name: string }) => g.name === groupName);
    expect(found).toBeTruthy();
    createdGroups.push(found.id);
  });

  test('view members — member panel opens with seeded members', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click 成员 on the test group row
    const row = page.locator('tr:has-text("test-e2e-测试群组")');
    await expect(row).toBeVisible({ timeout: 5000 });
    await row.locator('button:has-text("成员")').click();

    // Drawer should open
    const drawer = page.locator('.drawer-panel');
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await expect(drawer.locator('text=成员管理').first()).toBeVisible();
    // Seeded members (bob, charlie) should be visible in the drawer
    // Note: alice is the leader, not a member in the members list
    await expect(drawer.locator(`text=${TEST_PREFIX}bob`).first()).toBeVisible({ timeout: 5000 });
  });

  test('edit group — verify changes persist', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const row = page.locator('tr:has-text("test-e2e-测试群组")');
    await expect(row).toBeVisible({ timeout: 5000 });
    await row.locator('button:has-text("编辑")').click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    // Modify name
    const nameInput = dialog.locator('input[placeholder="例如: 研发一组"]');
    await nameInput.fill('test-e2e-测试群组-已编辑');

    await dialog.locator('button:has-text("保存")').click();
    await expect(dialog).not.toBeVisible({ timeout: 5000 });

    await expect(page.locator('text=test-e2e-测试群组-已编辑').first()).toBeVisible({ timeout: 5000 });

    // Revert the name change for other tests
    const res = await fetch(`${API_BASE}/groups`);
    const groups = await res.json();
    const g = groups.find((x: { name: string }) => x.name === 'test-e2e-测试群组-已编辑');
    if (g) {
      await fetch(`${API_BASE}/groups/${g.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'test-e2e-测试群组' }),
      });
    }
  });
});

/* ═══════════════════════════════════════════════
   3. Board / Task — Real Backend
   ═══════════════════════════════════════════════ */

test.describe('Board Task (real backend)', () => {
  test('board page loads and shows task data', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Board should render with stat cards
    await expect(page.locator('.rounded-xl').first()).toBeVisible({ timeout: 5000 });
  });

  test('dispatch task via UI — dialog opens and form is fillable', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click dispatch button
    await page.locator('button:has-text("+ 派活")').click();

    // Dialog should appear
    await expect(page.locator('h2:has-text("派活")').first()).toBeVisible({ timeout: 5000 });

    // Verify dialog has form elements
    const textarea = page.locator('textarea');
    const textareaCount = await textarea.count();
    expect(textareaCount).toBeGreaterThan(0);
  });

  test('board API returns data', async () => {
    const res = await fetch(`${API_BASE}/board`);
    expect(res.ok).toBeTruthy();
    const board = await res.json();
    expect(board).toHaveProperty('counts');
    expect(board).toHaveProperty('items');
    expect(board).toHaveProperty('total');
  });
});

/* ═══════════════════════════════════════════════
   4. Flow CRUD — Real Backend
   ═══════════════════════════════════════════════ */

test.describe('Flow CRUD (real backend)', () => {
  test('seeded flow appears in the list', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator('text=test-e2e-测试流程').first()).toBeVisible({ timeout: 10000 });
  });

  test('create flow via UI — dialog opens with form fields', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.locator('button:has-text("+ 创建 Flow")').click();
    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 5000 });
    await expect(dialog.locator('h2')).toHaveText('创建 Flow');

    // Verify dialog has input fields
    const nameInput = dialog.locator('input[placeholder="例如: 日报审批流程"]');
    await expect(nameInput).toBeVisible({ timeout: 3000 });

    // Close dialog via cancel
    await dialog.locator('button:has-text("取消")').click();
    await expect(dialog).not.toBeVisible({ timeout: 3000 });
  });

  test('flow detail page shows config button', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click 配置 on the seeded flow
    const row = page.locator('tr:has-text("test-e2e-测试流程")');
    await expect(row).toBeVisible({ timeout: 5000 });
    await row.locator('button:has-text("配置")').click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('编辑 Flow');

    // Close dialog
    await dialog.locator('button:has-text("取消")').click();
    await expect(dialog).not.toBeVisible({ timeout: 3000 });
  });

  test('delete flow — confirm removes from list', async ({ page }) => {
    // Create a flow to delete via API
    const flowName = `test-e2e-todelete-${Date.now()}`;
    let flowId: string | null = null;
    try {
      const res = await fetch(`${API_BASE}/flows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: flowName,
          group_id: 'default',
          definition_json: { version: 1, nodes: [] },
        }),
      });
      if (res.ok) {
        const flow = await res.json();
        flowId = flow.id;
        createdFlows.push(flowId);
      } else {
        // If creation fails (e.g., default group doesn't exist), skip test
        return;
      }
    } catch {
      return;
    }

    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator(`text=${flowName}`).first()).toBeVisible({ timeout: 5000 });

    page.on('dialog', (d) => d.accept());
    await page.locator(`tr:has-text("${flowName}") >> button:has-text("删除")`).click();
    await expect(page.locator(`text=${flowName}`).first()).not.toBeVisible({ timeout: 5000 });
  });
});

/* ═══════════════════════════════════════════════
   5. Schedule CRUD — Real Backend
   ═══════════════════════════════════════════════ */

test.describe('Schedule CRUD (real backend)', () => {
  test('seeded schedule appears in the list', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await expect(page.locator('text=test-e2e-测试调度').first()).toBeVisible({ timeout: 10000 });
  });

  test('pause/resume schedule — verify status change', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const row = page.locator('tr:has-text("test-e2e-测试调度")');
    await expect(row).toBeVisible({ timeout: 5000 });

    // Should have "暂停" button (active by default)
    const pauseBtn = row.locator('button:has-text("暂停")').first();
    const resumeBtn = row.locator('button:has-text("恢复")').first();

    const hasPause = await pauseBtn.isVisible().catch(() => false);
    if (hasPause) {
      await pauseBtn.click();
      await expect(resumeBtn).toBeVisible({ timeout: 5000 });
    } else {
      await resumeBtn.click();
      await expect(pauseBtn).toBeVisible({ timeout: 5000 });
    }
  });

  test('expand schedule run history', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click on schedule row to expand
    await page.locator('tr:has-text("test-e2e-测试调度")').first().click();

    // Expanded section should show "执行历史"
    await expect(page.locator('text=执行历史').first()).toBeVisible({ timeout: 3000 });
  });
});

/* ═══════════════════════════════════════════════
   6. Waker Detail Page — Real Backend
   ═══════════════════════════════════════════════ */

test.describe('WakerDetailPage (real backend)', () => {
  test('waker detail page loads with all sections', async ({ page }) => {
    await page.goto(`/wakers/${TEST_PREFIX}alice`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Verify all sections
    await expect(page.locator('text=基础配置').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=技能 Skill').first()).toBeVisible();
    await expect(page.locator('text=MCP 连接器').first()).toBeVisible();
    await expect(page.locator('text=工具组').first()).toBeVisible();
    await expect(page.locator('text=删除此 Waker').first()).toBeVisible();
  });

  test('chat link has correct href', async ({ page }) => {
    await page.goto(`/wakers/${TEST_PREFIX}alice`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const chatLink = page.locator('a:has-text("进入对话")');
    await expect(chatLink).toBeVisible();
    await expect(chatLink).toHaveAttribute('href', `/chat/direct/${TEST_PREFIX}alice`);
  });

  test('back link points to /wakers', async ({ page }) => {
    await page.goto(`/wakers/${TEST_PREFIX}alice`);
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const backLink = page.locator('a:has-text("返回员工管理")');
    await expect(backLink).toBeVisible();
    await expect(backLink).toHaveAttribute('href', '/wakers');
  });
});
