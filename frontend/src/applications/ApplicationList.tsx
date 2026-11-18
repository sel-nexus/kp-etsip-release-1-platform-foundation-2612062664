import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { ApplicationInventoryPage, PortfolioPage } from '../types';
import { downloadReport } from '../reports/ReportsPage';

/** Display a scoped, filterable application inventory. */
export default function ApplicationList() {
  const { token } = useAuth();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState<(ApplicationInventoryPage & { canExport: boolean }) | null>(null);
  const [portfolios, setPortfolios] = useState<PortfolioPage | null>(null);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const offset = Math.max(0, Number(params.get('offset')) || 0);
  useEffect(() => {
    let active = true;
    setPage(null); setError('');
    api<ApplicationInventoryPage & { canExport: boolean }>(`/reports/applications?${params.toString()}`, {}, token || undefined)
      .then(data => { if (active) setPage(data); })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [params, token]);
  useEffect(() => {
    let active = true;
    async function loadPortfolios() {
      const items: PortfolioPage['items'] = [];
      let offset = 0;
      while (true) {
        const page = await api<PortfolioPage>(`/portfolios?limit=100&offset=${offset}`, {}, token || undefined);
        items.push(...page.items);
        offset += page.items.length;
        if (!page.items.length || offset >= page.total) break;
      }
      if (active) setPortfolios({ items, total: items.length, canCreate: false });
    }
    loadPortfolios().catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [token]);
  function change(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== 'offset') next.delete('offset');
    setParams(next);
  }
  async function download() {
    setError('');
    setExporting(true);
    try { await downloadReport('applications', params, token); }
    catch (err) { setError((err as Error).message); }
    finally { setExporting(false); }
  }
  return (
    <main className="content">
      <p className="eyebrow">INVENTORY</p>
      <h1>Application Repository</h1>
      <p>Explore application health, ownership and risk.</p>
      {page?.canCreate && <Link to="/applications/new">Create application</Link>}
      {page?.canExport && <button disabled={exporting} onClick={() => void download()}>{exporting ? 'Exporting…' : 'Export CSV'}</button>}
      <div className="portfolio-filters">
        <label>Search<input value={params.get('search') || ''} onChange={e => change('search', e.target.value)} /></label>
        <label>Portfolio<select value={params.get('portfolioId') || ''} onChange={e => change('portfolioId', e.target.value)}><option value="">All portfolios</option>{portfolios?.items.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>Criticality<select value={params.get('criticality') || ''} onChange={e => change('criticality', e.target.value)}><option value="">All criticalities</option>{['Low', 'Medium', 'High', 'Critical'].map(v => <option key={v}>{v}</option>)}</select></label>
        <label>Risk level<select value={params.get('riskLevel') || ''} onChange={e => change('riskLevel', e.target.value)}><option value="">All risks</option>{['Low', 'Medium', 'High'].map(v => <option key={v}>{v}</option>)}</select></label>
        <label>Lifecycle status<select value={params.get('lifecycleStatus') || ''} onChange={e => change('lifecycleStatus', e.target.value)}><option value="">All statuses</option>{['Active', 'Deprecated', 'Retired'].map(v => <option key={v}>{v}</option>)}</select></label>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {!page && !error && <p role="status">Loading applications…</p>}
      {page && <>
        <p aria-live="polite">{page.total} applications</p>
        {page.items.length === 0 ? <p>No applications match these filters.</p> : <div className="table-wrap"><table><thead><tr><th scope="col">Name</th><th scope="col">Portfolio</th><th scope="col">Criticality</th><th scope="col">Tier</th><th scope="col">Lifecycle Status</th><th scope="col">Health Score</th><th scope="col">Automation Coverage %</th><th scope="col">Open Critical Defects</th><th scope="col">Risk Level</th></tr></thead><tbody>
          {page.items.map(item => <tr key={item.id}><td><Link to={`/applications/${item.id}`}>{item.name}</Link></td><td><Link to={`/portfolios/${item.portfolioId}`}>{item.portfolioName}</Link></td><td>{item.criticality}</td><td>{item.tier}</td><td>{item.lifecycleStatus}</td><td>{item.applicationHealthScore}</td><td>{item.automationCoverage}%</td><td>{item.openCriticalDefects}</td><td>{item.riskLevel}</td></tr>)}
        </tbody></table></div>}
        <div className="pagination"><button disabled={offset === 0} onClick={() => change('offset', String(Math.max(0, offset - 25)))}>Previous</button><button disabled={offset + 25 >= page.total} onClick={() => change('offset', String(offset + 25))}>Next</button></div>
      </>}
    </main>
  );
}
