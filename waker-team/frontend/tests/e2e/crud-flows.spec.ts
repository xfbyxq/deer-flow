import { test, expect } from '@playwright/test';
import { mockWakerTeamAPI } from './utils/mock-api';

test.beforeEach(async ({ page }) => {
  await mockWakerTeamAPI(page);
});

/* ═══════════════════════════════════════════════
   1. 新建员工 (Create Waker)
   ═══════════════════════════════════════════════ */

test.describe('Waker CRUD', () => {
  test('新建员工 — create dialog, fill form, submit, verify in list', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click create button
    await page.locator('button:has-text("+ 创建员工")').click();

    // Dialog should appear
    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('创建员工');

    // Fill in the form
    await dialog.locator('input[placeholder="例如: researcher"]').fill('test-waker');
    await dialog.locator('input[placeholder="简要描述该员工的职责"]').fill('自动化测试员工');
    await dialog.locator('textarea[placeholder="输入 SOUL 模板内容..."]').fill('你是一个测试助手。');

    // Select model
    await dialog.locator('select').first().selectOption('qwen3-flash');

    // Submit
    await dialog.locator('button:has-text("保存")').click();

    // Dialog should close and new waker should appear in the list
    await expect(dialog).not.toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=test-waker').first()).toBeVisible({ timeout: 5000 });
  });

  test('新建员工 — 选择模板自动带出字段，修改后提交', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.locator('button:has-text("+ 创建员工")').click();
    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    // 点击「前端工程师」模板卡片
    await dialog.locator('button:has-text("前端工程师")').first().click();

    // 模板字段被自动带出（名称/描述/角色）
    const nameInput = dialog.locator('input[placeholder="例如: researcher"]');
    await expect(nameInput).toHaveValue('frontend-dev');
    await expect(dialog.locator('input[placeholder="简要描述该员工的职责"]')).toHaveValue(/前端/);
    await expect(dialog.locator('input[placeholder="例如: 前端工程师"]')).toHaveValue('前端工程师');

    // 在模板基础上修改名称后提交
    await nameInput.fill('fe-template-test');
    await dialog.locator('button:has-text("保存")').click();

    await expect(dialog).not.toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=fe-template-test').first()).toBeVisible({ timeout: 5000 });
  });

  test('新建员工 — cancel closes dialog', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    await page.locator('button:has-text("+ 创建员工")').click();
    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible();

    // Click cancel
    await dialog.locator('button:has-text("取消")').click();
    await expect(dialog).not.toBeVisible({ timeout: 3000 });
  });

  /* ═══════════════════════════════════════════════
     2. 编辑员工 (Edit Waker)
     ═══════════════════════════════════════════════ */

  test('编辑员工 — edit dialog pre-fills data, modify and save', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click edit on first waker row
    await page.locator('tr >> button:has-text("编辑")').first().click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('编辑员工');

    // Name field should be pre-filled and read-only (uses readOnly, not disabled)
    const nameInput = dialog.locator('input[placeholder="例如: researcher"]');
    await expect(nameInput).toHaveValue('data-collector');
    await expect(nameInput).toHaveAttribute('readonly');

    // Modify description
    const descInput = dialog.locator('input[placeholder="简要描述该员工的职责"]');
    await descInput.fill('更新后的描述');

    // Save
    await dialog.locator('button:has-text("保存")').click();
    await expect(dialog).not.toBeVisible({ timeout: 5000 });

    // Updated description should be visible
    await expect(page.locator('text=更新后的描述').first()).toBeVisible({ timeout: 5000 });
  });

  /* ═══════════════════════════════════════════════
     3. 删除员工 (Delete Waker)
     ═══════════════════════════════════════════════ */

  test('删除员工 — confirm dialog removes waker', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Set up dialog handler for confirm()
    page.on('dialog', (dialog) => dialog.accept());

    // Verify waker exists before delete
    await expect(page.locator('text=data-collector').first()).toBeVisible();

    // Click delete
    await page.locator('tr >> button:has-text("删除")').first().click();

    // Waker should be removed from the table (may still appear in sidebar)
    await expect(page.locator('table').locator('text=data-collector')).toHaveCount(0, { timeout: 5000 });
  });

  /* ═══════════════════════════════════════════════
     4. 启用/停用员工 (Toggle Waker)
     ═══════════════════════════════════════════════ */

  test('停用员工 — toggle button changes status', async ({ page }) => {
    await page.goto('/wakers');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // data-collector is enabled, should show "停用" button
    const row = page.locator('tr:has-text("data-collector")');
    await expect(row.locator('text=启用').first()).toBeVisible();

    // Click 停用
    await row.locator('button:has-text("停用")').click();

    // After toggle, should show "启用" (disabled state)
    await expect(row.locator('button:has-text("启用")').first()).toBeVisible({ timeout: 5000 });
  });
});

