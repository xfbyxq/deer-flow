/* 配置功能交互验证脚本（临时，验证后删除） */
const { chromium } = require("@playwright/test");
const URL = "file:///Users/sanyi/code/python/deer-flow/waker-team/prototype/index.html";

(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  const results = {};
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message));

  await page.goto(URL);
  await page.waitForTimeout(400);

  // ── 1. 员工管理 → 配置页 ──
  await page.click('button[data-action="nav"][data-view="wakers"]');
  await page.waitForTimeout(300);
  await page.screenshot({ path: "/tmp/cfg-1-wakers.png" });
  await page.click('button[data-action="open-waker-config"][data-id="xiaoxi"]');
  await page.waitForTimeout(350);
  results.wakerConfigVisible = await page.isVisible(".config-identity-name");
  results.skillCountBefore = await page.locator(".chip.skill-chip").count();
  await page.screenshot({ path: "/tmp/cfg-2-waker-config.png" });

  // ── 2. 移除一个技能 ──
  await page.locator(".chip-x").first().click();
  await page.waitForTimeout(300);
  results.skillCountAfterRemove = await page.locator(".chip.skill-chip").count();

  // ── 3. 技能选择器：分类筛选 + 勾选 + 确认添加 ──
  await page.click('button[data-action="open-skill-picker"]');
  await page.waitForTimeout(350);
  results.pickerVisible = await page.isVisible(".modal");
  await page.screenshot({ path: "/tmp/cfg-3-skill-picker.png" });
  await page.click('.modal-cat-chip:has-text("数据与 AI")');
  await page.waitForTimeout(250);
  results.pickerFilteredCount = await page.locator(".picker-item").count();
  await page.locator(".picker-item:not(.disabled)").first().click();
  await page.waitForTimeout(150);
  results.pickerCountLabel = await page.locator("#pickerCount").textContent();
  await page.click("#pickerConfirm");
  await page.waitForTimeout(300);
  results.skillCountAfterAdd = await page.locator(".chip.skill-chip").count();

  // ── 4. MCP 连接器添加 ──
  await page.click('button[data-action="open-mcp-picker"]');
  await page.waitForTimeout(300);
  await page.locator(".picker-item:not(.disabled)").first().click();
  await page.click("#pickerConfirm");
  await page.waitForTimeout(300);
  results.mcpCountAfterAdd = await page.locator(".config-section .mcp-item").count();
  await page.screenshot({ path: "/tmp/cfg-4-waker-config-after.png" });

  // ── 5. 工具组切换 + 模型切换 + 启用开关 ──
  await page.locator(".chip.toggle-chip").first().click();
  await page.waitForTimeout(250);
  await page.selectOption('select[data-action="cfg-model"]', "glm-4.6");
  await page.waitForTimeout(250);
  results.modelValue = await page.inputValue('select[data-action="cfg-model"]');
  await page.click('[data-action="toggle-waker-enabled"]');
  await page.waitForTimeout(250);
  results.enabledTag = await page.locator(".config-identity-actions .tag").textContent();

  // ── 6. 群组配置页 ──
  await page.click('button[data-action="nav"][data-view="groups"]');
  await page.waitForTimeout(300);
  await page.click('button[data-action="open-group-config"]');
  await page.waitForTimeout(350);
  results.groupConfigVisible = await page.isVisible(".config-identity-name");
  await page.screenshot({ path: "/tmp/cfg-5-group-config.png" });
  results.memberCount = await page.locator(".member-manage-row").count();
  await page.locator('button[data-action="remove-member"]').first().click();
  await page.waitForTimeout(300);
  results.memberCountAfterRemove = await page.locator(".member-manage-row").count();
  await page.click('button[data-action="open-member-picker"]');
  await page.waitForTimeout(300);
  await page.locator(".picker-item").first().click();
  await page.screenshot({ path: "/tmp/cfg-6-member-picker.png" });
  await page.click("#pickerConfirm");
  await page.waitForTimeout(300);
  results.memberCountAfterAdd = await page.locator(".member-manage-row").count();
  await page.locator('button[data-action="set-sop"]').nth(1).click();
  await page.waitForTimeout(300);
  results.sopActive = await page.locator(".sop-option.on .sop-option-name").textContent();
  await page.click('button[data-action="open-skill-picker"][data-target="group"]');
  await page.waitForTimeout(300);
  await page.locator(".picker-item:not(.disabled)").first().click();
  await page.click("#pickerConfirm");
  await page.waitForTimeout(300);
  results.groupSkillCountAfterAdd = await page.locator(".config-section .mcp-item").count();
  await page.screenshot({ path: "/tmp/cfg-7-group-config-after.png" });

  // ── 7. 设置页交互 ──
  await page.click('button[data-action="nav"][data-view="settings"]');
  await page.waitForTimeout(350);
  results.settingsTitle = await page.locator(".page-title").textContent();
  await page.screenshot({ path: "/tmp/cfg-8-settings.png" });
  await page.click('[data-action="toggle-density"]');
  await page.waitForTimeout(300);
  results.compactOn = await page.evaluate(() => document.body.classList.contains("compact"));
  await page.locator('[data-action="toggle-notify"][data-key="notifyTask"]').click();
  await page.waitForTimeout(250);
  await page.click('[data-action="toggle-mcp-conn"]');
  await page.waitForTimeout(250);
  results.mcpConnTag = await page.locator(".panel-card .member-row .tag").first().textContent();
  await page.screenshot({ path: "/tmp/cfg-9-settings-toggled.png" });

  // ── 8. 对话页管理按钮 → 配置页 ──
  await page.click('button[data-action="open-waker"][data-id="zhangweiwei"]');
  await page.waitForTimeout(400);
  await page.click('.chat-side button[data-action="open-waker-config"]');
  await page.waitForTimeout(350);
  results.configFromChatName = await page.locator(".config-identity-name").textContent();

  console.log(JSON.stringify({ errors, results }, null, 2));
  await browser.close();
})();
