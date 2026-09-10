/**
 * Real-backend sidebar tests.
 *
 * Verifies sidebar navigation with real data from the backend API.
 * NO page.route() mocking.
 */

import { test, expect } from '@playwright/test';

const TEST_PREFIX = 'test-e2e-';

test.describe('Sidebar (real backend)', () => {
  test('renders with brand name "WakerTeam"', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.brand-name')).toHaveText('WakerTeam');
  });

  test('navigation links are visible and route correctly', async ({ page }) => {
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

  test('team panel shows seeded wakers from API', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    // Seeded wakers should appear in the team panel
    await expect(page.locator(`.team-item:has-text("${TEST_PREFIX}alice")`).first()).toBeVisible({ timeout: 10000 });
  });

  test('click waker in sidebar → navigates to direct chat', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    const wakerItem = page.locator(`.team-item:has-text("${TEST_PREFIX}alice")`).first();
    await expect(wakerItem).toBeVisible({ timeout: 10000 });
    await wakerItem.click();

    // Should navigate to chat/direct/:wakerName
    await expect(page).toHaveURL(new RegExp(`\\/chat\\/direct\\/${TEST_PREFIX}alice`), { timeout: 5000 });
  });

  test('switch to 群组 tab → shows seeded groups', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    // Switch to 群组 tab
    await page.locator('.team-tab:has-text("群组")').click();

    // Seeded group should appear
    const groupItem = page.locator('.team-item:has-text("test-e2e")').first();
    await expect(groupItem).toBeVisible({ timeout: 10000 });
  });

  test('click group in sidebar → navigates to group chat', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    // Switch to 群组 tab
    await page.locator('.team-tab:has-text("群组")').click();

    const groupItem = page.locator('.team-item:has-text("test-e2e")').first();
    await expect(groupItem).toBeVisible({ timeout: 10000 });
    await groupItem.click();

    // Should navigate to chat/group/:groupId
    await expect(page).toHaveURL(/\/chat\/group\//, { timeout: 5000 });
  });

  test('search filters real wakers', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    // Search for alice
    await page.locator('.team-search input').fill(`${TEST_PREFIX}alice`);
    const items = page.locator('.team-item');
    await expect(items).toHaveCount(1, { timeout: 5000 });
    await expect(items.first()).toContainText(`${TEST_PREFIX}alice`);
  });

  test('search with no match shows empty', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.sidebar')).toBeVisible();

    // Search for nonexistent waker
    await page.locator('.team-search input').fill('nonexistent-waker-xyz');
    const items = page.locator('.team-item');
    await expect(items).toHaveCount(0, { timeout: 5000 });
  });

  test('collapse button shrinks sidebar', async ({ page }) => {
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
