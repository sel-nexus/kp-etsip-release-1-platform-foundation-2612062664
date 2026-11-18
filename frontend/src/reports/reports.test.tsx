import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

const user = { id: '1', name: 'Leader', email: 'leader@ketsip.local', role: 'Portfolio Leader', portfolioAccess: ['p'], status: 'Active', defaultLandingPage: 'applications', mustChangePassword: false };
const app = { id: 'a', name: 'Gateway', portfolioId: 'p', portfolioName: 'Payments', criticality: 'High', tier: 1, lifecycleStatus: 'Active', applicationHealthScore: 80, qualityScore: 90, automationCoverage: 70, openCriticalDefects: 1, riskLevel: 'High' };
const portfolio = { id: 'p', name: 'Payments', owner: 'Owner', productArea: 'Payments', status: 'Active', applicationCount: 3, qualityScore: 80 };
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('reports', () => {
  beforeEach(() => { localStorage.setItem('ketsip.token', 'token'); history.replaceState({}, '', '/reports'); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

  it('renders application and portfolio data and filters', async () => {
    const fetcher = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('/auth/me')) return reply(user);
      if (url.includes('/reports/portfolios')) return reply({ items: [portfolio], total: 1, canExport: true });
      return reply({ items: [app], total: 30, canExport: true });
    });
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    expect(await screen.findByRole('link', { name: 'Gateway' })).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText('Risk level'), 'High');
    await waitFor(() => expect(fetcher.mock.calls.some(call => String(call[0]).includes('riskLevel=High'))).toBe(true));
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(fetcher.mock.calls.some(call => String(call[0]).includes('offset=25'))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText('Report'), 'portfolios');
    expect(await screen.findByRole('link', { name: 'Payments' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Portfolio Report' })).toBeVisible();
  });

  it('hides export for users without permission and displays empty state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : reply({ items: [], total: 0, canExport: false })));
    render(<App />);
    expect(await screen.findByText('No records match these filters.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Export CSV' })).toBeNull();
  });

  it('reports loading and failed requests', async () => {
    let rejectReport: (reason: Error) => void = () => {};
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => url.endsWith('/auth/me') ? Promise.resolve(reply(user)) : new Promise((_resolve, reject) => { rejectReport = reject; })));
    render(<App />);
    expect(await screen.findByText('Loading report…')).toBeVisible();
    rejectReport(new Error('Network unavailable'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable');
  });

  it('reports export denial from the server after a permission change', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : url.includes('format=csv') ? reply({ detail: 'Export is not permitted' }, 403) : reply({ items: [app], total: 1, canExport: true })));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: 'Export CSV' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Export is not permitted');
  });

  it('offers application-list export with active filters', async () => {
    history.replaceState({}, '', '/applications?riskLevel=High');
    const fetcher = vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : url.includes('/portfolios?') ? reply({ items: [], total: 0 }) : url.includes('format=csv') ? reply({ detail: 'Export is not permitted' }, 403) : reply({ items: [app], total: 1, canCreate: false, canExport: true }));
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: 'Export CSV' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Export is not permitted');
    expect(fetcher.mock.calls.some(call => String(call[0]).includes('riskLevel=High') && String(call[0]).includes('format=csv'))).toBe(true);
  });
});
