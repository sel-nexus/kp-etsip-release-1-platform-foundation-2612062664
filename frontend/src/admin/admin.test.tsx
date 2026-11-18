import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

const admin = { id: '1', name: 'Admin', email: 'admin@example.com', role: 'Administrator', portfolioAccess: [], status: 'Active', defaultLandingPage: 'dashboard', mustChangePassword: false, managerName: null, vendorIndicator: false };
const roles = [{ id: 'r', name: 'Engineer', description: 'Engineer', permissions: ['view', 'edit'] }];
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('administration and audit', () => {
  beforeEach(() => { localStorage.setItem('ketsip.token', 'token'); history.replaceState({}, '', '/admin'); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
  it('creates a user with portfolio assignment', async () => {
    const fetcher = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/auth/me')) return reply(admin);
      if (url.includes('/roles')) return reply({ items: roles, total: 1 });
      if (url.includes('/portfolios')) return reply({ items: [{ id: 'p', name: 'Payments' }], total: 1 });
      if (options?.method === 'POST') return reply({});
      return reply({ items: [admin], total: 1 });
    });
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    await screen.findByText('Payments');
    await userEvent.type(screen.getByLabelText('Name'), 'New Person');
    await userEvent.type(screen.getByLabelText('Email'), 'person@example.com');
    await userEvent.type(screen.getByLabelText('Initial password'), 'Very-Strong-Password123!');
    await userEvent.click(screen.getByLabelText('Payments'));
    await userEvent.click(screen.getByRole('button', { name: 'Save user' }));
    expect(await screen.findByText('User saved.')).toBeVisible();
    const call = fetcher.mock.calls.find(call => call[1]?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ email: 'person@example.com', portfolioAccess: ['p'], role: 'Engineer' });
  });
  it('edits role permissions', async () => {
    const fetcher = vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(admin) : url.includes('/roles') ? reply({ items: roles, total: 1 }) : reply({ items: [], total: 0 }));
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    await screen.findByRole('heading', { name: 'Users' });
    await userEvent.click(screen.getByRole('button', { name: 'Roles' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Engineer' }));
    await userEvent.click(screen.getByLabelText('edit'));
    await userEvent.click(screen.getByRole('button', { name: 'Save role' }));
    expect(await screen.findByText('Role saved.')).toBeVisible();
    expect(JSON.parse(String(fetcher.mock.calls.find(call => call[1]?.method === 'PUT')?.[1]?.body)).permissions).toEqual(['view']);
  });
  it('shows auditor activity and applies action filters without administration', async () => {
    history.replaceState({}, '', '/audit');
    const auditor = { ...admin, role: 'Auditor' };
    const fetcher = vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(auditor) : reply({ items: [{ id: 'a', timestamp: '2026-01-01T00:00:00Z', userId: '1', userName: 'Admin', action: 'edit', entityType: 'user', entityId: 'x', details: 'User updated' }], total: 1, canExport: false }));
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    expect(await screen.findByText('User updated')).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Administration' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Export current page CSV' })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText('Action'), 'edit');
    await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(fetcher.mock.calls.some(call => String(call[0]).includes('action=edit'))).toBe(true);
  });
});
