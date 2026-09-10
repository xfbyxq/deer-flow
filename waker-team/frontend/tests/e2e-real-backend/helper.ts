/**
 * Real-backend E2E helpers — 真实系统测试工具集。
 *
 * 后端运行在 localhost:8000（真实 SQLite + 真实 DeerFlow 网关）。
 *
 * 设计原则（吸取 mock E2E 漏检 bug 的教训）：
 * 1. 所有测试数据使用 test-e2e- 前缀，配套前置清扫 + 收尾清理，杜绝残留累积；
 * 2. 断言真实契约：HTTP 状态、响应体字段、时间戳时区、持久化（刷新后仍在）；
 * 3. 每个用例校验页面无 console error（React key 警告一类问题会被捕获）；
 * 4. tasks / conversations / runs 等没有删除 API 的数据用 SQLite 兜底清理。
 */

import { expect, type Page } from '@playwright/test';
import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const API_BASE = 'http://localhost:8000/api';
const TEST_PREFIX = 'test-e2e-';
/** waker-team/waker_team.db（E2E 后端同一数据库） */
const DB_PATH = join(__dirname, '..', '..', '..', 'waker_team.db');

// ─── Types ───

interface CreatedWaker {
  name: string;
}
interface CreatedGroup {
  id: string;
  name: string;
}
interface CreatedFlow {
  id: string;
  name: string;
}
interface CreatedSchedule {
  id: string;
  name: string;
}
interface CreatedTask {
  id: string;
}

export interface SeededData {
  wakers: CreatedWaker[];
  groups: CreatedGroup[];
  flows: CreatedFlow[];
  schedules: CreatedSchedule[];
  tasks: CreatedTask[];
}

// ─── API helpers ───

