import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import App from '../App';

const user = { id: '1', name: 'Leader', email: 'leader@ketsip.local', role: 'Portfolio Leader', portfolioAccess: ['p'], status: 'Active', defaultLandingPage: 'applications', mustChangePassword: false };
const application = { id: 'a', name: 'Gateway', description: '', portfolioId: 'p', portfolioName: 'Payments', productArea: 'Payments', businessOwner: 'Business', technologyOwner: 'Technology', etsOwner: 'ETS', criticality: 'High', tier: 1, lifecycleStatus: 'Active', technologyStack: 'Python', repositoryLink: null, pipelineLink: null, applicationHealthScore: 80, qualityScore: 90, automationCoverage: 70, unitTestCoverage: 75, openDefects: 2, openCriticalDefects: 1, riskLevel: 'High', canEdit: true, canDelete: false };
const reply = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });

describe('untrusted application text', () => {
  beforeEach(() => {
    localStorage.setItem('ketsip.token', 'token');
    history.replaceState({}, '', '/applications/a');
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('renders ordinary application details normally', async () => {
    const description = 'Payment gateway for customer transactions.';
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => reply(url.endsWith('/auth/me') ? user : { ...application, description })));
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Gateway' })).toBeVisible();
    expect(screen.getByText(description)).toBeVisible();
    expect(screen.getByText('Business')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Payments' })).toHaveAttribute('href', '/portfolios/p');
  });

  it('renders script and HTML as literal text, not markup', async () => {
    const hostile = '<script>window.inventoryXss = true</script><img src=x onerror="window.inventoryXss = true"><svg onload="window.inventoryXss = true"></svg>';
    vi.stubGlobal('inventoryXss', false);
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => reply(url.endsWith('/auth/me') ? user : { ...application, description: hostile })));
    const { container } = render(<App />);

    const text = await screen.findByText(hostile);
    expect(text).toBeVisible();
    expect(text.textContent).toBe(hostile);
    expect(text.childElementCount).toBe(0);
    expect(container.querySelector('script, img, svg')).toBeNull();
    expect((window as unknown as { inventoryXss: boolean }).inventoryXss).toBe(false);
  });

  it('renders untrusted names and owner fields as text in their actual UI locations', async () => {
    const name = '<img src=x onerror="window.inventoryXss = true">';
    const owner = '<b>Not an HTML owner</b>';
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => reply(url.endsWith('/auth/me') ? user : { ...application, name, businessOwner: owner })));
    const { container } = render(<App />);

    const heading = await screen.findByRole('heading', { name });
    expect(heading.textContent).toBe(name);
    expect(heading.childElementCount).toBe(0);
    const ownerText = screen.getByText(owner);
    expect(ownerText.childElementCount).toBe(0);
    expect(container.querySelector('img, b')).toBeNull();
  });
});
