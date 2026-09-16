import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listDocuments } from '../api/client'
import { Badge, Conf } from '../components/Layout'

const STATUSES = ['', 'pending_review', 'auto_verified', 'verified', 'rejected', 'processing', 'failed']

export default function Documents() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [q, setQ] = useState({ status: '', q: '', page: 1, size: 20 })
  const nav = useNavigate()

  const load = () => listDocuments({ ...q, status: q.status || undefined, q: q.q || undefined }).then((r) => setData(r.data))
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [q])

  const pages = Math.max(1, Math.ceil(data.total / q.size))
  return (
    <>
      <h1>Documents &amp; verification queue</h1>
      <div className="toolbar">
        <div><label>Status</label><select value={q.status} onChange={(e) => setQ({ ...q, status: e.target.value, page: 1 })}>{STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace('_', ' ') : 'All statuses'}</option>)}</select></div>
        <div style={{ minWidth: 280 }}><label>Search (filename or OCR text)</label><input type="text" value={q.q} onChange={(e) => setQ({ ...q, q: e.target.value, page: 1 })} placeholder="e.g. Rampur, khasra 123" /></div>
        <span className="muted">{data.total} document(s)</span>
      </div>
      <div className="card">
        <table>
          <thead><tr><th>File</th><th>Type</th><th>Language</th><th>State / District</th><th>Pages</th><th>Parcels</th><th>Quality</th><th>Confidence</th><th>Status</th><th>Uploaded</th></tr></thead>
          <tbody>
            {data.items.map((d) => (
              <tr key={d.id} className="clickable" onClick={() => nav(`/documents/${d.id}`)}>
                <td>{d.original_filename}</td><td>{d.doc_type}</td><td>{d.language}</td>
                <td>{d.state || '—'} / {d.district || '—'}</td><td>{d.page_count}</td><td>{d.parcel_count || '—'}</td>
                <td>{d.quality_score}%</td><td><Conf v={d.overall_confidence} /></td>
                <td><Badge v={d.status} /></td><td className="muted">{new Date(d.created_at).toLocaleString()}</td>
              </tr>))}
            {data.items.length === 0 && <tr><td colSpan={10} className="muted">No documents yet — upload some to get started.</td></tr>}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn sm ghost" disabled={q.page <= 1} onClick={() => setQ({ ...q, page: q.page - 1 })}>Prev</button>
          <span className="muted">Page {q.page} / {pages}</span>
          <button className="btn sm ghost" disabled={q.page >= pages} onClick={() => setQ({ ...q, page: q.page + 1 })}>Next</button>
        </div>
      </div>
    </>
  )
}
