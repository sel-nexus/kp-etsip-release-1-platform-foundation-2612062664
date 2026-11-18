import { useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';

/** Authenticate a local account and open its chosen destination. */
export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { signIn } = useAuth();
  const navigate = useNavigate();
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      const user = await signIn(email, password);
      navigate(user.mustChangePassword ? '/profile' : `/${user.defaultLandingPage}`, { replace: true });
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to sign in'); }
    finally { setBusy(false); }
  }
  return (
    <main className="login-layout">
      <section className="intro">
        <p className="eyebrow">KP ETSIP / PLATFORM FOUNDATION</p>
        <h1>Quality insight,<br />grounded in integrity.</h1>
        <p>One governed view of your enterprise application portfolio.</p>
      </section>
      <section className="card login-card">
        <p className="eyebrow">WELCOME BACK</p>
        <h2>Sign in</h2>
        <p>Use your local account to continue.</p>
        <form onSubmit={submit}>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} />
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} />
          {error && <p className="error" role="alert">{error}</p>}
          <button disabled={busy} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </section>
    </main>
  );
}
