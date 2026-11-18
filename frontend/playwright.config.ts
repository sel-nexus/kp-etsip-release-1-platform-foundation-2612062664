import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const frontendDir = path.dirname(fileURLToPath(import.meta.url));

// Each invocation owns its MongoDB database; remove it manually after the run.
// E2E_MONGODB_DB, if supplied, must name a fresh disposable database.
const frontend = new URL(process.env.E2E_BASE_URL || 'http://127.0.0.1:5173');
if (frontend.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(frontend.hostname) || !frontend.port || frontend.pathname !== '/' || frontend.search || frontend.hash) {
  throw new Error('E2E_BASE_URL must be a local HTTP origin with an explicit port');
}
const backendPort = Number(process.env.E2E_BACKEND_PORT || '8000');
if (!Number.isInteger(backendPort) || backendPort < 1 || backendPort > 65535 || backendPort === Number(frontend.port)) {
  throw new Error('E2E_BACKEND_PORT must be a valid port distinct from the frontend port');
}
const backend = `http://127.0.0.1:${backendPort}`;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
if (!adminPassword) throw new Error('Set E2E_ADMIN_PASSWORD for the disposable seeded database');
const demoPassword = process.env.E2E_DEMO_PASSWORD || 'DevDemo-Change123!';
const python = process.env.E2E_PYTHON || 'python3';

export default defineConfig({
  testDir: './e2e',
  use: { baseURL: frontend.origin, headless: true },
  workers: 1,
  retries: 0,
  timeout: 90_000,
  // The first-sign-in journey must run before another journey changes the seeded admin.
  projects: [
    { name: 'first-sign-in', testMatch: /(?:^|[\\/])auth\.spec\.ts$/ },
    { name: 'journeys', testIgnore: /(?:^|[\\/])auth\.spec\.ts$/, dependencies: ['first-sign-in'] },
  ],
  webServer: [
    {
      command: `${JSON.stringify(python)} -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: path.resolve(frontendDir, '../backend'),
      url: `${backend}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ENVIRONMENT: 'test',
        JWT_SECRET: process.env.E2E_JWT_SECRET || 'e2e-only-signing-key-with-sufficient-variety-12345',
        MONGODB_URI: process.env.E2E_MONGODB_URI || 'mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true',
        MONGODB_DB: process.env.E2E_MONGODB_DB || `ketsip_e2e_${randomUUID().replace(/-/g, '')}`,
        SEED_ON_STARTUP: 'true',
        SEED_ADMIN_PASSWORD: adminPassword,
        SEED_DEMO_PASSWORD: demoPassword,
        CORS_ORIGINS: frontend.origin,
      },
    },
    {
      command: `node node_modules/vite/bin/vite.js --host ${frontend.hostname} --port ${frontend.port} --strictPort`,
      cwd: frontendDir,
      url: frontend.origin,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { VITE_PROXY_TARGET: backend, VITE_API_BASE: '' },
    },
  ],
});