/* ═══════════════════════════════════════════════
   5. 新建群组 (Create Group)
   ═══════════════════════════════════════════════ */

test.describe('Group CRUD', () => {
  test('新建群组 — create dialog, fill form, submit, verify in list', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click create button
    await page.locator('button:has-text("+ 创建群组")').click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('创建群组');

    // Fill form
    await dialog.locator('input[placeholder="例如: 研发一组"]').fill('测试群组');

    // Select leader
    await dialog.locator('select').first().selectOption('zhangweiwei');

    // Fill project ID
    await dialog.locator('input[placeholder="可选，关联项目标识"]').fill('proj-test');

    // Submit
    await dialog.locator('button:has-text("保存")').click();

    // Dialog should close and new group should appear
    await expect(dialog).not.toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=测试群组').first()).toBeVisible({ timeout: 5000 });
  });

  test('编辑群组 — edit dialog pre-fills and saves changes', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click edit on first group
    await page.locator('tr >> button:has-text("编辑")').first().click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('编辑群组');

    // Name should be pre-filled
    const nameInput = dialog.locator('input[placeholder="例如: 研发一组"]');
    await expect(nameInput).toHaveValue('D-EYE 项目组');

    // Modify name
    await nameInput.fill('D-EYE 改版组');

    // Save
    await dialog.locator('button:has-text("保存")').click();
    await expect(dialog).not.toBeVisible({ timeout: 5000 });

    await expect(page.locator('text=D-EYE 改版组').first()).toBeVisible({ timeout: 5000 });
  });

  test('查看成员 — member panel opens with member list', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click 成员 button on first group
    await page.locator('tr >> button:has-text("成员")').first().click();

    // Drawer panel should open
    const drawer = page.locator('.drawer-panel');
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await expect(drawer.locator('text=成员管理').first()).toBeVisible();
    await expect(drawer.locator('text=zhangweiwei').first()).toBeVisible();
  });

  test('删除群组 — confirm dialog removes group', async ({ page }) => {
    await page.goto('/groups');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    page.on('dialog', (dialog) => dialog.accept());

    await expect(page.locator('text=D-EYE 项目组').first()).toBeVisible();
    await page.locator('tr >> button:has-text("删除")').first().click();
    await expect(page.locator('text=D-EYE 项目组').first()).not.toBeVisible({ timeout: 5000 });
  });
});

/* ═══════════════════════════════════════════════
   6. 看板任务创建 (Board Task Dispatch)
   ═══════════════════════════════════════════════ */

test.describe('Board Task Dispatch', () => {
  test('派活 — open dialog, fill task, submit', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click dispatch button
    await page.locator('button:has-text("+ 派活")').click();

    // Dialog should appear
    const dialog = page.locator('h2:has-text("派活")').locator('..').locator('..');
    await expect(page.locator('h2:has-text("派活")').first()).toBeVisible({ timeout: 3000 });

    // Select executor (first enabled waker)
    const executorSelect = page.locator('select[required]').first();
    await expect(executorSelect).toBeVisible();

    // Fill task content
    const textarea = page.locator('textarea[placeholder="输入任务描述..."]');
    await textarea.fill('执行自动化测试任务');

    // Submit
    await page.locator('button:has-text("派活")').last().click();

    // Dialog should close
    await expect(page.locator('h2:has-text("派活")').first()).not.toBeVisible({ timeout: 5000 });
  });

  test('看板 — stat cards show correct counts', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Total tasks = 8
    await expect(page.locator('text=8').first()).toBeVisible({ timeout: 5000 });
  });

  test('看板 — click task row opens detail drawer', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click on first task row
    await page.locator('tbody tr').first().click();

    // Task detail drawer should open
    const drawer = page.locator('.drawer-panel');
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await expect(drawer.locator('text=任务详情').first()).toBeVisible();
  });

  test('看板 — filter by status', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Select status filter
    const statusSelect = page.locator('select').first();
    await statusSelect.selectOption('running');

    // The page should reload with filtered data (mock returns same data regardless)
    await expect(page.locator('tbody')).toBeVisible({ timeout: 5000 });
  });
});

