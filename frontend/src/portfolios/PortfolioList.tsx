import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { PortfolioPage } from '../types';

/** Display the visible inventory with shareable filters and pagination. */
export default function PortfolioList() {
  const { token } = useAuth();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState<PortfolioPage | null>(null);
  const [error, setError] = useState('');
  const offset = Math.max(0, Number(params.get('offset')) || 0);
  useEffect(() => {
    let active = true;
    setPage(null); setError('');
    api<PortfolioPage>(`/portfolios?${params.toString()}`, {}, token || undefined)
      .then(data => { if (active) setPage(data); })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [params, token]);
  function change(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== 'offset') next.delete('offset');
    setParams(next);
  }
  return (
    <main className="content">
      <p className="eyebrow">INVENTORY</p>
      <h1>Portfolios</h1>
      <p>Explore ownership and quality across your visible portfolios.</p>
      {page?.canCreate && <Link to="/portfolios/new">Create portfolio</Link>}
      <div className="portfolio-filters">
        <label>Search<input value={params.get('search') || ''} onChange={e => change('search', e.target.value)} /></label>
        <label>Status<select value={params.get('status') || ''} onChange={e => change('status', e.target.value)}><option value="">All statuses</option><option>Active</option><option>Archived</option></select></label>
        <label>Product area<input value={params.get('productArea') || ''} onChange={e => change('productArea', e.target.value)} /></label>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {!page && !error && <p role="status">Loading portfolios…</p>}
      {page && <>
        <p aria-live="polite">{page.total} portfolios</p>
        {page.items.length === 0 ? <p>No portfolios match these filters.</p> : <div className="table-wrap"><table><thead><tr><th scope="col">Name</th><th scope="col">Owner</th><th scope="col">Product Area</th><th scope="col">Applications</th><th scope="col">Quality Score</th><th scope="col">Status</th></tr></thead><tbody>
          {page.items.map(item => <tr key={item.id}><td><Link to={`/portfolios/${item.id}`}>{item.name}</Link></td><td>{item.owner || '—'}</td><td>{item.productArea || '—'}</td><td>{item.applicationCount}</td><td>{item.qualityScore ?? 'No data'}</td><td>{item.status}</td></tr>)}
        </tbody></table></div>}
        <div className="pagination"><button disabled={offset === 0} onClick={() => change('offset', String(Math.max(0, offset - 25)))}>Previous</button><button disabled={offset + 25 >= page.total} onClick={() => change('offset', String(offset + 25))}>Next</button></div>
      </>}
    </main>
  );
}
