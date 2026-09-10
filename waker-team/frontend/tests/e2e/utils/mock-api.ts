import type { Page } from '@playwright/test';
import { expect } from '@playwright/test';

/**
 * 【项目规则】有真实环境时禁止使用 mock 数据。
 * 真实后端可达时必须用 pnpm test:e2e（tests/e2e-real-backend）。
 * 本文件仅供无真实环境时的前端渲染 smoke 使用，不得作为契约依据。
 * 修改本文件时请同步真实后端契约（否则会制造“mock 永远通过、真实系统报错”的盲区）。
 */

/* ─── In-memory mock data ─── */

const wakers = [
  {
    name: 'data-collector',
    description: '数据采集专家',
    soul: '你是一个数据采集专家，负责从各种数据源收集信息。',
    model: 'qwen3-flash',
    tool_groups: ['web', 'search'],
    skills: ['web-scraper'],
    max_concurrent_tasks: 3,
    enabled: true,
    deer_user: 'user-1',
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
  },
  {
    name: 'xiaoxi',
    description: '质量数据分析师',
    soul: '你是质量数据分析师，专注于缺陷趋势分析。',
    model: 'qwen3-flash',
    tool_groups: ['web', 'jira'],
    skills: ['jira-scanner'],
    max_concurrent_tasks: 2,
    enabled: true,
    deer_user: 'user-1',
    created_at: '2025-01-02T00:00:00Z',
    updated_at: '2025-01-02T00:00:00Z',
  },
  {
    name: 'zhangweiwei',
    description: '项目管理员',
    soul: '你是项目管理员，负责进度跟踪和风险管控。',
    model: 'qwen3-plus',
    tool_groups: ['web', 'jira', 'git'],
    skills: ['progress-tracker'],
    max_concurrent_tasks: 5,
    enabled: true,
    deer_user: 'user-1',
    created_at: '2025-01-03T00:00:00Z',
    updated_at: '2025-01-03T00:00:00Z',
  },
  {
    name: 'qianduan',
    description: '前端开发',
    soul: '你是前端开发工程师，负责 UI 规约巡检和原型实现。',
    model: 'qwen3-flash',
    tool_groups: ['web', 'code'],
    skills: ['ui-inspector'],
    max_concurrent_tasks: 2,
    enabled: false,
    deer_user: 'user-1',
    created_at: '2025-01-04T00:00:00Z',
    updated_at: '2025-01-04T00:00:00Z',
  },
];

const groups = [
  {
    id: 'deye',
    name: 'D-EYE 项目组',
    leader_waker_id: 'zhangweiwei',
    project_id: 'proj-1',
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
  },
  {
    id: 'quality',
    name: '质量保障组',
    leader_waker_id: 'xiaoxi',
    project_id: 'proj-2',
    created_at: '2025-01-05T00:00:00Z',
    updated_at: '2025-01-05T00:00:00Z',
  },
];

const board = {
  counts: { pending: 2, running: 1, done: 5, failed: 0, total: 8 },
  items: [
    {
      id: 'task-1',
      kind: 'manual',
      group_id: 'deye',
      ticket_id: null,
      parent_task_id: null,
      executor: 'data-collector',
      status: 'done',
      input_text: '采集 D-EYE 6.5 缺陷数据',
      result_summary: '已采集 23 条未关闭 bug',
      created_by: 'admin',
      created_at: '2025-09-09T10:00:00Z',
    },
    {
      id: 'task-2',
      kind: 'schedule',
      group_id: 'deye',
      ticket_id: null,
      parent_task_id: null,
      executor: 'xiaoxi',
      status: 'running',
      input_text: '每日质量看板生成',
      created_by: 'admin',
      created_at: '2025-09-10T01:05:00Z',
    },
  ],
  total: 8,
};