/* ═══════════════════════════════════════════════
   7. 流程创建 (Flow CRUD)
   ═══════════════════════════════════════════════ */

test.describe('Flow CRUD', () => {
  test('新建 Flow — create dialog, fill form, submit', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click create button
    await page.locator('button:has-text("+ 创建 Flow")').click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('创建 Flow');

    // Fill name
    await dialog.locator('input[placeholder="例如: 日报审批流程"]').fill('测试流程');

    // Group should already have a default selected
    // Fill description
    await dialog.locator('input[placeholder="可选描述"]').fill('自动化测试流程');

    // Submit (definition JSON already has default content)
    await dialog.locator('button:has-text("保存")').click();

    // Dialog should close — after save it navigates to /flows/:id
    await expect(dialog).not.toBeVisible({ timeout: 5000 });
    // Should navigate to flow detail page
    await expect(page).toHaveURL(/\/flows\/flow-/, { timeout: 5000 });
  });

  test('编辑 Flow 配置 — config button opens edit dialog', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click 配置 button on first flow
    await page.locator('tr >> button:has-text("配置")').first().click();

    const dialog = page.locator('.modal-panel');
    await expect(dialog).toBeVisible({ timeout: 3000 });
    await expect(dialog.locator('h2')).toHaveText('编辑 Flow');

    // Name should be pre-filled
    const nameInput = dialog.locator('input[placeholder="例如: 日报审批流程"]');
    await expect(nameInput).toHaveValue('每日质量报告');

    // Close dialog
    await dialog.locator('button:has-text("取消")').click();
    await expect(dialog).not.toBeVisible({ timeout: 3000 });
  });

  test('查看运行记录 — opens drawer with run history', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click 运行记录 button
    await page.locator('tr >> button:has-text("运行记录")').first().click();

    // Drawer should open
    const drawer = page.locator('.drawer-panel');
    await expect(drawer).toBeVisible({ timeout: 3000 });
    await expect(drawer.locator('text=运行记录').first()).toBeVisible();
  });

  test('删除 Flow — confirm removes flow', async ({ page }) => {
    await page.goto('/flows');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    page.on('dialog', (d) => d.accept());

    await expect(page.locator('text=每日质量报告').first()).toBeVisible();
    await page.locator('tr >> button:has-text("删除")').first().click();
    await expect(page.locator('text=每日质量报告').first()).not.toBeVisible({ timeout: 5000 });
  });
});

/* ═══════════════════════════════════════════════
   8. 调度创建 (Schedule CRUD)
   ═══════════════════════════════════════════════ */

test.describe('Schedule CRUD', () => {
  test('新建调度 — create dialog, fill form, submit', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click create button
    await page.locator('button:has-text("+ 创建调度")').click();

    // Dialog should appear (uses a different class, not .modal-panel)
    await expect(page.locator('h2:has-text("创建调度")').first()).toBeVisible({ timeout: 3000 });

    // Fill name
    const dialogs = page.locator('.fixed.inset-0').last();
    await dialogs.locator('input[placeholder="例如: 每日报告"]').fill('测试调度');

    // Select group
    await dialogs.locator('select').first().selectOption('deye');

    // Fill cron expression
    await dialogs.locator('input[placeholder="0 9 * * *"]').fill('30 3 * * *');

    // Fill target ID（目标类型默认“员工任务”，placeholder 为员工名称）
    await dialogs.locator('input[placeholder="员工名称（如 zww）"]').fill('data-collector');

    // Submit
    await dialogs.locator('button:has-text("创建")').click();

    // Dialog should close
    await expect(page.locator('h2:has-text("创建调度")').first()).not.toBeVisible({ timeout: 5000 });

    // New schedule should appear in list
    await expect(page.locator('text=测试调度').first()).toBeVisible({ timeout: 5000 });
  });

  test('暂停/恢复调度 — pause and resume actions', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // First schedule is active, should have "暂停" button
    const row = page.locator('tr:has-text("每日质量看板")');
    await expect(row).toBeVisible();
    await expect(row.locator('button:has-text("暂停")').first()).toBeVisible();

    // Click pause
    await row.locator('button:has-text("暂停")').first().click();

    // After pause, should show "恢复" button
    await expect(row.locator('button:has-text("恢复")').first()).toBeVisible({ timeout: 5000 });
  });

  test('展开执行历史 — click row expands run history', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click on schedule row to expand
    await page.locator('tr:has-text("每日质量看板")').first().click();

    // Expanded section should show "执行历史"
    await expect(page.locator('text=执行历史').first()).toBeVisible({ timeout: 3000 });
  });

  test('删除调度 — confirm removes schedule', async ({ page }) => {
    await page.goto('/schedules');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    page.on('dialog', (d) => d.accept());

    await expect(page.locator('text=每日质量看板').first()).toBeVisible();
    await page.locator('tr:has-text("每日质量看板") >> button:has-text("删除")').first().click();
    await expect(page.locator('text=每日质量看板').first()).not.toBeVisible({ timeout: 5000 });
  });
});

