import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { Application } from '../types';

/** Show the application record and its permitted actions. */
export default function ApplicationDetail() {
  const { id } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const [item, setItem] = useState<Application | null>(null);
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    let active = true;
    setItem(null); setError('');
    api<Application>(`/applications/${id}`, {}, token || undefined)
      .then(data => { if (active) setItem(data); })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [id, token]);
  async function remove() {
    if (!window.confirm('Delete this application?')) return;
    setDeleting(true); setError('');
    try { await api<void>(`/applications/${id}`, { method: 'DELETE' }, token || undefined); navigate('/applications'); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not delete application'); setDeleting(false); }
  }
  return (
    <main className="content">
      <Link to="/applications">← Applications</Link>
      {error && <p role="alert" className="error">{error}</p>}
      {!item && !error && <p role="status">Loading application…</p>}
      {item && <>
        <p className="eyebrow">APPLICATION</p>
        <h1>{item.name}</h1>
        <p>{item.description || 'No description provided.'}</p>
        <div className="portfolio-actions">{item.canEdit && <Link to={`/applications/${item.id}/edit`}>Edit application</Link>}{item.canDelete && <button disabled={deleting} onClick={() => void remove()}>Delete application</button>}</div>
        <dl className="portfolio-summary">
          <div><dt>Portfolio</dt><dd><Link to={`/portfolios/${item.portfolioId}`}>{item.portfolioName}</Link></dd></div>
          <div><dt>Product area</dt><dd>{item.productArea || '—'}</dd></div>
          <div><dt>Business owner</dt><dd>{item.businessOwner || '—'}</dd></div>
          <div><dt>Technology owner</dt><dd>{item.technologyOwner || '—'}</dd></div>
          <div><dt>ETS owner</dt><dd>{item.etsOwner || '—'}</dd></div>
          <div><dt>Criticality</dt><dd>{item.criticality}</dd></div>
          <div><dt>Tier</dt><dd>{item.tier}</dd></div>
          <div><dt>Lifecycle status</dt><dd>{item.lifecycleStatus}</dd></div>
          <div><dt>Technology stack</dt><dd>{item.technologyStack || '—'}</dd></div>
          <div><dt>Health score</dt><dd>{item.applicationHealthScore}</dd></div>
          <div><dt>Quality score</dt><dd>{item.qualityScore}</dd></div>
          <div><dt>Automation coverage</dt><dd>{item.automationCoverage}%</dd></div>
          <div><dt>Unit-test coverage</dt><dd>{item.unitTestCoverage}%</dd></div>
          <div><dt>Open defects</dt><dd>{item.openDefects}</dd></div>
          <div><dt>Open critical defects</dt><dd>{item.openCriticalDefects}</dd></div>
          <div><dt>Risk level</dt><dd>{item.riskLevel}</dd></div>
          <div><dt>Repository</dt><dd>{item.repositoryLink ? <a href={item.repositoryLink} target="_blank" rel="noreferrer">Open repository</a> : '—'}</dd></div>
          <div><dt>Pipeline</dt><dd>{item.pipelineLink ? <a href={item.pipelineLink} target="_blank" rel="noreferrer">Open pipeline</a> : '—'}</dd></div>
        </dl>
      </>}
    </main>
  );
}
