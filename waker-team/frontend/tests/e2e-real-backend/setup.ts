/**
 * Playwright globalSetup for real-backend E2E tests.
 *
 * Seeds the database with test data via the real API.
 * Writes seeded IDs to a JSON file so the teardown can clean up.
 */

import { seedTestData, sweepTestData, type SeededData } from './helper';
import { writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SEEDED_FILE = join(__dirname, '.seeded-data.json');

export default async function globalSetup() {
  console.log('[real-backend] Sweeping leftover test data...');
  await sweepTestData();
  console.log('[real-backend] Seeding test data...');
  const seeded = await seedTestData();
  writeFileSync(SEEDED_FILE, JSON.stringify(seeded, null, 2));
  console.log(
    `[real-backend] Seeded: ${seeded.wakers.length} wakers, ` +
    `${seeded.groups.length} groups, ${seeded.flows.length} flows, ` +
    `${seeded.schedules.length} schedules, ${seeded.tasks.length} tasks`,
  );
}

export { SEEDED_FILE };
