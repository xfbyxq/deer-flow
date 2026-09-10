import { test, expect } from '@playwright/test';
import { mockWakerTeamAPI } from './utils/mock-api';

test.beforeEach(async ({ page }) => {
  await mockWakerTeamAPI(page);
});

test.describe('Sidebar', () => {
  test('renders with brand name "WakerTeam"', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.brand-name')).toHaveText('WakerTeam');
  });

  test('navigation links are visible', async ({ page }) => {
    await page.goto('/');
    const navLabels = ['看板', '流程', '调度', '员工', '群组'];
    for (const label of navLabels) {
      await expect(page.locator(`.nav-label, .nav-item >> text=${label}`).first()).toBeVisible();
    }
  });

  test('clicking nav links navigates to correct routes', async ({ page }) => {
    await page.goto('/');

    // Click 流程
    await page.locator('.nav-item:has-text("流程")').click();
    await expect(page).toHaveURL(/\/flows/);

    // Click 调度
    await page.locator('.nav-item:has-text("调度")').click();
    await expect(page).toHaveURL(/\/schedules/);

    // Click 员工
    await page.locator('.nav-item:has-text("员工")').click();
    await expect(page).toHaveURL(/\/wakers/);

    // Click 群组
    await page.locator('.nav-item:has-text("群组")').click();
    await expect(page).toHaveURL(/\/groups/);

    // Click 看板 (back to home)
    await page.locator('.nav-item:has-text("看板")').click();
    await expect(page).toHaveURL(/\/$/);
  });

  test('team panel shows 员工/群组 tabs', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.team-tab:has-text("员工")')).toBeVisible();
    await expect(page.locator('.team-tab:has-text("群组")')).toBeVisible();
  });

  test('clicking 群组 tab switches to group list', async ({ page }) => {
    await page.goto('/');
    // Default tab is 员工, switch to 群组
    await page.locator('.team-tab:has-text("群组")').click();
    // Group list should show group names
    await expect(page.locator('.team-item').first()).toBeVisible();
    await expect(page.locator('.team-item-name').first()).toContainText('D-EYE');
  });

  test('search input filters waker list', async ({ page }) => {
    await page.goto('/');
    const searchInput = page.locator('.team-search input');
    await searchInput.fill('xiaoxi');
    // Should filter to show only xiaoxi
    const items = page.locator('.team-item');
    await expect(items).toHaveCount(1);
    await expect(items.first()).toContainText('xiaoxi');
  });

  test('collapse button shrinks sidebar (icons only)', async ({ page }) => {
    await page.goto('/');
    // Initially expanded
    await expect(page.locator('.sidebar')).not.toHaveClass(/collapsed/);
    // Click collapse button
    await page.locator('.sidebar-head .icon-btn').click();
    // Now collapsed
    await expect(page.locator('.sidebar')).toHaveClass(/collapsed/);
    // Team panel should be hidden
    await expect(page.locator('.team-panel')).not.toBeVisible();
  });
});