const flows = [
  {
    id: 'flow-1',
    name: '每日质量报告',
    group_id: 'deye',
    description: '自动采集缺陷数据并生成质量报告',
    version: 1,
    definition_json: {
      version: 1,
      nodes: [
        { key: 'collect', type: 'waker_task', waker: 'data-collector', instruction: '采集数据', depends_on: [] },
        { key: 'analyze', type: 'waker_task', waker: 'xiaoxi', instruction: '分析数据', depends_on: ['collect'] },
      ],
    },
    status: 'active',
    created_at: '2025-01-10T00:00:00Z',
    updated_at: '2025-01-10T00:00:00Z',
    created_by: 'admin',
  },
  {
    id: 'flow-2',
    name: '停滞单扫描流程',
    group_id: 'deye',
    description: '扫描 Jira 停滞单并推送通知',
    version: 1,
    definition_json: {
      version: 1,
      nodes: [
        { key: 'scan', type: 'waker_task', waker: 'zhangweiwei', instruction: '扫描停滞单', depends_on: [] },
        { key: 'notify', type: 'notify', instruction: '推送通知', depends_on: ['scan'], channel: 'feishu' },
      ],
    },
    status: 'active',
    created_at: '2025-01-12T00:00:00Z',
    updated_at: '2025-01-12T00:00:00Z',
    created_by: 'admin',
  },
];

const schedules = [
  {
    id: 'sched-1',
    name: '每日质量看板',
    group_id: 'deye',
    description: '每天凌晨 1 点生成质量看板',
    cron_expression: '0 1 * * *',
    target_type: 'flow',
    target_id: 'flow-1',
    status: 'active',
    last_run_at: '2025-09-10T01:00:00Z',
    next_run_at: '2025-09-11T01:00:00Z',
    run_count: 42,
    created_at: '2025-01-10T00:00:00Z',
    updated_at: '2025-01-10T00:00:00Z',
  },
  {
    id: 'sched-2',
    name: 'Jira 异常单扫描',
    group_id: 'deye',
    description: '每天凌晨 2 点扫描新增 bug',
    cron_expression: '0 2 * * *',
    target_type: 'task',
    target_id: 'task-scan',
    status: 'active',
    last_run_at: '2025-09-10T02:00:00Z',
    next_run_at: '2025-09-11T02:00:00Z',
    run_count: 30,
    created_at: '2025-01-15T00:00:00Z',
    updated_at: '2025-01-15T00:00:00Z',
  },
];

const enumData = {
  models: [
    { name: 'qwen3-flash', display_name: 'Qwen3 Flash' },
    { name: 'qwen3-plus', display_name: 'Qwen3 Plus' },
  ],
  skills: [
    { name: 'web-scraper' },
    { name: 'jira-scanner' },
    { name: 'progress-tracker' },
    { name: 'ui-inspector' },
  ],
  tool_groups: ['web', 'search', 'jira', 'git', 'code'],
};

const wakerTemplates = [
  {
    id: 'frontend-engineer',
    title: '前端工程师',
    description: '负责 Web 前端页面/组件开发、UI 还原与前端工程化',
    suggested_name: 'frontend-dev',
    role: '前端工程师',
    soul: '你是一名前端工程师。专注于 Web 页面与组件开发。',
    model: null,
    tool_groups: ['web', 'code'],
    skills: ['ui-inspector'],
    max_concurrent_tasks: 3,
    mcp_connectors: { 'browser-use': {} },
  },
  {
    id: 'backend-engineer',
    title: '后端工程师',
    description: '负责后端服务、API 与数据层开发及性能稳定性',
    suggested_name: 'backend-dev',
    role: '后端工程师',
    soul: '你是一名后端工程师。专注于服务端接口与数据层。',
    model: null,
    tool_groups: ['web', 'code'],
    skills: ['jira-scanner'],
    max_concurrent_tasks: 3,
    mcp_connectors: {},
  },
  {
    id: 'qa-engineer',
    title: '测试工程师',
    description: '负责测试方案设计、用例编写与质量闸门把关',
    suggested_name: 'qa-engineer',
    role: '测试工程师',
    soul: '你是一名测试工程师。专注于测试方案与质量把关。',
    model: null,
    tool_groups: ['web'],
    skills: [],
    max_concurrent_tasks: 3,
  },
];

const settings = {
  compact_mode: false,
  notification_enabled: true,
  notification_sound: true,
  default_model: 'qwen3-flash',
};

/* ─── Mock API handler ─── */

