import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';

type Event = { id: string; timestamp: string; userId: string | null; userName: string; action: string; entityType: string; entityId: string | null; details: string };
type Page = { items: Event[]; total: number; canExport: boolean };

/** Present the separate read-only audit history and its bounded CSV export. */
export default function AuditPage() {
  const { token } = useAuth();
  const [userId, setUserId] = useState('');
  const [action, setAction] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [filters, setFilters] = useState('');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page>({ items: [], total: 0, canExport: false });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const query = `${filters}${filters ? '&' : ''}limit=25&offset=${offset}`;
  useEffect(() => {
    let active = true;
    setLoading(true);
    api<Page>(`/audit-logs?${query}`, {}, token || undefined).then(result => { if (active) { setPage(result); setError(''); } }).catch(e => { if (active) setError((e as Error).message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [query, token]);
  function apply(event: FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams();
    if (userId) params.set('userId', userId);
    if (action) params.set('action', action);
    if (from) params.set('from', new Date(`${from}T00:00:00Z`).toISOString());
    if (to) params.set('to', new Date(`${to}T23:59:59.999Z`).toISOString());
    setOffset(0); setFilters(params.toString());
  }
  async function download() {
    try {
      const response = await fetch(`${import.meta.env.VITE_API_BASE || ''}/api/audit-logs?${query}&format=csv`, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error(response.status === 403 ? 'Export is not permitted.' : `Export failed (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = 'audit-logs.csv'; link.click(); URL.revokeObjectURL(url);
    } catch (e) { setError((e as Error).message); }
  }
  return (
    <main className="content">
      <h1>Audit log</h1>
      <p>Read sensitive activity. CSV export downloads the current page and requires export permission.</p>
      <form className="dashboard-filters" onSubmit={apply}>
        <label>User ID<input value={userId} onChange={e => setUserId(e.target.value)} /></label>
        <label>Action<select value={action} onChange={e => setAction(e.target.value)}><option value="">All actions</option>{['login', 'create', 'edit', 'delete', 'roleChange', 'permissionChange', 'export'].map(item => <option key={item}>{item}</option>)}</select></label>
        <label>From (UTC)<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
        <label>To (UTC)<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
        <button>Apply filters</button>
      </form>
      {page.canExport && <button onClick={() => void download()}>Export current page CSV</button>}
      {error && <p role="alert" className="error">{error}</p>}
      {loading && <p role="status">Loading audit log…</p>}
      {!loading && !page.items.length && <p>No audit events match these filters.</p>}
      <div className="table-wrap"><table><thead><tr><th scope="col">Time</th><th scope="col">User</th><th scope="col">Action</th><th scope="col">Entity</th><th scope="col">Details</th></tr></thead><tbody>{page.items.map(item => <tr key={item.id}><td>{new Date(item.timestamp).toLocaleString()}</td><td>{item.userName} {item.userId && <small>({item.userId})</small>}</td><td>{item.action}</td><td>{item.entityType} {item.entityId}</td><td>{item.details}</td></tr>)}</tbody></table></div>
      <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button><span>{page.total ? offset + 1 : 0}–{Math.min(offset + 25, page.total)} of {page.total}</span><button disabled={offset + 25 >= page.total} onClick={() => setOffset(offset + 25)}>Next</button></div>
    </main>
  );
}
