import { useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import type { User } from '../types';

/** Show account details and manage password and landing preference. */
export default function ProfilePage() {
  const { user, changePassword, changeLanding } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [landing, setLanding] = useState<User['defaultLandingPage']>(user?.defaultLandingPage || 'applications');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!user) return null;
  async function passwordSubmit(event: FormEvent) {
    event.preventDefault(); setError(''); setMessage(''); setBusy(true);
    try {
      const updated = await changePassword(current, next);
      setCurrent(''); setNext(''); setMessage('Password changed.');
      if (user?.mustChangePassword) navigate(`/${updated.defaultLandingPage}`, { replace: true });
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to change password'); }
    finally { setBusy(false); }
  }
  async function landingSubmit(event: FormEvent) {
    event.preventDefault(); setError(''); setMessage(''); setBusy(true);
    try { await changeLanding(landing); setMessage('Landing preference saved.'); }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to save preference'); }
    finally { setBusy(false); }
  }
  return (
    <main className="content">
      <p className="eyebrow">ACCOUNT / PROFILE</p>
      <h1>My profile</h1>
      <p>Manage your account and preferred starting point.</p>
      {user.mustChangePassword && <p className="notice" role="status">Change your initial password before continuing.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <div className="profile-grid">
        <section className="card">
          <h2>Account details</h2>
          <dl>
            <dt>Name</dt><dd>{user.name}</dd>
            <dt>Email</dt><dd>{user.email}</dd>
            <dt>Role</dt><dd>{user.role}</dd>
            <dt>Portfolio access</dt><dd>{user.portfolioAccess.length ? user.portfolioAccess.join(', ') : ['Administrator', 'Executive', 'Auditor'].includes(user.role) ? 'All portfolios' : 'No assigned portfolios'}</dd>
          </dl>
        </section>
        <div className="stack">
          <section className="card">
            <h2>Change password</h2>
            <form onSubmit={passwordSubmit}>
              <label htmlFor="current">Current password</label>
              <input id="current" type="password" autoComplete="current-password" required value={current} onChange={e => setCurrent(e.target.value)} />
              <label htmlFor="new">New password</label>
              <input id="new" type="password" autoComplete="new-password" minLength={12} required value={next} onChange={e => setNext(e.target.value)} />
              <small>At least 12 characters, maximum 72 UTF-8 bytes.</small>
              <button type="submit" disabled={busy}>Change password</button>
            </form>
          </section>
          {!user.mustChangePassword && <section className="card">
            <h2>Default landing page</h2>
            <form onSubmit={landingSubmit}>
              <label htmlFor="landing">Open after sign in</label>
              <select id="landing" value={landing} onChange={e => setLanding(e.target.value as User['defaultLandingPage'])}>
                <option value="dashboard">Executive Dashboard</option>
                <option value="applications">Application Repository</option>
              </select>
              <button type="submit" disabled={busy}>Save preference</button>
            </form>
          </section>}
        </div>
      </div>
    </main>
  );
}