export async function mockWakerTeamAPI(page: Page) {
  // Clone data for CRUD isolation per test
  const wakersData = structuredClone(wakers);
  const groupsData = structuredClone(groups);
  const flowsData = structuredClone(flows);
  const schedulesData = structuredClone(schedules);
  const settingsData = structuredClone(settings);

  // ─── Conversations（与新 UI 契约对齐：DirectChat/GroupChat 依赖这些端点）───
  const conversationsData: Array<{
    id: string; scope: string; waker_id: string | null; group_id: string | null;
    title: string | null; status: string; thread_id: string | null;
    created_by: string | null; created_at: string | null; updated_at: string | null;
  }> = [
    {
      id: 'conv-xiaoxi-1', scope: 'direct', waker_id: 'xiaoxi', group_id: null,
      title: '质量数据周报', status: 'active', thread_id: null,
      created_by: 'admin', created_at: '2025-01-05T02:00:00Z', updated_at: '2025-01-05T02:00:00Z',
    },
    {
      id: 'conv-deye-1', scope: 'group', waker_id: null, group_id: 'deye',
      title: 'D-EYE 协作会话', status: 'active', thread_id: null,
      created_by: 'admin', created_at: '2025-01-06T02:00:00Z', updated_at: '2025-01-06T02:00:00Z',
    },
  ];
  const messagesData: Record<string, Array<{
    id: string; conversation_id: string; role: string; waker_id: string | null;
    content_json: string | null; created_at: string | null;
  }>> = {
    'conv-xiaoxi-1': [
      { id: 'msg-1', conversation_id: 'conv-xiaoxi-1', role: 'user', waker_id: null, content_json: JSON.stringify({ text: '帮我完善一下小析的周报数据' }), created_at: '2025-01-05T02:01:00Z' },
      { id: 'msg-2', conversation_id: 'conv-xiaoxi-1', role: 'waker', waker_id: 'xiaoxi', content_json: JSON.stringify({ text: '好的，我来完善一下小析的周报数据。' }), created_at: '2025-01-05T02:02:00Z' },
    ],
    'conv-deye-1': [
      { id: 'msg-3', conversation_id: 'conv-deye-1', role: 'user', waker_id: null, content_json: JSON.stringify({ text: '定制我的协作团队' }), created_at: '2025-01-06T02:01:00Z' },
    ],
  };
  let convSeq = 100;
  let msgSeq = 100;

  await page.route('**/api/**', async (route) => {
    // Skip Vite module requests that happen to contain /api/ in the path
    const reqUrl = route.request().url();
    if (reqUrl.includes('/src/api/') || reqUrl.includes('/node_modules/')) {
      return route.continue();
    }
    const url = new URL(reqUrl);
    const path = url.pathname.replace('/api', '');
    const method = route.request().method();

    // ─── Header validation: catch Content-Type bugs ───
    // POST/PUT/PATCH requests must include Content-Type: application/json.
    // This assertion would have caught the fetchJSON spread-order bug where
    // Content-Type was overwritten by undefined.
    if (method === 'POST' || method === 'PUT' || method === 'PATCH') {
      const contentType = route.request().headers()['content-type'];
      expect(contentType, `Missing Content-Type: application/json on ${method} ${path}`).toContain('application/json');
    }

    // Health
    if (path === '/health' && method === 'GET') {
      return route.fulfill({ json: { status: 'ok' } });
    }

    // Enum data
    if (path === '/wakers/enum' && method === 'GET') {
      return route.fulfill({ json: enumData });
    }

    // Waker templates（必须在 /wakers/{name} 匹配之前处理）
    if (path === '/wakers/templates' && method === 'GET') {
      return route.fulfill({ json: wakerTemplates });
    }

    // Wakers list
    if (path === '/wakers' && method === 'GET') {
      return route.fulfill({ json: wakersData });
    }

    // Create waker
    if (path === '/wakers' && method === 'POST') {
      const body = route.request().postDataJSON();
      const newWaker = {
        ...body,
        enabled: body.enabled ?? true,
        deer_user: 'user-1',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      wakersData.push(newWaker);
      return route.fulfill({ json: newWaker });
    }

    // Waker detail
    const wakerMatch = path.match(/^\/wakers\/([^/]+)$/);
    if (wakerMatch) {
      const name = decodeURIComponent(wakerMatch[1]);
      if (method === 'GET') {
        const w = wakersData.find((w) => w.name === name);
        return w
          ? route.fulfill({ json: w })
          : route.fulfill({ status: 404, json: { detail: 'Not found' } });
      }
      if (method === 'PUT') {
        const idx = wakersData.findIndex((w) => w.name === name);
        if (idx === -1) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
        const body = route.request().postDataJSON();
        Object.assign(wakersData[idx], body);
        return route.fulfill({ json: wakersData[idx] });
      }
      if (method === 'DELETE') {
        const idx = wakersData.findIndex((w) => w.name === name);
        if (idx !== -1) wakersData.splice(idx, 1);
        return route.fulfill({ status: 204 });
      }
    }

    // Waker toggle
    const toggleMatch = path.match(/^\/wakers\/([^/]+)\/toggle$/);
    if (toggleMatch && method === 'PATCH') {
      const name = decodeURIComponent(toggleMatch[1]);
      const w = wakersData.find((w) => w.name === name);
      if (!w) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
      const body = route.request().postDataJSON();
      w.enabled = body.enabled;
      return route.fulfill({ json: w });
    }

    // Groups list
    if (path === '/groups' && method === 'GET') {
      return route.fulfill({ json: groupsData });
    }

    // Create group
    if (path === '/groups' && method === 'POST') {
      const body = route.request().postDataJSON();
      const newGroup = {
        id: `group-${Date.now()}`,
        name: body.name,
        leader_waker_id: body.leader_waker_id || null,
        project_id: body.project_id || null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      groupsData.push(newGroup);
      return route.fulfill({ json: newGroup });
    }

    // Group detail
    const groupMatch = path.match(/^\/groups\/([^/]+)$/);
    if (groupMatch) {
      const id = groupMatch[1];
      if (method === 'GET') {
        const g = groupsData.find((g) => g.id === id);
        return g
          ? route.fulfill({ json: g })
          : route.fulfill({ status: 404, json: { detail: 'Not found' } });
      }
      if (method === 'PUT') {
        const idx = groupsData.findIndex((g) => g.id === id);
        if (idx === -1) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
        const body = route.request().postDataJSON();
        Object.assign(groupsData[idx], body, { updated_at: new Date().toISOString() });
        return route.fulfill({ json: groupsData[idx] });
      }
      if (method === 'DELETE') {
        const idx = groupsData.findIndex((g) => g.id === id);
        if (idx !== -1) groupsData.splice(idx, 1);
        return route.fulfill({ status: 204 });
      }
    }

    // Group members
    const membersMatch = path.match(/^\/groups\/([^/]+)\/members$/);
    if (membersMatch) {
      if (method === 'GET') {
        return route.fulfill({
          json: [
            { group_id: membersMatch[1], waker_id: 'zhangweiwei', role: 'leader', joined_at: '2025-01-01T00:00:00Z' },
            { group_id: membersMatch[1], waker_id: 'xiaoxi', role: 'member', joined_at: '2025-01-02T00:00:00Z' },
          ],
        });
      }
      if (method === 'POST') {
        const body = route.request().postDataJSON();
        return route.fulfill({
          json: { group_id: membersMatch[1], waker_id: body.waker_id, role: body.role || 'member', joined_at: new Date().toISOString() },
        });
      }
    }

    // Remove group member
    const removeMemberMatch = path.match(/^\/groups\/([^/]+)\/members\/([^/]+)$/);
    if (removeMemberMatch && method === 'DELETE') {
      return route.fulfill({ status: 204 });
    }

    // Board
    if (path === '/board' && method === 'GET') {
      return route.fulfill({ json: board });
    }

    // Flows list
    if (path === '/flows' && method === 'GET') {
      return route.fulfill({ json: flowsData });
    }

    // Create flow
    if (path === '/flows' && method === 'POST') {
      const body = route.request().postDataJSON();
      const newFlow = {
        id: `flow-${Date.now()}`,
        name: body.name,
        group_id: body.group_id,
        description: body.description || null,
        version: 1,
        definition_json: body.definition_json || { version: 1, nodes: [] },
        status: 'active',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        created_by: 'admin',
      };
      flowsData.push(newFlow);
      return route.fulfill({ json: newFlow });
    }

    // Flow detail
    const flowMatch = path.match(/^\/flows\/([^/]+)$/);
    if (flowMatch) {
      const id = flowMatch[1];
      if (method === 'GET') {
        const f = flowsData.find((f) => f.id === id);
        return f
          ? route.fulfill({ json: f })
          : route.fulfill({ status: 404, json: { detail: 'Not found' } });
      }
      if (method === 'PUT') {
        const idx = flowsData.findIndex((f) => f.id === id);
        if (idx === -1) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
        const body = route.request().postDataJSON();
        Object.assign(flowsData[idx], body, { updated_at: new Date().toISOString() });
        return route.fulfill({ json: flowsData[idx] });
      }
      if (method === 'DELETE') {
        const idx = flowsData.findIndex((f) => f.id === id);
        if (idx !== -1) flowsData.splice(idx, 1);
        return route.fulfill({ status: 204 });
      }
    }

    // Schedules list
    if (path === '/schedules' && method === 'GET') {
      return route.fulfill({ json: schedulesData });
    }

    // Create schedule
    if (path === '/schedules' && method === 'POST') {
      const body = route.request().postDataJSON();
      const newSched = {
        id: `sched-${Date.now()}`,
        name: body.name,
        group_id: body.group_id || null,
        description: body.description || null,
        cron_expression: body.cron_expression,
        target_type: body.target_type,
        target_id: body.target_id,
        status: 'active',
        last_run_at: null,
        next_run_at: '2025-09-11T01:00:00Z',
        run_count: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      schedulesData.push(newSched);
      return route.fulfill({ json: newSched });
    }

    // Schedule detail
    const schedMatch = path.match(/^\/schedules\/([^/]+)$/);
    if (schedMatch) {
      const id = schedMatch[1];
      if (method === 'GET') {
        const s = schedulesData.find((s) => s.id === id);
        return s
          ? route.fulfill({ json: s })
          : route.fulfill({ status: 404, json: { detail: 'Not found' } });
      }
      if (method === 'PUT') {
        const idx = schedulesData.findIndex((s) => s.id === id);
        if (idx === -1) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
        const body = route.request().postDataJSON();
        Object.assign(schedulesData[idx], body, { updated_at: new Date().toISOString() });
        return route.fulfill({ json: schedulesData[idx] });
      }
      if (method === 'DELETE') {
        const idx = schedulesData.findIndex((s) => s.id === id);
        if (idx !== -1) schedulesData.splice(idx, 1);
        return route.fulfill({ status: 204 });
      }
    }

    // ─── Conversations（与真实后端契约一致）───
    const wakerConvsMatch = path.match(/^\/wakers\/([^/]+)\/conversations$/);
    if (wakerConvsMatch) {
      const wakerName = decodeURIComponent(wakerConvsMatch[1]);
      if (method === 'GET') {
        return route.fulfill({ json: conversationsData.filter((c) => c.waker_id === wakerName) });
      }
      if (method === 'POST') {
        const body = route.request().postDataJSON();
        const conv = {
          id: `conv-${convSeq++}`, scope: 'direct', waker_id: wakerName, group_id: null,
          title: body?.title ?? null, status: 'active', thread_id: null,
          created_by: 'admin', created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        };
        conversationsData.push(conv);
        return route.fulfill({ status: 201, json: conv });
      }
    }
    const groupConvsMatch = path.match(/^\/groups\/([^/]+)\/conversations$/);
    if (groupConvsMatch) {
      const groupId = decodeURIComponent(groupConvsMatch[1]);
      if (method === 'GET') {
        return route.fulfill({ json: conversationsData.filter((c) => c.group_id === groupId) });
      }
      if (method === 'POST') {
        const body = route.request().postDataJSON();
        const conv = {
          id: `conv-${convSeq++}`, scope: 'group', waker_id: null, group_id: groupId,
          title: body?.title ?? null, status: 'active', thread_id: null,
          created_by: 'admin', created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        };
        conversationsData.push(conv);
        return route.fulfill({ status: 201, json: conv });
      }
    }
    const convMsgsMatch = path.match(/^\/conversations\/([^/]+)\/messages$/);
    if (convMsgsMatch) {
      const convId = decodeURIComponent(convMsgsMatch[1]);
      if (method === 'GET') {
        return route.fulfill({ json: messagesData[convId] ?? [] });
      }
      if (method === 'POST') {
        const body = route.request().postDataJSON();
        const msg = {
          id: `msg-${msgSeq++}`, conversation_id: convId, role: body?.role ?? 'user',
          waker_id: body?.waker_id ?? null,
          content_json: body?.content_json ? JSON.stringify(body.content_json) : null,
          created_at: new Date().toISOString(),
        };
        if (!messagesData[convId]) messagesData[convId] = [];
        messagesData[convId].push(msg);
        return route.fulfill({ status: 201, json: msg });
      }
    }

    // Schedule actions (pause/resume/trigger)
    const schedPauseMatch = path.match(/^\/schedules\/([^/]+)\/pause$/);
    if (schedPauseMatch && method === 'POST') {
      const s = schedulesData.find((s) => s.id === schedPauseMatch[1]);
      if (!s) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
      s.status = 'paused';
      return route.fulfill({ json: s });
    }
    const schedResumeMatch = path.match(/^\/schedules\/([^/]+)\/resume$/);
    if (schedResumeMatch && method === 'POST') {
      const s = schedulesData.find((s) => s.id === schedResumeMatch[1]);
      if (!s) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
      s.status = 'active';
      return route.fulfill({ json: s });
    }
    const schedTriggerMatch = path.match(/^\/schedules\/([^/]+)\/trigger$/);
    if (schedTriggerMatch && method === 'POST') {
      return route.fulfill({ json: { id: `run-${Date.now()}`, status: 'pending', trigger_type: 'manual', triggered_at: new Date().toISOString() } });
    }

    // Settings
    if (path === '/settings' && method === 'GET') {
      return route.fulfill({ json: settingsData });
    }
    if (path === '/settings' && method === 'PUT') {
      const body = route.request().postDataJSON();
      Object.assign(settingsData, body);
      return route.fulfill({ json: settingsData });
    }

    // Tasks
    if (path === '/tasks' && method === 'POST') {
      const body = route.request().postDataJSON();
      return route.fulfill({
        json: {
          id: `task-${Date.now()}`,
          kind: 'manual',
          group_id: body.group_id || 'deye',
          ticket_id: null,
          parent_task_id: null,
          executor: body.executor,
          status: 'pending',
          input_text: body.input_text,
          created_by: 'admin',
          created_at: new Date().toISOString(),
        },
      });
    }

    // Task detail
    const taskMatch = path.match(/^\/tasks\/([^/]+)$/);
    if (taskMatch && method === 'GET') {
      const id = taskMatch[1];
      const t = board.items.find((t) => t.id === id);
      return t
        ? route.fulfill({ json: t })
        : route.fulfill({ json: { id, kind: 'manual', group_id: 'deye', ticket_id: null, parent_task_id: null, executor: 'data-collector', status: 'pending', input_text: 'Mock task', created_by: 'admin', created_at: new Date().toISOString() } });
    }

    // Task retry
    const taskRetryMatch = path.match(/^\/tasks\/([^/]+)\/retry$/);
    if (taskRetryMatch && method === 'POST') {
      return route.fulfill({ json: { id: taskRetryMatch[1], status: 'pending' } });
    }

    // Task cancel
    const taskCancelMatch = path.match(/^\/tasks\/([^/]+)\/cancel$/);
    if (taskCancelMatch && method === 'POST') {
      return route.fulfill({ json: { id: taskCancelMatch[1], status: 'cancelled' } });
    }

    // Flow runs
    if (path === '/flow-runs' && method === 'GET') {
      return route.fulfill({ json: [] });
    }

    // Schedule runs
    const schedRunsMatch = path.match(/^\/schedules\/([^/]+)\/runs$/);
    if (schedRunsMatch && method === 'GET') {
      return route.fulfill({ json: [] });
    }

    // Fallback: return 404 for unmatched routes
    return route.fulfill({ status: 404, json: { detail: `Mock: no handler for ${method} ${path}` } });
  });
}
