import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { Dashboard } from '../types';

/** Show the live executive metrics for the caller's visible inventory. */
export default function DashboardPage() {
  const { token } = useAuth();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [portfolioId, setPortfolioId] = useState(params.get('portfolioId') || '');
  const [productArea, setProductArea] = useState(params.get('productArea') || '');
  const [riskLevel, setRiskLevel] = useState(params.get('riskLevel') || '');
  const query = params.toString();
  const offset = Math.max(0, Number(params.get('offset')) || 0);

  useEffect(() => {
    setPortfolioId(params.get('portfolioId') || '');
    setProductArea(params.get('productArea') || '');
    setRiskLevel(params.get('riskLevel') || '');
  }, [query]);
  useEffect(() => {
    let active = true;
    setData(null);
    setError('');
    api<Dashboard>(`/dashboard/executive?${query}`, {}, token || undefined)
      .then(result => { if (active) setData(result); })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [query, token, retry]);

  function applyFilters(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (portfolioId.trim()) next.set('portfolioId', portfolioId.trim());
    if (productArea.trim()) next.set('productArea', productArea.trim());
    if (riskLevel) next.set('riskLevel', riskLevel);
    setParams(next);
  }
  function move(nextOffset: number) {
    const next = new URLSearchParams(params);
    if (nextOffset) next.set('offset', String(nextOffset)); else next.delete('offset');
    setParams(next);
  }

  const cards = data && [
    ['Enterprise Quality Score', data.enterpriseQualityScore],
    ['Application Health Score', data.applicationHealthScore],
    ['Automation Maturity Score', data.automationMaturityScore],
    ['Open Critical Defects', data.openCriticalDefects],
    ['Applications at Risk', data.applicationsAtRisk],
    ['Total Applications', data.totalApplications],
    ['Total Portfolios', data.totalPortfolios],
  ] as const;
  return (
    <main className="content">
      <p className="eyebrow">EXECUTIVE INSIGHT</p>
      <h1>Executive Dashboard</h1>
      <p>Quality and risk across your visible inventory. Scores are manually maintained.</p>
      <form className="dashboard-filters" onSubmit={applyFilters}>
        <label htmlFor="dashboard-portfolio">Portfolio ID
          <input id="dashboard-portfolio" value={portfolioId} onChange={event => setPortfolioId(event.target.value)} pattern="[0-9a-fA-F]{24}" title="Enter a 24-character portfolio ID" />
        </label>
        <label htmlFor="dashboard-area">Product area
          <input id="dashboard-area" value={productArea} maxLength={200} onChange={event => setProductArea(event.target.value)} />
        </label>
        <label htmlFor="dashboard-risk">Risk level
          <select id="dashboard-risk" value={riskLevel} onChange={event => setRiskLevel(event.target.value)}>
            <option value="">All risk levels</option>
            <option>Low</option>
            <option>Medium</option>
            <option>High</option>
          </select>
        </label>
        <button type="submit">Apply filters</button>
      </form>
      {error && <div role="alert" className="error"><p>{error}</p><button type="button" onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
      {!data && !error && <p role="status">Loading dashboard…</p>}
      {data && <>
        <section aria-label="Executive metrics" className="dashboard-cards">
          {cards && cards.map(([label, value]) => <article className="card dashboard-card" key={label}>
            <h2>{label}</h2>
            <p>{value ?? '—'}</p>
          </article>)}
        </section>
        {data.totalApplications === 0 && <p role="status">No applications match the selected filters.</p>}
        <section aria-labelledby="breakdown-heading">
          <h2 id="breakdown-heading">Portfolio quality and risk</h2>
          <p>Quality is the average score; risk counts applications rated High.</p>
          {data.portfolios.total === 0 ? <p>No portfolios match the selected filters.</p> : <ul className="dashboard-list">
            {data.portfolios.items.map(item => <li key={item.id}>
              <div><Link to={`/portfolios/${item.id}`}>{item.name}</Link><span> · {item.applicationCount} applications</span></div>
              <div>Quality: {item.qualityScore ?? '—'} <span className="dashboard-bar" aria-hidden="true"><span style={{ width: `${item.qualityScore ?? 0}%` }} /></span></div>
              <div>Applications at risk: {item.applicationsAtRisk}</div>
            </li>)}
          </ul>}
          {data.portfolios.total > 25 && <div className="pagination">
            <button type="button" disabled={offset === 0} onClick={() => move(Math.max(0, offset - 25))}>Previous</button>
            <span>Showing {offset + 1}–{Math.min(offset + data.portfolios.items.length, data.portfolios.total)} of {data.portfolios.total}</span>
            <button type="button" disabled={offset + 25 >= data.portfolios.total} onClick={() => move(offset + 25)}>Next</button>
          </div>}
        </section>
      </>}
    </main>
  );
}
