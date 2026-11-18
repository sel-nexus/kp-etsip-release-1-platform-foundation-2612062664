import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

const user = { id: '1', name: 'Executive', email: 'executive@ketsip.local', role: 'Executive', portfolioAccess: [], status: 'Active', defaultLandingPage: 'dashboard', mustChangePassword: false };
const dashboard = { enterpriseQualityScore: 76, applicationHealthScore: 80, automationMaturityScore: 65, openCriticalDefects: 4, applicationsAtRisk: 1, totalApplications: 2, totalPortfolios: 1, portfolios: { items: [{ id: 'aaaaaaaaaaaaaaaaaaaaaaaa', name: 'Payments', applicationCount: 2, qualityScore: 76, applicationsAtRisk: 1 }], total: 1 } };
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('executive dashboard', () => {
  beforeEach(() => { localStorage.setItem('ketsip.token', 'token'); history.replaceState({}, '', '/dashboard'); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
  it('renders metric values, portfolio breakdown and sends all filters', async () => {
    const fetcher = vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : reply(dashboard));
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    const metrics = await screen.findByRole('region', { name: 'Executive metrics' });
    expect(within(metrics).getByText('76')).toBeVisible();
    expect(within(metrics).getByText('65')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Payments' })).toHaveAttribute('href', '/portfolios/aaaaaaaaaaaaaaaaaaaaaaaa');
    await userEvent.type(screen.getByLabelText('Portfolio ID'), 'aaaaaaaaaaaaaaaaaaaaaaaa');
    await userEvent.type(screen.getByLabelText('Product area'), 'Payments');
    await userEvent.selectOptions(screen.getByLabelText('Risk level'), 'High');
    await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(fetcher.mock.calls.some(call => String(call[0]).includes('portfolioId=aaaaaaaaaaaaaaaaaaaaaaaa') && String(call[0]).includes('productArea=Payments') && String(call[0]).includes('riskLevel=High'))).toBe(true);
  });
  it('displays empty averages and an empty result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : reply({ ...dashboard, enterpriseQualityScore: null, applicationHealthScore: null, automationMaturityScore: null, totalApplications: 0, totalPortfolios: 0, portfolios: { items: [], total: 0 } })));
    render(<App />);
    expect(await screen.findByText('No applications match the selected filters.')).toBeVisible();
    expect(screen.getAllByText('—')).toHaveLength(3);
    expect(screen.getByText('No portfolios match the selected filters.')).toBeVisible();
  });
  it('shows an error and allows a retry', async () => {
    let fail = true;
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : fail ? reply({ detail: 'Not permitted' }, 403) : reply(dashboard)));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Not permitted');
    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('link', { name: 'Payments' })).toBeVisible();
  });
});
