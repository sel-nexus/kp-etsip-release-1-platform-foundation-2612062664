import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

const user = { id: '1', name: 'Leader', email: 'leader@ketsip.local', role: 'Portfolio Leader', portfolioAccess: ['p'], status: 'Active', defaultLandingPage: 'applications', mustChangePassword: false };
const portfolio = { id: 'p', name: 'Payments', owner: 'Team', description: 'Payments work', productArea: 'Payments', status: 'Active', applicationCount: 1, qualityScore: 80, canEdit: true, canDelete: false };
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('portfolio inventory', () => {
  beforeEach(() => { localStorage.setItem('ketsip.token', 'token'); history.replaceState({}, '', '/portfolios'); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
  it('renders scoped results and sends filters to the API', async () => {
    const archived = { ...portfolio, id: 'q', name: 'Legacy', status: 'Archived' };
    const fetcher = vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : reply(url.includes('status=Archived') ? { items: [archived], total: 1, canCreate: false } : { items: [portfolio, archived], total: 2, canCreate: false }));
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    expect(await screen.findByRole('link', { name: 'Payments' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'Legacy' })).toBeVisible();
    expect(screen.getByText('2 portfolios')).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Create portfolio' })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'Archived');
    expect(fetcher.mock.calls.some(call => String(call[0]).includes('status=Archived'))).toBe(true);
    expect(await screen.findByText('1 portfolios')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Legacy' })).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Payments' })).toBeNull();
  });
  it('shows detail, linked applications and permitted edit but no delete', async () => {
    history.replaceState({}, '', '/portfolios/p');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : url.includes('/applications?') ? reply({ items: [{ id: 'a', name: 'Gateway', productArea: 'Payments', qualityScore: 80, riskLevel: 'Low' }], total: 1 }) : reply(portfolio)));
    render(<App />);
    expect(await screen.findByRole('link', { name: 'Gateway' })).toHaveAttribute('href', '/applications/a');
    expect(screen.getByRole('link', { name: 'Edit portfolio' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Delete portfolio' })).toBeNull();
  });
  it('saves edits and displays the server response', async () => {
    history.replaceState({}, '', '/portfolios/p/edit');
    let savedPortfolio = { ...portfolio };
    const fetcher = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/auth/me')) return reply(user);
      if (options?.method === 'PUT') {
        savedPortfolio = { ...savedPortfolio, ...JSON.parse(String(options.body)) };
        return reply(savedPortfolio);
      }
      if (url.includes('/applications?')) return reply({ items: [], total: 0 });
      return reply(savedPortfolio);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    const input = await screen.findByLabelText('Description');
    await userEvent.clear(input);
    await userEvent.type(input, 'Updated');
    await userEvent.click(screen.getByRole('button', { name: 'Save portfolio' }));
    expect(fetcher.mock.calls.some(call => call[1]?.method === 'PUT' && JSON.parse(String(call[1]?.body)).description === 'Updated')).toBe(true);
    expect(await screen.findByRole('heading', { name: 'Payments' })).toBeVisible();
    expect(await screen.findByText('Updated')).toBeVisible();
  });
  it('reports request errors and prevents an unpermitted form', async () => {
    history.replaceState({}, '', '/portfolios/p/edit');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/me') ? reply(user) : reply({ ...portfolio, canEdit: false })));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot change');
    expect(screen.queryByRole('button', { name: 'Save portfolio' })).toBeNull();
  });
});
