import { expect, test } from '@playwright/test';

test('executive sees live scoped metrics and filters by risk', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('requestfailed', request => errors.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`));
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let received!: () => void;
  const responseReady = new Promise<void>(resolve => { received = resolve; });
  await page.route('**/api/dashboard/executive*', async route => {
    const response = await route.fetch(); // The real backend response is held, not replaced with fixture data.
    received();
    await held;
    await route.fulfill({ response });
  });
  await page.goto('/login');
  await page.getByLabel('Email').fill('executive@ketsip.local');
  await page.getByLabel('Password', { exact: true }).fill(process.env.E2E_DEMO_PASSWORD || 'DevDemo-Change123!');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await responseReady;
  try {
    await expect(page.getByRole('heading', { name: 'Executive Dashboard' })).toBeVisible();
    await expect(page.getByRole('status')).toHaveText('Loading dashboard…');
    await expect(page.getByRole('region', { name: 'Executive metrics' })).toHaveCount(0);
  } finally { release(); }
  await expect(page.getByText('Loading dashboard…')).toHaveCount(0);
  // Compare the rendered values to the live authenticated API response.
  const token = await page.evaluate(() => localStorage.getItem('ketsip.token'));
  const apiResponse = await page.request.get('/api/dashboard/executive', { headers: { Authorization: `Bearer ${token}` } });
  expect(apiResponse.ok()).toBeTruthy();
  const data = await apiResponse.json();
  expect(data.totalApplications).toBeGreaterThan(0);
  const metrics = page.getByRole('region', { name: 'Executive metrics' });
  await expect(metrics.getByRole('article').filter({ hasText: 'Total Applications' })).toContainText(String(data.totalApplications));
  await expect(page.getByRole('link', { name: data.portfolios.items[0].name, exact: true })).toBeVisible();
  await page.getByLabel('Risk level').selectOption('High');
  const filteredResponse = page.waitForResponse(result => result.url().includes('riskLevel=High') && result.ok());
  await page.getByRole('button', { name: 'Apply filters' }).click();
  const filtered = await (await filteredResponse).json();
  await expect(metrics.getByRole('article').filter({ hasText: 'Total Applications' })).toContainText(String(filtered.totalApplications));
  expect(filtered.totalApplications).toBe(filtered.applicationsAtRisk);
  await page.screenshot({ path: testInfo.outputPath('dashboard-desktop.png'), fullPage: true });
  expect(errors).toEqual([]);
});
