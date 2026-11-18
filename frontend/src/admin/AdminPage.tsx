import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api';
import { useAuth } from '../auth/AuthContext';
import type { User } from '../types';

type Account = User & { managerName: string | null; vendorIndicator: boolean };
type Role = { id: string; name: string; description: string; permissions: string[] };
type Page<T> = { items: T[]; total: number };
type Form = { name: string; email: string; role: string; portfolioAccess: string[]; status: string; managerName: string; vendorIndicator: boolean; password: string };
const empty: Form = { name: '', email: '', role: 'Engineer', portfolioAccess: [], status: 'Active', managerName: '', vendorIndicator: false, password: '' };
const permissions = ['view', 'create', 'edit', 'delete', 'export', 'administer'];

/** Manage local accounts and role permissions through the protected API. */
export default function AdminPage() {
  const { token } = useAuth();
  const [tab, setTab] = useState<'users' | 'roles'>('users');
  const [users, setUsers] = useState<Account[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [portfolios, setPortfolios] = useState<{ id: string; name: string }[]>([]);
  const [portfolioOffset, setPortfolioOffset] = useState(0);
  const [portfolioTotal, setPortfolioTotal] = useState(0);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [form, setForm] = useState<Form>(empty);
  const [editing, setEditing] = useState<string | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  async function load() {
    setLoading(true);
    try {
      const [u, r, p] = await Promise.all([
        api<Page<Account>>(`/users?limit=25&offset=${offset}`, {}, token || undefined),
        api<Page<Role>>('/roles?limit=100', {}, token || undefined),
        api<Page<{ id: string; name: string }>>(`/admin/portfolios?limit=100&offset=${portfolioOffset}`, {}, token || undefined),
      ]);
      setUsers(u.items); setTotal(u.total); setRoles(r.items); setPortfolios(p.items); setPortfolioTotal(p.total); setError('');
    } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [token, offset, portfolioOffset]);
  function edit(user: Account) {
    setEditing(user.id);
    setForm({ name: user.name, email: user.email, role: user.role, portfolioAccess: user.portfolioAccess, status: user.status, managerName: user.managerName || '', vendorIndicator: user.vendorIndicator, password: '' });
    setNotice('');
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await api(`/users${editing ? `/${editing}` : ''}`, { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ ...form, managerName: form.managerName || null, password: form.password || null }) }, token || undefined);
      setEditing(null); setForm(empty); setNotice('User saved.'); await load();
    } catch (e) { setError((e as Error).message); }
  }
  async function deactivate(id: string) {
    if (!window.confirm('Deactivate this user? Existing audit history will be retained.')) return;
    try { await api(`/users/${id}`, { method: 'DELETE' }, token || undefined); setNotice('User deactivated.'); await load(); }
    catch (e) { setError((e as Error).message); }
  }
  async function saveRole(event: FormEvent) {
    event.preventDefault();
    if (!role) return;
    try { await api(`/roles/${role.id}`, { method: 'PUT', body: JSON.stringify({ name: role.name, description: role.description, permissions: role.permissions }) }, token || undefined); setRole(null); setNotice('Role saved.'); await load(); }
    catch (e) { setError((e as Error).message); }
  }
  return (
    <main className="content">
      <h1>Administration</h1>
      <p>Manage local accounts and role permissions. Role-specific restrictions still apply.</p>
      <div className="portfolio-actions" aria-label="Administration sections">
        <button type="button" onClick={() => setTab('users')} aria-pressed={tab === 'users'}>Users</button>
        <button type="button" onClick={() => setTab('roles')} aria-pressed={tab === 'roles'}>Roles</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">Loading administration…</p>}
      {tab === 'users' && <>
        <h2>Users</h2>
        <div className="table-wrap"><table><thead><tr><th scope="col">Name</th><th scope="col">Email</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead><tbody>
          {users.map(u => <tr key={u.id}><td>{u.name}</td><td>{u.email}</td><td>{u.role}</td><td>{u.status}</td><td><button type="button" onClick={() => edit(u)}>Edit {u.name}</button> {u.status === 'Active' && <button type="button" onClick={() => void deactivate(u.id)}>Deactivate {u.name}</button>}</td></tr>)}
        </tbody></table></div>
        <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button><span>{offset + 1}–{Math.min(offset + 25, total)} of {total}</span><button disabled={offset + 25 >= total} onClick={() => setOffset(offset + 25)}>Next</button></div>
        <section className="card portfolio-form"><h2>{editing ? 'Edit user' : 'Create user'}</h2>
          <form onSubmit={save}>
            <label htmlFor="admin-name">Name</label><input id="admin-name" required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
            <label htmlFor="admin-email">Email</label><input id="admin-email" type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} />
            <label htmlFor="admin-password">{editing ? 'Reset password (optional)' : 'Initial password'}</label><input id="admin-password" type="password" minLength={12} maxLength={72} required={!editing} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} autoComplete="new-password" />
            <label htmlFor="admin-role">Role</label><select id="admin-role" value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>{roles.map(r => <option key={r.id}>{r.name}</option>)}</select>
            <label htmlFor="admin-status">Status</label><select id="admin-status" value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}><option>Active</option><option>Inactive</option></select>
            <label htmlFor="admin-manager">Manager name</label><input id="admin-manager" value={form.managerName} onChange={e => setForm({ ...form, managerName: e.target.value })} />
            <label><input type="checkbox" checked={form.vendorIndicator} onChange={e => setForm({ ...form, vendorIndicator: e.target.checked })} /> Vendor</label>
            <fieldset><legend>Portfolio access</legend><p>Empty means all for Administrator, Executive and Auditor; none for other roles.</p>{portfolios.map(p => <label key={p.id} className="admin-check"><input type="checkbox" checked={form.portfolioAccess.includes(p.id)} onChange={e => setForm({ ...form, portfolioAccess: e.target.checked ? [...form.portfolioAccess, p.id] : form.portfolioAccess.filter(id => id !== p.id) })} /> {p.name}</label>)}<div className="pagination"><button type="button" disabled={portfolioOffset === 0} onClick={() => setPortfolioOffset(Math.max(0, portfolioOffset - 100))}>Previous portfolios</button><span>{portfolioTotal ? portfolioOffset + 1 : 0}–{Math.min(portfolioOffset + 100, portfolioTotal)} of {portfolioTotal}</span><button type="button" disabled={portfolioOffset + 100 >= portfolioTotal} onClick={() => setPortfolioOffset(portfolioOffset + 100)}>Next portfolios</button></div></fieldset>
            <button type="submit">Save user</button>{editing && <button type="button" onClick={() => { setEditing(null); setForm(empty); }}>Cancel edit</button>}
          </form>
        </section>
      </>}
      {tab === 'roles' && <><h2>Roles</h2><div className="table-wrap"><table><thead><tr><th scope="col">Role</th><th scope="col">Permissions</th><th scope="col">Action</th></tr></thead><tbody>{roles.map(r => <tr key={r.id}><td>{r.name}</td><td>{r.permissions.join(', ')}</td><td><button onClick={() => setRole(r)}>Edit {r.name}</button></td></tr>)}</tbody></table></div>
        {role && <section className="card portfolio-form"><h2>Edit {role.name}</h2><form onSubmit={saveRole}><label htmlFor="role-description">Description</label><input id="role-description" value={role.description} onChange={e => setRole({ ...role, description: e.target.value })} /><fieldset><legend>Permissions</legend>{permissions.map(p => <label key={p} className="admin-check"><input type="checkbox" checked={role.permissions.includes(p)} onChange={e => setRole({ ...role, permissions: e.target.checked ? [...role.permissions, p] : role.permissions.filter(item => item !== p) })} /> {p}</label>)}</fieldset><button>Save role</button><button type="button" onClick={() => setRole(null)}>Cancel role edit</button></form></section>}
      </>}
    </main>
  );
}
