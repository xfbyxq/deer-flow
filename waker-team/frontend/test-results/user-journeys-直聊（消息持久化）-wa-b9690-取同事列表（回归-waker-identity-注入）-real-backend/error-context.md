# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: user-journeys.spec.ts >> 直聊（消息持久化） >> waker 对话中可调用团队工具获取同事列表（回归 waker_identity 注入）
- Location: tests/e2e-real-backend/user-journeys.spec.ts:448:3

# Error details

```
Error: 应收到查同事的 waker 回复

expect(received).toContain(expected) // indexOf

Expected substring: "test-e2e-bob"
Received string:    ""

Call Log:
- Test timeout of 60000ms exceeded
```

# Page snapshot

```yaml
- generic [ref=e3]:
  - complementary [ref=e4]:
    - generic [ref=e5]:
      - generic [ref=e6]:
        - generic [ref=e7]: W
        - generic [ref=e8]: WakerTeam
      - button "收起侧边栏" [ref=e9]:
        - img [ref=e10]
    - navigation [ref=e12]:
      - generic [ref=e13]: 工作管理
      - link "看板" [ref=e14] [cursor=pointer]:
        - /url: /
        - img [ref=e15]
        - generic [ref=e19]: 看板
      - link "流程" [ref=e20] [cursor=pointer]:
        - /url: /flows
        - img [ref=e21]
        - generic [ref=e26]: 流程
      - link "调度" [ref=e27] [cursor=pointer]:
        - /url: /schedules
        - img [ref=e28]
        - generic [ref=e31]: 调度
      - generic [ref=e32]: 员工资源
      - link "员工" [ref=e33] [cursor=pointer]:
        - /url: /wakers
        - img [ref=e34]
        - generic [ref=e39]: 员工
      - link "群组" [ref=e40] [cursor=pointer]:
        - /url: /groups
        - img [ref=e41]
        - generic [ref=e46]: 群组
    - generic [ref=e47]:
      - generic [ref=e48]:
        - button "员工 6" [ref=e49]:
          - text: 员工
          - generic [ref=e50]: "6"
        - button "群组 3" [ref=e51]:
          - text: 群组
          - generic [ref=e52]: "3"
      - textbox "搜索员工..." [ref=e54]
      - generic [ref=e55]:
        - button "B backend-dev 负责后端服务、API 与数据层开发及性能稳定性" [ref=e56] [cursor=pointer]:
          - generic [ref=e57]: B
          - generic [ref=e59]:
            - generic [ref=e60]: backend-dev
            - generic [ref=e61]: 负责后端服务、API 与数据层开发及性能稳定性
        - button "T test-e2e-alice E2E测试员工Alice" [ref=e62] [cursor=pointer]:
          - generic [ref=e63]: T
          - generic [ref=e65]:
            - generic [ref=e66]: test-e2e-alice
            - generic [ref=e67]: E2E测试员工Alice
        - button "T test-e2e-bob E2E测试员工Bob" [ref=e68] [cursor=pointer]:
          - generic [ref=e69]: T
          - generic [ref=e71]:
            - generic [ref=e72]: test-e2e-bob
            - generic [ref=e73]: E2E测试员工Bob
        - button "T test-e2e-charlie E2E测试员工Charlie" [ref=e74] [cursor=pointer]:
          - generic [ref=e75]: T
          - generic [ref=e77]:
            - generic [ref=e78]: test-e2e-charlie
            - generic [ref=e79]: E2E测试员工Charlie
        - button "U ui-test-agent UI 验收测试员工" [ref=e80] [cursor=pointer]:
          - generic [ref=e81]: U
          - generic [ref=e83]:
            - generic [ref=e84]: ui-test-agent
            - generic [ref=e85]: UI 验收测试员工
        - button "Z zww 小张 — 易大哥的3D打印博主生活助手，严谨负责日常数据调研工作。" [ref=e86] [cursor=pointer]:
          - generic [ref=e87]: Z
          - generic [ref=e89]:
            - generic [ref=e90]: zww
            - generic [ref=e91]: 小张 — 易大哥的3D打印博主生活助手，严谨负责日常数据调研工作。
    - button "A Admin" [ref=e93]:
      - generic [ref=e94]: A
      - generic [ref=e96]: Admin
      - img [ref=e98]
  - main [ref=e102]:
    - generic [ref=e103]:
      - generic [ref=e104]:
        - generic [ref=e105]: T
        - generic [ref=e107]:
          - generic [ref=e108]:
            - generic [ref=e109]: test-e2e-alice
            - generic [ref=e110]:
              - img [ref=e111]
              - text: 1v1 私聊
          - generic [ref=e114]: 在线
      - generic [ref=e115]:
        - complementary [ref=e116]:
          - generic [ref=e117]:
            - generic [ref=e118]:
              - generic [ref=e119]: T
              - generic [ref=e121]: test-e2e-alice
            - paragraph [ref=e122]: E2E测试员工Alice
          - generic [ref=e123]:
            - button "对话任务" [ref=e124]
            - button "自动任务" [ref=e125]
          - generic [ref=e127]:
            - generic [ref=e128]: 1 个会话
            - button "未命名会话 进行中 9月10日 17:36" [ref=e129]:
              - generic [ref=e130]: 未命名会话
              - generic [ref=e131]:
                - generic [ref=e132]: 进行中
                - generic [ref=e133]: 9月10日 17:36
        - generic [ref=e134]:
          - generic [ref=e135]:
            - generic [ref=e137]:
              - generic [ref=e138]: "[e2e] 请调用工具查询团队成员列表，只回复成员名字"
              - generic [ref=e139]: 17:36
            - generic [ref=e141]: ⚠️ 回复生成失败（模型服务暂不可用或超时），请稍后重试。
            - generic [ref=e147]: test-e2e-alice 正在思考…
          - generic [ref=e149]:
            - button "附件" [ref=e150]:
              - img [ref=e151]
            - textbox "输入消息… 输入 @ 提及成员" [ref=e153]
            - button "发送" [disabled] [ref=e154]:
              - img [ref=e155]
```

