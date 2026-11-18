import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { Portfolio, PortfolioInput, PortfolioPage } from '../types';

const empty: PortfolioInput = { name: '', owner: '', description: '', productArea: '', status: 'Active' };

/** Create or edit a portfolio only when the API grants the action. */
export default function PortfolioForm() {
  const { id } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState<PortfolioInput>(empty);
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    const request = id ? api<Portfolio>(`/portfolios/${id}`, {}, token || undefined) : api<PortfolioPage>('/portfolios?limit=1', {}, token || undefined);
    request.then(data => {
      if (!active) return;
      if ('canEdit' in data) { setAllowed(data.canEdit); setForm({ name: data.name, owner: data.owner, description: data.description, productArea: data.productArea, status: data.status }); }
      else setAllowed(data.canCreate);
    }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [id, token]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setSaving(true);
    try {
      const saved = await api<Portfolio>(id ? `/portfolios/${id}` : '/portfolios', { method: id ? 'PUT' : 'POST', body: JSON.stringify(form) }, token || undefined);
      navigate(`/portfolios/${saved.id}`);
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save portfolio'); setSaving(false); }
  }
  return (
    <main className="content">
      <Link to={id ? `/portfolios/${id}` : '/portfolios'}>← Cancel</Link>
      <h1>{id ? 'Edit portfolio' : 'Create portfolio'}</h1>
      {error && <p role="alert" className="error">{error}</p>}
      {allowed === null && !error && <p role="status">Checking access…</p>}
      {allowed === false && <p role="alert">You cannot change this portfolio.</p>}
      {allowed && <form className="portfolio-form" onSubmit={submit}>
        <label htmlFor="name">Name</label><input id="name" required maxLength={200} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
        <label htmlFor="owner">Owner</label><input id="owner" maxLength={200} value={form.owner} onChange={e => setForm({ ...form, owner: e.target.value })} />
        <label htmlFor="area">Product area</label><input id="area" maxLength={200} value={form.productArea} onChange={e => setForm({ ...form, productArea: e.target.value })} />
        <label htmlFor="description">Description</label><textarea id="description" maxLength={4000} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} />
        <label htmlFor="status">Status</label><select id="status" value={form.status} onChange={e => setForm({ ...form, status: e.target.value as PortfolioInput['status'] })}><option>Active</option><option>Archived</option></select>
        <button disabled={saving}>{saving ? 'Saving…' : 'Save portfolio'}</button>
      </form>}
    </main>
  );
}
