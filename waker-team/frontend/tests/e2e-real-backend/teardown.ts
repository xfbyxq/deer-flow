/**
 * Playwright globalTeardown for real-backend E2E tests.
 *
 * 1. 删除本次 seed 的数据（cleanupTestData）
 * 2. 按前缀全量清扫用例运行期间创建的其它产物（sweepTestData，含未登记的 waker/调度等）
 */

import { cleanupTestData, sweepTestData, type SeededData } from './helper';
import { existsSync, readFileSync, unlinkSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SEEDED_FILE = join(__dirname, '.seeded-data.json');

export default async function globalTeardown() {
  console.log('[real-backend] Cleaning up test data...');
  if (existsSync(SEEDED_FILE)) {
    try {
      const seeded: SeededData = JSON.parse(readFileSync(SEEDED_FILE, 'utf-8'));
      await cleanupTestData(seeded);
      await sweepTestData();
      console.log('[real-backend] Cleanup complete.');
    } catch (e) {
      console.warn('[real-backend] Cleanup error:', e);
    }
    try { unlinkSync(SEEDED_FILE); } catch { /* ignore */ }
  } else {
    console.log('[real-backend] No seed file found, skipping cleanup.');
  }
}
