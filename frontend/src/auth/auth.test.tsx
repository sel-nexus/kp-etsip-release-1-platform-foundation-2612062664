import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

const profile = { id: '1', name: 'Administrator', email: 'admin@ketsip.local', role: 'Administrator', portfolioAccess: [], status: 'Active', defaultLandingPage: 'dashboard', mustChangePassword: true };
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('authentication interface', () => {
  beforeEach(() => { localStorage.clear(); history.replaceState({}, '', '/login'); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  it('shows login errors without navigating', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ detail: 'Invalid email or password' }, 401)));
    render(<App />);
    await userEvent.type(screen.getByLabelText('Email'), 'admin@ketsip.local');
    await userEvent.type(screen.getByLabelText('Password'), 'incorrect');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password');
  });
  it('requires initial password change before showing preferences', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => url.endsWith('/auth/login') ? reply({ token: 'token', user: profile }) : reply(profile)));
    render(<App />);
    await userEvent.type(screen.getByLabelText('Email'), 'admin@ketsip.local');
    await userEvent.type(screen.getByLabelText('Password'), 'initial-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Change your initial password before continuing.')).toBeTruthy();
    expect(screen.queryByLabelText('Open after sign in')).toBeNull();
    await waitFor(() => expect(localStorage.getItem('ketsip.token')).toBe('token'));
  });
});
