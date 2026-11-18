import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

const user = { id: '1', name: 'Leader', email: 'leader@ketsip.local', role: 'Portfolio Leader', portfolioAccess: ['p'], status: 'Active', defaultLandingPage: 'applications', mustChangePassword: false };
const portfolio = { id: 'p', name: 'Payments', owner: '', description: '', productArea: '', status: 'Active', applicationCount: 1, qualityScore: 80, canEdit: true, canDelete: false };
const application = { id: 'a', name: 'Gateway', description: 'Initial', portfolioId: 'p', portfolioName: 'Payments', productArea: 'Payments', businessOwner: 'Business', technologyOwner: 'Technology', etsOwner: 'ETS', criticality: 'High', tier: 1, lifecycleStatus: 'Active', technologyStack: 'Python', repositoryLink: null, pipelineLink: null, applicationHealthScore: 80, qualityScore: 90, automationCoverage: 70, unitTestCoverage: 75, openDefects: 2, openCriticalDefects: 1, riskLevel: 'High', canEdit: true, canDelete: false };
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('application inventory', () => {
  beforeEach(() => { localStorage.setItem('ketsip.token', 'token'); history.replaceState({}, '', '/applications'); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
  it('renders inventory and sends filters', async () => {
    const lowRisk = { ...application, id: 'b', name: 'Ledger', riskLevel: 'Low' };
    const fetcher = vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : url.includes('/portfolios?') ? reply({ items: [portfolio], total: 1 }) : reply(url.includes('riskLevel=High') ? { items: [application], total: 1, canCreate: true } : { items: [application, lowRisk], total: 2, canCreate: true }));
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    expect(await screen.findByRole('link', { name: 'Gateway' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'Ledger' })).toBeVisible();
    expect(screen.getByText('2 applications')).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText('Risk level'), 'High');
    expect(fetcher.mock.calls.some(call => String(call[0]).includes('riskLevel=High'))).toBe(true);
    expect(await screen.findByText('1 applications')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Gateway' })).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Ledger' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Create application' })).toBeVisible();
  });
  it('shows full detail and hides unauthorized deletion', async () => {
    history.replaceState({}, '', '/applications/a');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : reply(application)));
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Gateway' })).toBeVisible();
    expect(screen.getByText('Technology')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Payments' })).toHaveAttribute('href', '/portfolios/p');
    expect(screen.queryByRole('button', { name: 'Delete application' })).toBeNull();
  });
  it('submits edited fields and navigates to detail', async () => {
    history.replaceState({}, '', '/applications/a/edit');
    let savedApplication = { ...application };
    const fetcher = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/auth/me')) return reply(user);
      if (url.includes('/portfolios?')) return reply({ items: [portfolio], total: 1 });
      if (options?.method === 'PUT') {
        savedApplication = { ...savedApplication, ...JSON.parse(String(options.body)) };
        return reply(savedApplication);
      }
      return reply(savedApplication);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    const input = await screen.findByLabelText('Description');
    await userEvent.clear(input);
    await userEvent.type(input, 'Updated');
    await userEvent.click(screen.getByRole('button', { name: 'Save application' }));
    expect(fetcher.mock.calls.some(call => call[1]?.method === 'PUT' && JSON.parse(String(call[1].body)).description === 'Updated')).toBe(true);
    expect(await screen.findByRole('heading', { name: 'Gateway' })).toBeVisible();
    expect(await screen.findByText('Updated')).toBeVisible();
  });
  it('shows denied form and API errors', async () => {
    history.replaceState({}, '', '/applications/a/edit');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : url.includes('/portfolios?') ? reply({ items: [portfolio], total: 1 }) : reply({ ...application, canEdit: false })));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot change');
    expect(screen.queryByRole('button', { name: 'Save application' })).toBeNull();
  });
  it('reports failed list requests', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : url.includes('/portfolios?') ? reply({ items: [], total: 0 }) : reply({ detail: 'Not permitted' }, 403)));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Not permitted');
  });
});