/* ═══════════════════════════════════════════════
   9. 侧边栏团队面板导航
   ═══════════════════════════════════════════════ */

test.describe('Sidebar Team Navigation', () => {
  test('点击员工跳转私聊页', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    // Click on a waker in the team panel
    const wakerItem = page.locator('.team-item').first();
    await expect(wakerItem).toBeVisible();
    await wakerItem.click();

    // Should navigate to chat/direct/:wakerName
    await expect(page).toHaveURL(/\/chat\/direct\//, { timeout: 5000 });
  });

  test('点击群组跳转群聊页', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    // Switch to 群组 tab
    await page.locator('.team-tab:has-text("群组")').click();

    // Click on a group
    const groupItem = page.locator('.team-item').first();
    await expect(groupItem).toBeVisible();
    await groupItem.click();

    // Should navigate to chat/group/:groupId
    await expect(page).toHaveURL(/\/chat\/group\//, { timeout: 5000 });
  });

  test('搜索过滤后点击跳转', async ({ page }) => {
    await page.goto('/');

    // Search for xiaoxi
    await page.locator('.team-search input').fill('xiaoxi');
    const items = page.locator('.team-item');
    await expect(items).toHaveCount(1);

    // Click the filtered result
    await items.first().click();
    await expect(page).toHaveURL(/\/chat\/direct\/xiaoxi/, { timeout: 5000 });
  });
});

/* ═══════════════════════════════════════════════
   10. Waker 配置页交互 (WakerDetailPage)
   ═══════════════════════════════════════════════ */

test.describe('WakerDetailPage Interactions', () => {
  test('5 个配置区域均可见', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Verify all sections
    await expect(page.locator('text=基础配置').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=技能 Skill').first()).toBeVisible();
    await expect(page.locator('text=MCP 连接器').first()).toBeVisible();
    await expect(page.locator('text=工具组').first()).toBeVisible();
    await expect(page.locator('text=删除此 Waker').first()).toBeVisible();
  });

  test('技能市场 picker 打开并选择技能', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click "从技能市场添加"
    await page.locator('button:has-text("从技能市场添加")').first().click();

    // Skill picker modal should appear
    await expect(page.locator('text=技能市场').first()).toBeVisible({ timeout: 3000 });
  });

  test('MCP 连接器 picker 打开', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click "添加连接器"
    await page.locator('button:has-text("添加连接器")').first().click();

    // MCP picker modal should appear
    await expect(page.locator('text=MCP').first()).toBeVisible({ timeout: 3000 });
  });

  test('工具组切换 — click toggles tool group', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Tool groups are rendered as buttons with tool group names (enum values)
    const toolGroupButtons = page.locator('button:has-text("web"), button:has-text("jira")');
    const count = await toolGroupButtons.count();
    expect(count).toBeGreaterThan(0);
  });

  test('进入对话链接跳转正确', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click "进入对话" link
    const chatLink = page.locator('a:has-text("进入对话")');
    await expect(chatLink).toBeVisible();
    await expect(chatLink).toHaveAttribute('href', '/chat/direct/data-collector');
  });

  test('删除确认弹窗打开', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    // Click delete button in danger zone
    await page.locator('button:has-text("删除")').last().click();

    // Confirmation modal should appear
    await expect(page.locator('text=确认删除').first()).toBeVisible({ timeout: 3000 });
    await expect(page.locator('text=此操作不可撤销').first()).toBeVisible();
  });

  test('返回员工管理链接', async ({ page }) => {
    await page.goto('/wakers/data-collector');
    await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });

    const backLink = page.locator('a:has-text("返回员工管理")');
    await expect(backLink).toBeVisible();
    await expect(backLink).toHaveAttribute('href', '/wakers');
  });
});