async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok && res.status !== 204) {
    const text = await res.text().catch(() => '');
    throw new Error(`API ${init?.method ?? 'GET'} ${path} → ${res.status}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

async function apiDelete(path: string): Promise<void> {
  try {
    await apiFetch(path, { method: 'DELETE' });
  } catch {
    /* ignore */
  }
}

// ─── SQLite 兜底清理（tasks / conversations / runs 无删除 API）───

// 清扫匹配规则：本仓库所有 E2E/测试数据约定为 test-* 前缀或 [e2e]/E2E/Contract 前缀的输入文本
const SWEEP_PREFIX = 'test-';

export function sqliteSweepTestData(): void {
  if (!existsSync(DB_PATH)) return;
  const db = new DatabaseSync(DB_PATH);
  try {
    db.exec('PRAGMA busy_timeout=30000');
    db.exec(`
      DELETE FROM conversation_messages WHERE conversation_id IN (
        SELECT id FROM conversations
        WHERE waker_id LIKE '${SWEEP_PREFIX}%'
           OR COALESCE(group_id, '') LIKE '${SWEEP_PREFIX}%'
           OR group_id IN (SELECT id FROM groups WHERE name LIKE '${SWEEP_PREFIX}%')
      );
      DELETE FROM conversation_messages WHERE waker_id LIKE '${SWEEP_PREFIX}%';
      DELETE FROM conversations
        WHERE waker_id LIKE '${SWEEP_PREFIX}%'
           OR COALESCE(group_id, '') LIKE '${SWEEP_PREFIX}%'
           OR group_id IN (SELECT id FROM groups WHERE name LIKE '${SWEEP_PREFIX}%');
      DELETE FROM schedule_runs WHERE schedule_def_id IN (
        SELECT id FROM schedule_defs WHERE name LIKE '${SWEEP_PREFIX}%'
      );
      DELETE FROM tasks
        WHERE executor LIKE '${SWEEP_PREFIX}%'
           OR input_text LIKE '[e2e]%'
           OR input_text LIKE 'E2E测试%'
           OR input_text LIKE 'Contract test%';
      DELETE FROM node_runs WHERE flow_run_id IN (
        SELECT id FROM flow_runs WHERE flow_def_id IN (
          SELECT id FROM flow_defs WHERE name LIKE '${SWEEP_PREFIX}%'
        )
      );
      DELETE FROM flow_runs WHERE flow_def_id IN (
        SELECT id FROM flow_defs WHERE name LIKE '${SWEEP_PREFIX}%'
      );
    `);
  } finally {
    db.close();
  }
}

/** 前置/收尾清扫：按前缀删除所有 E2E 残留（API + SQLite 双层） */
export async function sweepTestData(): Promise<void> {
  // 先做 SQLite 清扫（任务 cancel 状态与运行记录），避免后续 API 删除
  // 因 running task 检查（409）或外键约束失败而留下残留
  sqliteSweepTestData();

  const safeList = async <T>(path: string): Promise<T[]> => {
    try {
      return (await apiFetch(path)) as T[];
    } catch {
      return [];
    }
  };

  const schedules = await safeList<{ id: string; name: string }>('/schedules');
  for (const s of schedules) {
    if (s.name.startsWith('test-')) await apiDelete(`/schedules/${s.id}`);
  }
  const flows = await safeList<{ id: string; name: string }>('/flows');
  for (const f of flows) {
    if (f.name.startsWith('test-')) await apiDelete(`/flows/${f.id}`);
  }
  const groups = await safeList<{ id: string; name: string }>('/groups');
  for (const g of groups) {
    if (g.id.startsWith('test-') || g.name.startsWith('test-')) {
      await apiDelete(`/groups/${g.id}`);
    }
  }
  const wakers = await safeList<{ name: string }>('/wakers');
  for (const w of wakers) {
    if (w.name.startsWith('test-')) await apiDelete(`/wakers/${w.name}`);
  }
  sqliteSweepTestData();
}

// ─── Seed ───

export async function seedTestData(): Promise<SeededData> {
  const seeded: SeededData = { wakers: [], groups: [], flows: [], schedules: [], tasks: [] };

  // 1. Create 3 wakers
  const wakerDefs = [
    { name: `${TEST_PREFIX}alice`, description: 'E2E测试员工Alice', soul: '你是测试助手Alice。', model: null, tool_groups: ['web'], skills: [], max_concurrent_tasks: 3 },
    { name: `${TEST_PREFIX}bob`, description: 'E2E测试员工Bob', soul: '你是测试助手Bob。', model: null, tool_groups: ['web', 'sandbox'], skills: [], max_concurrent_tasks: 2 },
    { name: `${TEST_PREFIX}charlie`, description: 'E2E测试员工Charlie', soul: '你是测试助手Charlie。', model: null, tool_groups: ['web'], skills: [], max_concurrent_tasks: 5 },
  ];
  for (const w of wakerDefs) {
    try {
      const resp = await apiFetch('/wakers', { method: 'POST', body: JSON.stringify(w) });
      seeded.wakers.push({ name: resp.name });
    } catch (e) {
      // 409 = already exists, that's fine
      if (!(e as Error).message.includes('409')) throw e;
      seeded.wakers.push({ name: w.name });
    }
  }

  // 2. Create 1 group with leader
  try {
    const group = await apiFetch('/groups', {
      method: 'POST',
      body: JSON.stringify({
        name: `${TEST_PREFIX}测试群组`,
        leader_waker_id: `${TEST_PREFIX}alice`,
        project_id: 'e2e-proj',
      }),
    });
    seeded.groups.push({ id: group.id, name: group.name });
  } catch (e) {
    if (!(e as Error).message.includes('409')) throw e;
  }

  // Also ensure a group named "default" exists (needed for task dispatch).
  // 先查询避免重复创建同名 UUID 组（历史 bug：每次 seed 都新建一个 default 命名组）
  try {
    const existingGroups = (await apiFetch('/groups')) as Array<{ id: string; name: string }>;
    if (!existingGroups.some((g) => g.name === 'default')) {
      const defaultGroup = await apiFetch('/groups', {
        method: 'POST',
        body: JSON.stringify({ name: 'default', leader_waker_id: null }),
      });
      // Track for cleanup only if we created it
      if (defaultGroup.id) {
        seeded.groups.push({ id: defaultGroup.id, name: defaultGroup.name });
      }
    }
  } catch {
    // Already exists — fine
  }

  // 3. Add members to the test group
  const groupId = seeded.groups[0]?.id;
  if (groupId) {
    for (const member of [`${TEST_PREFIX}bob`, `${TEST_PREFIX}charlie`]) {
      try {
        await apiFetch(`/groups/${groupId}/members`, {
          method: 'POST',
          body: JSON.stringify({ waker_id: member, role: 'member' }),
        });
      } catch {
        // Already member — fine
      }
    }
  }

  // 4. Create 2 tasks
  for (const taskDef of [
    { executor: `${TEST_PREFIX}alice`, input_text: '[e2e] seed 任务 1：数据采集', group_id: groupId || 'default' },
    { executor: `${TEST_PREFIX}bob`, input_text: '[e2e] seed 任务 2：数据分析', group_id: groupId || 'default' },
  ]) {
    try {
      const task = await apiFetch('/tasks', { method: 'POST', body: JSON.stringify(taskDef) });
      seeded.tasks.push({ id: task.id });
    } catch {
      // Task creation may fail if DeerFlow is unavailable — tests will adapt
    }
  }

  // 5. Create 1 flow
  try {
    const flow = await apiFetch('/flows', {
      method: 'POST',
      body: JSON.stringify({
        name: `${TEST_PREFIX}测试流程`,
        group_id: groupId || 'default',
        description: 'E2E自动化测试流程',
        definition_json: {
          version: 1,
          nodes: [
            { key: 'step1', type: 'waker_task', waker: `${TEST_PREFIX}alice`, instruction: '执行测试步骤1', depends_on: [] },
            { key: 'step2', type: 'waker_task', waker: `${TEST_PREFIX}bob`, instruction: '执行测试步骤2', depends_on: ['step1'] },
          ],
        },
      }),
    });
    seeded.flows.push({ id: flow.id, name: flow.name });
  } catch {
    // Flow creation may fail — tests will skip
  }

  // 6. Create 1 schedule
  if (seeded.flows.length > 0) {
    try {
      const sched = await apiFetch('/schedules', {
        method: 'POST',
        body: JSON.stringify({
          name: `${TEST_PREFIX}测试调度`,
          group_id: groupId || 'default',
          description: 'E2E自动化测试调度',
          cron_expression: '0 3 * * *',
          target_type: 'flow',
          target_id: seeded.flows[0].id,
        }),
      });
      seeded.schedules.push({ id: sched.id, name: sched.name });
    } catch {
      // Schedule creation may fail — tests will skip
    }
  }

  return seeded;
}

// ─── Cleanup ───

export async function cleanupTestData(seeded: SeededData): Promise<void> {
  // 删除顺序：调度 → Flow → 任务 → 群组 → 员工 → SQLite 兜底

  for (const s of seeded.schedules) {
    await apiDelete(`/schedules/${s.id}`);
  }

  for (const f of seeded.flows) {
    await apiDelete(`/flows/${f.id}`);
  }

  // Tasks：cancel 后由 sqliteSweepTestData 最终清除（cancel 为 POST 端点）
  for (const t of seeded.tasks) {
    try {
      await apiFetch(`/tasks/${t.id}/cancel`, { method: 'POST' });
    } catch {
      /* ignore */
    }
  }

  // Groups — remove members first, then delete
  for (const g of seeded.groups) {
    // Don't delete the "default" group — it may be needed by the system
    if (g.id === 'default') continue;
    try {
      const members = (await apiFetch(`/groups/${g.id}/members`)) as Array<{ waker_id: string }>;
      if (Array.isArray(members)) {
        for (const m of members) {
          await apiDelete(`/groups/${g.id}/members/${m.waker_id}`);
        }
      }
    } catch {
      /* ignore */
    }
    await apiDelete(`/groups/${g.id}`);
  }

  // Wakers last
  for (const w of seeded.wakers) {
    await apiDelete(`/wakers/${w.name}`);
  }

  // SQLite 兜底（将 tasks/conversations/runs 全部清扫干净）
  sqliteSweepTestData();
}

// ─── Console error tracking ───

export interface ConsoleTracker {
  errors: string[];
  failedRequests: string[];
}

const CONSOLE_ALLOWLIST: RegExp[] = [
  /favicon/i,
  /Download the React DevTools/i,
  /\[vite\]/i,
  /ResizeObserver loop/i,
];
/** 开始收集页面 console error / pageerror / 失败请求（断言时一并输出，便于定位 404 源） */
export function trackConsoleErrors(page: Page): ConsoleTracker {
  const tracker: ConsoleTracker = { errors: [], failedRequests: [] };
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const loc = msg.location();
      tracker.errors.push(`${msg.text()} @ ${loc.url}:${loc.lineNumber}`);
    }
  });
  page.on('pageerror', (err) => {
    tracker.errors.push(`pageerror: ${err.message}`);
  });
  page.on('response', (res) => {
    if (res.status() >= 400) {
      tracker.failedRequests.push(
        `${res.request().method()} ${res.url()} → ${res.status()}`,
      );
    }
  });
  return tracker;
}

export function expectNoConsoleErrors(tracker: ConsoleTracker): void {
  const unexpected = tracker.errors.filter(
    (e) => !CONSOLE_ALLOWLIST.some((re) => re.test(e)) && !isBenignStatic404(e),
  );
  expect(
    unexpected,
    `页面出现非预期 console error：\n${unexpected.join('\n---\n')}\n` +
      (tracker.failedRequests.length
        ? `失败请求：\n${tracker.failedRequests.join('\n')}`
        : '(无失败请求)'),
  ).toEqual([]);
}

/**
 * dev-server 静态资源 404（vite 按需编译/HMR 期间的资源抖动）不属于产品问题；
 * 但指向 /api/ 的 404 一律视为真实契约问题，必须报错。
 */
function isBenignStatic404(entry: string): boolean {
  if (!/Failed to load resource.*404/i.test(entry)) return false;
  const match = entry.match(/@\s+(https?:\/\/\S+?)(?::\d+)?$/);
  const url = match?.[1] ?? '';
  return url !== '' && !url.includes('/api/');
}

// ─── Contract assertions ───

/** 断言 ISO 时间戳携带时区信息（防 naive-UTC 导致的显示偏移回归） */
export function expectIsoWithTimezone(value: string | null | undefined, label = 'timestamp'): void {
  expect(value, `${label} 应存在`).toBeTruthy();
  expect(value!, `${label} 应带时区后缀，实际: ${value}`).toMatch(/(Z|[+-]\d{2}:\d{2})$/);
}

// ─── Utility ───

export function getApiBase(): string {
  return API_BASE;
}

export function getTestPrefix(): string {
  return TEST_PREFIX;
}

export function getDbPath(): string {
  return DB_PATH;
}

export { apiFetch };
