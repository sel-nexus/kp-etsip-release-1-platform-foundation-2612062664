import { expect, test } from '@playwright/test';

 test('leader views scoped reports and downloads filtered inventory', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto('/login');
  await page.getByLabel('Email').fill('leader@ketsip.local');
  await page.getByLabel('Password', { exact: true }).fill(process.env.E2E_DEMO_PASSWORD || 'DevDemo-Change123!');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Reports', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Payment Gateway' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Customer Profile' })).toHaveCount(0);
  await page.getByLabel('Risk level').selectOption('High');
  await expect(page.getByText('1 matching records')).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  let csv = '';
  for await (const chunk of stream) csv += chunk.toString();
  expect(csv).toContain('Payment Gateway');
  expect(csv).not.toContain('Customer Profile');
  await page.getByLabel('Report', { exact: true }).selectOption('portfolios');
  await expect(page.getByRole('link', { name: 'Payments', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Customer Platform', exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});
