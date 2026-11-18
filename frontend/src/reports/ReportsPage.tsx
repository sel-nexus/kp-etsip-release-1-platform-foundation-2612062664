import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { Application, Portfolio } from '../types';

type ReportPage = { items: (Application | Portfolio)[]; total: number; canExport: boolean };

/** Download every matching row using the authenticated reports endpoint. */
export async function downloadReport(kind: 'applications' | 'portfolios', filters: URLSearchParams, token: string | null): Promise<void> {
  const query = new URLSearchParams(filters);
  query.delete('offset');
  query.delete('limit');
  query.set('format', 'csv');
  const response = await fetch(`${import.meta.env.VITE_API_BASE || ''}/api/reports/${kind}?${query}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) {
    if (response.status === 401 && token) {
      localStorage.removeItem('ketsip.token');
      window.dispatchEvent(new Event('ketsip:session-expired'));
    }
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Export failed (${response.status})`);
  }
  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = `${kind}.csv`;
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Show scoped application and portfolio reports with shareable filters. */
export default function ReportsPage() {
  const { token } = useAuth();
  const [params, setParams] = useSearchParams();
  const kind = params.get('report') === 'portfolios' ? 'portfolios' : 'applications';
  const [page, setPage] = useState<ReportPage | null>(null);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);
  const offset = Math.max(0, Number(params.get('offset')) || 0);
  const query = new URLSearchParams(params);
  query.delete('report');
  const queryText = query.toString();

  useEffect(() => {
    let active = true;
    setPage(null);
    setError('');
    api<ReportPage>(`/reports/${kind}?${queryText}`, {}, token || undefined)
      .then(result => { if (active) setPage(result); })
      .catch(err => { if (active) setError((err as Error).message); });
    return () => { active = false; };
  }, [kind, queryText, token]);

  function change(key: string, value: string) {
    const next = key === 'report' ? new URLSearchParams() : new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== 'offset') next.delete('offset');
    setParams(next);
  }

  async function download() {
    setError('');
    setExporting(true);
    try { await downloadReport(kind, query, token); }
    catch (err) { setError((err as Error).message); }
    finally { setExporting(false); }
  }

  return (
    <main className="content">
      <p className="eyebrow">INSIGHT</p>
      <h1>Reports</h1>
      <p>Explore visible inventory. CSV exports include all matching records.</p>
      <label>Report
        <select value={kind} onChange={e => change('report', e.target.value)}>
          <option value="applications">Application Report</option>
          <option value="portfolios">Portfolio Report</option>
        </select>
      </label>
      <h2>{kind === 'applications' ? 'Application Report' : 'Portfolio Report'}</h2>
      <div className="portfolio-filters">
        <label>Search<input value={params.get('search') || ''} onChange={e => change('search', e.target.value)} /></label>
        {kind === 'applications' ? <>
          <label>Portfolio ID<input value={params.get('portfolioId') || ''} onChange={e => change('portfolioId', e.target.value)} /></label>
          <label>Criticality<select value={params.get('criticality') || ''} onChange={e => change('criticality', e.target.value)}><option value="">All criticalities</option>{['Low', 'Medium', 'High', 'Critical'].map(v => <option key={v}>{v}</option>)}</select></label>
          <label>Risk level<select value={params.get('riskLevel') || ''} onChange={e => change('riskLevel', e.target.value)}><option value="">All risks</option>{['Low', 'Medium', 'High'].map(v => <option key={v}>{v}</option>)}</select></label>
          <label>Lifecycle status<select value={params.get('lifecycleStatus') || ''} onChange={e => change('lifecycleStatus', e.target.value)}><option value="">All statuses</option>{['Active', 'Deprecated', 'Retired'].map(v => <option key={v}>{v}</option>)}</select></label>
        </> : <>
          <label>Status<select value={params.get('status') || ''} onChange={e => change('status', e.target.value)}><option value="">All statuses</option><option>Active</option><option>Archived</option></select></label>
          <label>Product area<input value={params.get('productArea') || ''} onChange={e => change('productArea', e.target.value)} /></label>
        </>}
      </div>
      {page?.canExport && <button disabled={exporting} onClick={() => void download()}>{exporting ? 'Exporting…' : 'Export CSV'}</button>}
      {error && <p role="alert" className="error">{error}</p>}
      {!page && !error && <p role="status">Loading report…</p>}
      {page && <>
        <p aria-live="polite">{page.total} matching records</p>
        {!page.items.length ? <p>No records match these filters.</p> : <div className="table-wrap">
          {kind === 'applications' ? <table><thead><tr><th scope="col">Name</th><th scope="col">Portfolio</th><th scope="col">Criticality</th><th scope="col">Tier</th><th scope="col">Lifecycle</th><th scope="col">Health</th><th scope="col">Quality</th><th scope="col">Automation %</th><th scope="col">Critical defects</th><th scope="col">Risk</th></tr></thead><tbody>
            {(page.items as Application[]).map(item => <tr key={item.id}><td><Link to={`/applications/${item.id}`}>{item.name}</Link></td><td>{item.portfolioName}</td><td>{item.criticality}</td><td>{item.tier}</td><td>{item.lifecycleStatus}</td><td>{item.applicationHealthScore}</td><td>{item.qualityScore}</td><td>{item.automationCoverage}%</td><td>{item.openCriticalDefects}</td><td>{item.riskLevel}</td></tr>)}
          </tbody></table> : <table><thead><tr><th scope="col">Name</th><th scope="col">Owner</th><th scope="col">Product area</th><th scope="col">Applications</th><th scope="col">Quality</th><th scope="col">Status</th></tr></thead><tbody>
            {(page.items as Portfolio[]).map(item => <tr key={item.id}><td><Link to={`/portfolios/${item.id}`}>{item.name}</Link></td><td>{item.owner || '—'}</td><td>{item.productArea || '—'}</td><td>{item.applicationCount}</td><td>{item.qualityScore ?? 'No data'}</td><td>{item.status}</td></tr>)}
          </tbody></table>}
        </div>}
        <div className="pagination">
          <button disabled={offset === 0} onClick={() => change('offset', String(Math.max(0, offset - 25)))}>Previous</button>
          <span>{page.total ? offset + 1 : 0}–{Math.min(offset + 25, page.total)} of {page.total}</span>
          <button disabled={offset + 25 >= page.total} onClick={() => change('offset', String(offset + 25))}>Next</button>
        </div>
      </>}
    </main>
  );
}
