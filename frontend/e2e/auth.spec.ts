import { test, expect } from '@playwright/test';

test('first sign-in, password change, preference persistence and sign-out', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => errors.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const initial = process.env.E2E_ADMIN_PASSWORD;
  if (!initial) throw new Error('Set E2E_ADMIN_PASSWORD to the seeded admin password on a fresh disposable database');
  const replacement = `Changed-${Date.now()}-Secure!`;
  await page.goto('/login');
  await page.getByLabel('Email').fill('admin@ketsip.local');
  await page.getByLabel('Password', { exact: true }).fill(initial);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByText('Change your initial password before continuing.')).toBeVisible();
  await expect(page.getByText('admin@ketsip.local')).toBeVisible();
  await page.getByLabel('Current password').fill(initial);
  await page.getByLabel('New password').fill(replacement);
  await page.getByRole('button', { name: 'Change password' }).click();
  await expect(page.getByRole('heading', { name: 'Executive Dashboard' })).toBeVisible();
  await page.getByRole('link', { name: 'My profile' }).click();
  await page.getByLabel('Open after sign in').selectOption('applications');
  await page.getByRole('button', { name: 'Save preference' }).click();
  await expect(page.getByText('Landing preference saved.')).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.getByLabel('Email').fill('admin@ketsip.local');
  await page.getByLabel('Password', { exact: true }).fill(replacement);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Application Repository' })).toBeVisible();
  // Restore shared seed credentials and landing preference for subsequent journeys.
  await page.getByRole('link', { name: 'My profile' }).click();
  await page.getByLabel('Open after sign in').selectOption('dashboard');
  await page.getByRole('button', { name: 'Save preference' }).click();
  await expect(page.getByText('Landing preference saved.')).toBeVisible();
  await page.getByLabel('Current password').fill(replacement);
  await page.getByLabel('New password').fill(initial);
  await page.getByRole('button', { name: 'Change password' }).click();
  await expect(page.getByText('Password changed.')).toBeVisible();
  expect(errors).toEqual([]);
});

test('a clean session redirects protected routes and rejects invalid credentials', async ({ page }) => {
  const errors: string[] = [];
  let expectingRejectedLogin = false;
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => errors.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    // Chromium reports the deliberately rejected HTTP login as a resource error.
    if (expectingRejectedLogin && message.location().url === new URL('/api/auth/login', page.url()).href && /Failed to load resource: the server responded with a status of 401\b/.test(message.text())) return;
    errors.push(message.text());
  });
  await page.goto('/reports');
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  const loginResponse = page.waitForResponse(response => response.url().includes('/api/auth/login') && response.request().method() === 'POST');
  await page.getByLabel('Email').fill('leader@ketsip.local');
  await page.getByLabel('Password', { exact: true }).fill('definitely-wrong-password');
  expectingRejectedLogin = true;
  await page.getByRole('button', { name: 'Sign in' }).click();
  expect((await loginResponse).status()).toBe(401);
  await expect(page.getByRole('alert')).toHaveText('Invalid email or password');
  expectingRejectedLogin = false;
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole('link', { name: 'Reports', exact: true })).toHaveCount(0);
  await page.getByLabel('Password', { exact: true }).fill(process.env.E2E_DEMO_PASSWORD || 'DevDemo-Change123!');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Application Repository' })).toBeVisible();
  await page.getByRole('link', { name: 'Reports', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Payment Gateway' })).toBeVisible();
  expect(errors).toEqual([]);
});
