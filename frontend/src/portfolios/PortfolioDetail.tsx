import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { ApplicationPage, Portfolio } from '../types';

/** Show a portfolio's computed summary and linked applications. */
export default function PortfolioDetail() {
  const { id } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [applications, setApplications] = useState<ApplicationPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    let active = true;
    setError(''); setPortfolio(null); setApplications(null);
    Promise.all([api<Portfolio>(`/portfolios/${id}`, {}, token || undefined), api<ApplicationPage>(`/portfolios/${id}/applications?offset=${offset}`, {}, token || undefined)])
      .then(([p, a]) => { if (active) { setPortfolio(p); setApplications(a); } })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [id, token, offset]);
  async function remove() {
    if (!window.confirm('Delete this portfolio?')) return;
    setDeleting(true); setError('');
    try { await api<void>(`/portfolios/${id}`, { method: 'DELETE' }, token || undefined); navigate('/portfolios'); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not delete portfolio'); setDeleting(false); }
  }
  return (
    <main className="content">
      <Link to="/portfolios">← Portfolios</Link>
      {error && <p role="alert" className="error">{error}</p>}
      {!portfolio && !error && <p role="status">Loading portfolio…</p>}
      {portfolio && <>
        <p className="eyebrow">PORTFOLIO</p>
        <h1>{portfolio.name}</h1>
        <p>{portfolio.description || 'No description provided.'}</p>
        <dl className="portfolio-summary"><div><dt>Owner</dt><dd>{portfolio.owner || '—'}</dd></div><div><dt>Product area</dt><dd>{portfolio.productArea || '—'}</dd></div><div><dt>Status</dt><dd>{portfolio.status}</dd></div><div><dt>Applications</dt><dd>{portfolio.applicationCount}</dd></div><div><dt>Quality score</dt><dd>{portfolio.qualityScore ?? 'No data'}</dd></div></dl>
        <div className="portfolio-actions">{portfolio.canEdit && <Link to={`/portfolios/${portfolio.id}/edit`}>Edit portfolio</Link>}{portfolio.canDelete && <button disabled={deleting} onClick={() => void remove()}>Delete portfolio</button>}</div>
        <h2>Applications</h2>
        {applications && (applications.items.length ? <div className="table-wrap"><table><thead><tr><th scope="col">Name</th><th scope="col">Product Area</th><th scope="col">Quality Score</th><th scope="col">Risk</th></tr></thead><tbody>{applications.items.map(app => <tr key={app.id}><td><Link to={`/applications/${app.id}`}>{app.name}</Link></td><td>{app.productArea || '—'}</td><td>{app.qualityScore ?? 'No data'}</td><td>{app.riskLevel || '—'}</td></tr>)}</tbody></table></div> : <p>No linked applications.</p>)}
        {applications && <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button><button disabled={offset + 25 >= applications.total} onClick={() => setOffset(offset + 25)}>Next</button></div>}
      </>}
    </main>
  );
}
