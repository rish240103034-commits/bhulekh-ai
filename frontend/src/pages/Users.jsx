import { useEffect, useState } from 'react'
import { createUser, listUsers, toggleUser } from '../api/client'

const ROLES = ['admin', 'verifier', 'operator', 'viewer', 'integration']
const DESC = { admin: 'Full access, user management, deletion', verifier: 'Reviews & approves records, sees audit trail',
  operator: 'Uploads & re-runs processing (jurisdiction-scoped)', viewer: 'Read-only dashboards & records', integration: 'API client for LRMS/DILRMP sync' }

export default function Users() {
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', password: '', full_name: '', role: 'operator', state: '', district: '' })
  const [err, setErr] = useState('')
  const load = () => listUsers().then((r) => setUsers(r.data))
  useEffect(() => { load() }, [])
  const submit = async (e) => {
    e.preventDefault(); setErr('')
    try { await createUser({ ...form, state: form.state || null, district: form.district || null }); setForm({ ...form, username: '', password: '', full_name: '' }); load() }
    catch (e) { setErr(e.response?.data?.detail || 'Failed') }
  }
  return (
    <>
      <h1>Users &amp; role-based access</h1>
      <div className="grid two">
        <div className="card"><table>
          <thead><tr><th>User</th><th>Role</th><th>Jurisdiction</th><th>Active</th><th></th></tr></thead>
          <tbody>{users.map((u) => <tr key={u.id}><td>{u.full_name}<br /><span className="muted">{u.username}</span></td><td><span className="badge info">{u.role}</span></td>
            <td>{u.state || 'All'} / {u.district || 'All'}</td><td>{u.is_active ? 'yes' : 'no'}</td>
            <td><button className="btn sm ghost" onClick={() => toggleUser(u.id).then(load)}>{u.is_active ? 'Deactivate' : 'Activate'}</button></td></tr>)}</tbody>
        </table></div>
        <form className="card" onSubmit={submit}>
          <h2>Create user</h2>
          {err && <div className="alert err">{err}</div>}
          {['username', 'password', 'full_name', 'state', 'district'].map((k) => (
            <div className="field" key={k}><label>{k.replace('_', ' ')}</label><input type={k === 'password' ? 'password' : 'text'} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} required={['username', 'password'].includes(k)} /></div>))}
          <div className="field"><label>Role</label><select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
            <p className="muted" style={{ fontSize: 12 }}>{DESC[form.role]}</p></div>
          <button className="btn">Create</button>
        </form>
      </div>
    </>
  )
}
