import { expect, test } from '@playwright/test';

test('leader searches, edits and navigates an assigned application', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto('/login');
  await page.getByLabel('Email').fill('leader@ketsip.local');
  await page.getByLabel('Password', { exact: true }).fill(process.env.E2E_DEMO_PASSWORD || 'DevDemo-Change123!');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Application Repository' })).toBeVisible();
  await page.getByLabel('Search').fill('Payment Gateway');
  await page.getByRole('link', { name: 'Payment Gateway' }).click();
  await expect(page.getByRole('link', { name: 'Payments', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete application' })).toHaveCount(0);
  await page.getByRole('link', { name: 'Edit application' }).click();
  await page.getByLabel('Description').fill('Updated through application journey');
  await page.getByRole('button', { name: 'Save application' }).click();
  await expect(page.getByText('Updated through application journey')).toBeVisible();
  await page.getByRole('link', { name: 'Payments', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Payments' })).toBeVisible();
  expect(errors).toEqual([]);
});
