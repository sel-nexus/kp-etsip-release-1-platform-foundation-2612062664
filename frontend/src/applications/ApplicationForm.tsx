import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { Application, ApplicationInput, ApplicationInventoryPage, Portfolio } from '../types';

const empty: ApplicationInput = { name: '', description: '', portfolioId: '', productArea: '', businessOwner: '', technologyOwner: '', etsOwner: '', criticality: 'Medium', tier: 1, lifecycleStatus: 'Active', technologyStack: '', repositoryLink: null, pipelineLink: null, applicationHealthScore: 0, qualityScore: 0, automationCoverage: 0, unitTestCoverage: 0, openDefects: 0, openCriticalDefects: 0, riskLevel: 'Low' };
const textFields = [ ['name', 'Name'], ['description', 'Description'], ['productArea', 'Product area'], ['businessOwner', 'Business owner'], ['technologyOwner', 'Technology owner'], ['etsOwner', 'ETS owner'], ['technologyStack', 'Technology stack'], ['repositoryLink', 'Repository link'], ['pipelineLink', 'Pipeline link'] ] as const;
const numberFields = [ ['tier', 'Tier', 1, 4], ['applicationHealthScore', 'Health score', 0, 100], ['qualityScore', 'Quality score', 0, 100], ['automationCoverage', 'Automation coverage %', 0, 100], ['unitTestCoverage', 'Unit-test coverage %', 0, 100], ['openDefects', 'Open defects', 0, undefined], ['openCriticalDefects', 'Open critical defects', 0, undefined] ] as const;

/** Create or edit all inventory fields using server-authorized actions. */
export default function ApplicationForm() {
  const { id } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState<ApplicationInput>(empty);
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    async function load() {
      const request = id ? api<Application>(`/applications/${id}`, {}, token || undefined) : api<ApplicationInventoryPage>('/applications?limit=1', {}, token || undefined);
      const result = await request;
      const all: Portfolio[] = [];
      let offset = 0;
      while (true) {
        const page = await api<{items: Portfolio[]; total: number}>(`/portfolios?limit=100&offset=${offset}`, {}, token || undefined);
        all.push(...page.items);
        offset += page.items.length;
        if (!page.items.length || offset >= page.total) break;
      }
      if (!active) return;
      setPortfolios(all);
      if ('canEdit' in result) {
        setAllowed(result.canEdit);
        const fields = Object.keys(empty) as (keyof ApplicationInput)[];
        setForm(Object.fromEntries(fields.map(key => [key, result[key]])) as unknown as ApplicationInput);
      } else setAllowed(result.canCreate);
    }
    load().catch(err => { if (active) setError(err instanceof Error ? err.message : 'Could not load form'); });
    return () => { active = false; };
  }, [id, token]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setSaving(true);
    try {
      const saved = await api<Application>(id ? `/applications/${id}` : '/applications', { method: id ? 'PUT' : 'POST', body: JSON.stringify(form) }, token || undefined);
      navigate(`/applications/${saved.id}`);
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save application'); setSaving(false); }
  }
  return (
    <main className="content">
      <Link to={id ? `/applications/${id}` : '/applications'}>← Cancel</Link>
      <h1>{id ? 'Edit application' : 'Create application'}</h1>
      {error && <p role="alert" className="error">{error}</p>}
      {allowed === null && !error && <p role="status">Checking access…</p>}
      {allowed === false && <p role="alert">You cannot change this application.</p>}
      {allowed && <form className="portfolio-form" onSubmit={submit}>
        {textFields.map(([key, label]) => <div key={key}>
          <label htmlFor={key}>{label}</label>
          {key === 'description' || key === 'technologyStack' ? <textarea id={key} maxLength={4000} value={form[key] || ''} onChange={e => setForm({ ...form, [key]: e.target.value })} /> : <input id={key} required={key === 'name'} maxLength={key.includes('Link') ? 2048 : 200} type={key.includes('Link') ? 'url' : 'text'} value={form[key] || ''} onChange={e => setForm({ ...form, [key]: e.target.value || (key.includes('Link') ? null : '') })} />}
        </div>)}
        <label htmlFor="portfolioId">Portfolio</label>
        <select id="portfolioId" required value={form.portfolioId} onChange={e => setForm({ ...form, portfolioId: e.target.value })}>
          <option value="">Select a portfolio</option>
          {portfolios.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label htmlFor="criticality">Criticality</label>
        <select id="criticality" value={form.criticality} onChange={e => setForm({ ...form, criticality: e.target.value as ApplicationInput['criticality'] })}>{['Low', 'Medium', 'High', 'Critical'].map(v => <option key={v}>{v}</option>)}</select>
        <label htmlFor="lifecycleStatus">Lifecycle status</label>
        <select id="lifecycleStatus" value={form.lifecycleStatus} onChange={e => setForm({ ...form, lifecycleStatus: e.target.value as ApplicationInput['lifecycleStatus'] })}>{['Active', 'Deprecated', 'Retired'].map(v => <option key={v}>{v}</option>)}</select>
        <label htmlFor="riskLevel">Risk level</label>
        <select id="riskLevel" value={form.riskLevel} onChange={e => setForm({ ...form, riskLevel: e.target.value as ApplicationInput['riskLevel'] })}>{['Low', 'Medium', 'High'].map(v => <option key={v}>{v}</option>)}</select>
        {numberFields.map(([key, label, min, max]) => <div key={key}><label htmlFor={key}>{label}</label><input id={key} type="number" required min={min} max={max} step={1} value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value === '' ? '' : Number(e.target.value) })} /></div>)}
        <button disabled={saving}>{saving ? 'Saving…' : 'Save application'}</button>
      </form>}
    </main>
  );
}
