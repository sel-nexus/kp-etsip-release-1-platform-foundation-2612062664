import { expect, test } from '@playwright/test';

test('leader views and edits an assigned portfolio through the live API', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const password = process.env.E2E_DEMO_PASSWORD || 'DevDemo-Change123!';
  await page.goto('/login');
  await page.getByLabel('Email').fill('leader@ketsip.local');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Portfolios', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Payments', exact: true })).toBeVisible();
  await page.getByLabel('Search').fill('Payments');
  await page.getByRole('link', { name: 'Payments', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Payment Gateway' })).toBeVisible();
  await expect(page.getByText('76', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete portfolio' })).toHaveCount(0);
  await page.getByRole('link', { name: 'Edit portfolio' }).click();
  await page.getByLabel('Description').fill('Updated through the portfolio journey');
  await page.getByRole('button', { name: 'Save portfolio' }).click();
  await expect(page.getByText('Updated through the portfolio journey')).toBeVisible();
  expect(errors).toEqual([]);
});