# Test source

```ts
  362 | 
  363 |     expectNoConsoleErrors(tracker);
  364 | 
  365 |     // cleanup：取消运行 + 删除 Flow（后者回归：带运行记录的 Flow 删除不再 500）
  366 |     await apiFetch(`/flow-runs/${runId}/cancel`, { method: 'POST' }).catch(() => {});
  367 |     await apiFetch(`/flows/${flow.id}`, { method: 'DELETE' }).catch(() => {});
  368 |     sqliteSweepTestData();
  369 |   });
  370 | });
  371 | 
  372 | /* ═══════════════════════════════════════════════
  373 |    6. 直聊：消息持久化 + 会话状态标签回归
  374 |    ═══════════════════════════════════════════════ */
  375 | 
  376 | test.describe('直聊（消息持久化）', () => {
  377 |   test('发送消息 → 刷新仍在 → 会话标签显示「进行中」', async ({ page }) => {
  378 |     const tracker = trackConsoleErrors(page);
  379 |     const msgText = `[e2e] 聊天持久化 ${Date.now()}`;
  380 | 
  381 |     await page.goto(`/chat/direct/${ALICE}`);
  382 |     await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
  383 | 
  384 |     await page.locator('textarea[placeholder*="输入消息"]').fill(msgText);
  385 |     await page.locator('button[title="发送"]').click();
  386 |     await expect(page.locator('body')).toContainText(msgText, { timeout: 10000 });
  387 | 
  388 |     // 回归：active 会话曾错误显示“已完成”
  389 |     await expect(page.locator('body')).toContainText('进行中');
  390 | 
  391 |     // API 契约：消息已持久化
  392 |     const convs = await apiFetch(`/wakers/${ALICE}/conversations`);
  393 |     expect(convs.length).toBeGreaterThan(0);
  394 |     const msgs = await apiFetch(`/conversations/${convs[0].id}/messages`);
  395 |     expect(msgs.some((m: { content_json: string | null }) => (m.content_json ?? '').includes(msgText))).toBe(true);
  396 |     expectIsoWithTimezone(msgs[0].created_at, 'message.created_at');
  397 | 
  398 |     // waker 回复渲染链路：以 content_json={"text": ...} 存储的 waker 消息必须显示（历史 bug：空白气泡）
  399 |     const wakerText = `[e2e] waker 回复渲染 ${Date.now()}`;
  400 |     await apiFetch(`/conversations/${convs[0].id}/messages`, {
  401 |       method: 'POST',
  402 |       body: JSON.stringify({ role: 'waker', waker_id: ALICE, content_json: { text: wakerText } }),
  403 |     });
  404 | 
  405 |     // 刷新后 user 消息与 waker 回复均仍在（真实持久化 + 渲染）
  406 |     await page.reload();
  407 |     await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
  408 |     await expect(page.locator('body')).toContainText(msgText, { timeout: 10000 });
  409 |     await expect(page.locator('body')).toContainText(wakerText, { timeout: 10000 });
  410 |     await expect(page.locator('div.direct-immersive').first()).toBeVisible();
  411 | 
  412 |     expectNoConsoleErrors(tracker);
  413 |     sqliteSweepTestData();
  414 |   });
  415 | 
  416 |   test('发送消息后 waker 自动回复（真实 DeerFlow run + 前端轮询）', async ({ page }) => {
  417 |     // 独立新建会话，避免与其他用例的会话历史串扰
  418 |     const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
  419 |       method: 'POST',
  420 |       body: JSON.stringify({}),
  421 |     });
  422 | 
  423 |     await page.goto(`/chat/direct/${ALICE}`);
  424 |     await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
  425 |     await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 自动回复验证：收到请回复一句话');
  426 |     await page.locator('button[title="发送"]').click();
  427 | 
  428 |     // 真实链路：后端在 DeerFlow 上发起 waker run，回复异步写回；
  429 |     // 前端轮询（3s）应自动把回复带到页面（无需刷新），此实现验证不可用 mock 替代。
  430 |     await expect(page.locator('div.direct-immersive').last()).toBeVisible({ timeout: 150000 });
  431 | 
  432 |     // API 复核：waker 回复已写入且非空
  433 |     await expect
  434 |       .poll(
  435 |         async () => {
  436 |           const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
  437 |           return msgs.filter(
  438 |             (m: { role: string; content_json: string | null }) =>
  439 |               m.role === 'waker' && (m.content_json ?? '').length > 10,
  440 |           ).length;
  441 |         },
  442 |         { timeout: 30000, message: 'waker 回复应写入会话' },
  443 |       )
  444 |       .toBeGreaterThan(0);
  445 |     sqliteSweepTestData();
  446 |   });
  447 | 
  448 |   test('waker 对话中可调用团队工具获取同事列表（回归 waker_identity 注入）', async ({ page }) => {
  449 |     // 独立新建会话
  450 |     const conv = await apiFetch(`/wakers/${ALICE}/conversations`, {
  451 |       method: 'POST',
  452 |       body: JSON.stringify({}),
  453 |     });
  454 | 
  455 |     await page.goto(`/chat/direct/${ALICE}`);
  456 |     await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
  457 |     await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 请调用工具查询团队成员列表，只回复成员名字');
  458 |     await page.locator('button[title="发送"]').click();
  459 | 
  460 |     // 真实链路：waker run 携 waker_identity 调用 waker-team MCP 工具；
  461 |     // 回归背景：缺失该凭据时 MCP 拦截器 fail-closed，工具被拒（无法获取同事列表）。
> 462 |     await expect
      |     ^ Error: 应收到查同事的 waker 回复
  463 |       .poll(
  464 |         async () => {
  465 |           const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
  466 |           const wakerMsgs = msgs.filter(
  467 |             (m: { role: string; content_json: string | null }) =>
  468 |               m.role === 'waker' && (m.content_json ?? '').length > 10,
  469 |           );
  470 |           return wakerMsgs.length ? (wakerMsgs[wakerMsgs.length - 1].content_json ?? '') : '';
  471 |         },
  472 |         { timeout: 150000, message: '应收到查同事的 waker 回复' },
  473 |       )
  474 |       .toContain(`${P}bob`); // 同事列表应包含 seeded 员工（bob 不可能是执行者 alice 自己）
  475 | 
  476 |     // 回复不得混入模型思考段（<think>）
  477 |     const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
  478 |     const lastWaker = msgs.filter((m: { role: string }) => m.role === 'waker').pop();
  479 |     expect(lastWaker.content_json).not.toContain('</think>');
  480 | 
  481 |     // UI 也应渲染出回复气泡
  482 |     await expect(page.locator('div.direct-immersive').last()).toBeVisible({ timeout: 30000 });
  483 | 
  484 |     sqliteSweepTestData();
  485 |   });
  486 | });
  487 | 
  488 | /* ═══════════════════════════════════════════════
  489 |    7. 群聊：Leader 自动回复（真实链路）
  490 |    ═══════════════════════════════════════════════ */
  491 | 
  492 | test.describe('群聊（真实持久化）', () => {
  493 |   test('发送消息后群 Leader 自动回复（真实 DeerFlow run + 前端轮询）', async ({ page }) => {
  494 |     const groups = await apiFetch('/groups');
  495 |     const g = groups.find((x: { name: string }) => x.name.startsWith(P));
  496 |     expect(g, 'seeded 群组应存在').toBeTruthy();
  497 |     expect(g.leader_waker_id, 'seeded 群组应有 Leader').toBe(ALICE);
  498 | 
  499 |     // 独立新建群会话
  500 |     const conv = await apiFetch(`/groups/${g.id}/conversations`, {
  501 |       method: 'POST',
  502 |       body: JSON.stringify({}),
  503 |     });
  504 | 
  505 |     await page.goto(`/chat/group/${g.id}`);
  506 |     await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
  507 |     await page.locator('textarea[placeholder*="输入消息"]').fill('[e2e] 群聊自动回复验证：Leader 收到请回复');
  508 |     await page.locator('button[title="发送"]').click();
  509 | 
  510 |     // 群聊回复渲染为白色卡片（.msg-waker）；等待其自动出现
  511 |     await expect(page.locator('div.msg-waker').last()).toBeVisible({ timeout: 150000 });
  512 | 
  513 |     // API 复核：Leader（alice）已回复
  514 |     await expect
  515 |       .poll(
  516 |         async () => {
  517 |           const msgs = await apiFetch(`/conversations/${conv.id}/messages`);
  518 |           return msgs.filter((m: { role: string; waker_id: string | null }) => m.role === 'waker').length;
  519 |         },
  520 |         { timeout: 30000, message: '群 Leader 回复应写入会话' },
  521 |       )
  522 |       .toBeGreaterThan(0);
  523 |     sqliteSweepTestData();
  524 |   });
  525 | 
  526 |   test('右侧任务/设置面板默认隐藏，可通过头部按钮展开/收起', async ({ page }) => {
  527 |     const tracker = trackConsoleErrors(page);
  528 |     const groups = await apiFetch('/groups');
  529 |     const g = groups.find((x: { name: string }) => x.name.startsWith(P));
  530 |     expect(g, 'seeded 群组应存在').toBeTruthy();
  531 | 
  532 |     await page.goto(`/chat/group/${g.id}`);
  533 |     await expect(page.locator('textarea[placeholder*="输入消息"]')).toBeVisible({ timeout: 10000 });
  534 | 
  535 |     // 默认隐藏：面板内的「群设置」tab 不应存在
  536 |     await expect(page.getByRole('button', { name: '群设置', exact: true })).not.toBeVisible();
  537 | 
  538 |     // 点击头部按钮展开面板
  539 |     await page.getByRole('button', { name: '任务 / 设置', exact: true }).click();
  540 |     await expect(page.getByRole('button', { name: '群设置', exact: true })).toBeVisible();
  541 |     await expect(page.getByRole('button', { name: '任务', exact: true })).toBeVisible();
  542 | 
  543 |     // 再次点击收起
  544 |     await page.getByRole('button', { name: '任务 / 设置', exact: true }).click();
  545 |     await expect(page.getByRole('button', { name: '群设置', exact: true })).not.toBeVisible();
  546 | 
  547 |     expectNoConsoleErrors(tracker);
  548 |   });
  549 | });
  550 | 
  551 | /* ═══════════════════════════════════════════════
  552 |    7. 侧边栏即时刷新 + 契约断言
  553 |    ═══════════════════════════════════════════════ */
  554 | 
  555 | test.describe('侧边栏与契约', () => {
  556 |   test('创建员工后侧边栏团队列表即时出现（无需整页刷新）', async ({ page }) => {
  557 |     const name = `${P}sidebar-${Date.now()}`;
  558 | 
  559 |     await page.goto('/wakers');
  560 |     await expect(page.locator('text=加载中...')).not.toBeVisible({ timeout: 10000 });
  561 | 
  562 |     await page.getByRole('button', { name: '+ 创建员工' }).click();
```