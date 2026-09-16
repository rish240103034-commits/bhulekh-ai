import { useEffect, useState } from 'react'
import { getAudit } from '../api/client'

export default function Audit() {
  const [rows, setRows] = useState([])
  const [f, setF] = useState({ action: '', entity_id: '' })
  useEffect(() => { getAudit({ action: f.action || undefined, entity_id: f.entity_id || undefined, limit: 200 }).then((r) => setRows(r.data)) }, [f])
  return (
    <>
      <h1>Audit trail</h1>
      <div className="toolbar">
        <div><label>Action prefix</label><input type="text" placeholder="document. / auth. / integration." value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} /></div>
        <div style={{ minWidth: 300 }}><label>Entity id</label><input type="text" value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })} /></div>
      </div>
      <div className="card"><table>
        <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id}><td className="muted">{new Date(r.created_at).toLocaleString()}</td><td>{r.actor_name}</td><td><code>{r.action}</code></td>
          <td>{r.entity_type} <span className="muted">{r.entity_id?.slice(0, 8)}</span></td><td><code style={{ fontSize: 11 }}>{r.detail ? JSON.stringify(r.detail) : ''}</code></td></tr>)}</tbody>
      </table></div>
    </>
  )
}
