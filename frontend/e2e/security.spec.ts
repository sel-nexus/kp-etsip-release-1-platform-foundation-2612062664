import { expect, test } from '@playwright/test';

test('stored hostile application text stays inert in the real browser', async ({ page }) => {
  const errors: string[] = [];
  const dialogs: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('requestfailed', request => errors.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`));
  page.on('dialog', dialog => { dialogs.push(dialog.message()); void dialog.dismiss(); });
  await page.addInitScript(() => { (window as unknown as { inventoryXss: boolean }).inventoryXss = false; });

  const password = process.env.E2E_DEMO_PASSWORD || 'DevDemo-Change123!';
  const login = await page.request.post('/api/auth/login', { data: { email: 'leader@ketsip.local', password } });
  expect(login.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await login.json()).token}` };
  const listing = await page.request.get('/api/applications?search=Payment%20Gateway', { headers });
  expect(listing.ok()).toBeTruthy();
  const original = (await listing.json()).items.find((item: { name: string }) => item.name === 'Payment Gateway');
  expect(original).toBeTruthy();
  const fields = ['name', 'description', 'portfolioId', 'productArea', 'businessOwner', 'technologyOwner', 'etsOwner', 'criticality', 'tier', 'lifecycleStatus', 'technologyStack', 'repositoryLink', 'pipelineLink', 'applicationHealthScore', 'qualityScore', 'automationCoverage', 'unitTestCoverage', 'openDefects', 'openCriticalDefects', 'riskLevel'];
  const originalInput = Object.fromEntries(fields.map(field => [field, original[field]]));
  const hostile = '<script>window.inventoryXss = true</script><img src=x onerror="window.inventoryXss = true"><svg onload="window.inventoryXss = true"></svg>';

  try {
    await page.goto('/login');
    await page.getByLabel('Email').fill('leader@ketsip.local');
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.getByRole('link', { name: 'Payment Gateway' }).click();
    await page.getByRole('link', { name: 'Edit application' }).click();
    await page.getByLabel('Description').fill(hostile);
    const saved = page.waitForResponse(response => response.url().endsWith(`/api/applications/${original.id}`) && response.request().method() === 'PUT');
    await page.getByRole('button', { name: 'Save application' }).click();
    const response = await saved;
    expect(response.ok()).toBeTruthy();
    expect((await response.json()).description).toBe(hostile);
    const rendered = page.getByText(hostile, { exact: true });
    await expect(rendered).toBeVisible();
    await expect(rendered.locator('script, img, svg')).toHaveCount(0);
    await expect(page.locator('main script, main img, main svg')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { inventoryXss: boolean }).inventoryXss)).toBe(false);
    expect(dialogs).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    const restored = await page.request.put(`/api/applications/${original.id}`, { headers, data: originalInput });
    expect(restored.ok()).toBeTruthy();
  }
});
