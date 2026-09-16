import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis } from 'recharts'
import { getStats } from '../api/client'

const COLORS = { verified: '#2e8b57', auto_verified: '#5fb87f', pending_review: '#d98c00', rejected: '#c0392b',
  failed: '#8e3b2f', processing: '#4a6fa5', uploaded: '#9bb0c9', extracted: '#7a95b8' }
const PALETTE = ['#1f4e79', '#2e75b6', '#5fa8d3', '#2e8b57', '#d98c00', '#c0392b', '#7f8c8d']

const Kpi = ({ label, value, sub }) => (
  <div className="card kpi"><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="sub">{sub}</div>}</div>
)

export default function Dashboard() {
  const [s, setS] = useState(null)
  const [filters, setFilters] = useState({ state: '', district: '' })

  const load = () => getStats({ state: filters.state || undefined, district: filters.district || undefined }).then((r) => setS(r.data))
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [filters])

  if (!s) return <p>Loading…</p>
  const statusData = Object.entries(s.status_breakdown).filter(([, v]) => v > 0).map(([name, value]) => ({ name, value }))
  const typeData = Object.entries(s.doc_type_breakdown).map(([name, value]) => ({ name, value }))

  return (
    <>
      <h1>Digitization dashboard</h1>
      <div className="toolbar">
        <div><label>State</label><input type="text" placeholder="All states" value={filters.state} onChange={(e) => setFilters({ ...filters, state: e.target.value })} /></div>
        <div><label>District</label><input type="text" placeholder="All districts" value={filters.district} onChange={(e) => setFilters({ ...filters, district: e.target.value })} /></div>
        <span className="muted">Auto-refreshes every 10 s</span>
      </div>

      <div className="grid kpi" style={{ marginBottom: 14 }}>
        <Kpi label="Documents processed" value={s.processed} sub={`${s.total_documents} uploaded`} />
        <Kpi label="Extraction confidence" value={`${s.avg_extraction_confidence}%`} sub="mean across fields" />
        <Kpi label="Field accuracy (post-review)" value={`${s.avg_field_accuracy}%`} sub="fields not corrected by verifiers" />
        <Kpi label="Pending verification" value={s.pending_review} sub="need human review" />
        <Kpi label="Verified records" value={s.verified} sub={`${s.auto_verified_rate}% auto-verified`} />
        <Kpi label="Errors / rejected" value={s.failed + s.rejected} sub={`${s.failed} failed, ${s.rejected} rejected`} />
        <Kpi label="Avg processing time" value={`${(s.avg_processing_ms / 1000).toFixed(1)} s`} sub="per document" />
      </div>

      <div className="grid two" style={{ marginBottom: 14 }}>
        <div className="card">
          <h2>Validation status</h2>
          <ResponsiveContainer width="100%" height={240}>
            <PieChart><Pie data={statusData} dataKey="value" nameKey="name" innerRadius={55} outerRadius={90} label>
              {statusData.map((d) => <Cell key={d.name} fill={COLORS[d.name] || '#999'} />)}</Pie><Tooltip /><Legend /></PieChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <h2>Daily throughput (last 14 days)</h2>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={s.daily_throughput}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" tick={{ fontSize: 10 }} /><YAxis allowDecimals={false} /><Tooltip />
              <Line type="monotone" dataKey="documents" stroke="#2e75b6" strokeWidth={2} /></LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid two" style={{ marginBottom: 14 }}>
        <div className="card">
          <h2>State-wise digitization progress</h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={s.by_state} layout="vertical" margin={{ left: 40 }}><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" /><YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11 }} /><Tooltip /><Legend />
              <Bar dataKey="verified" stackId="a" fill="#2e8b57" /><Bar dataKey="pending" stackId="a" fill="#d98c00" /><Bar dataKey="failed" stackId="a" fill="#c0392b" /></BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <h2>District-wise progress</h2>
          <table><thead><tr><th>District</th><th>Total</th><th>Verified</th><th>Pending</th><th>Progress</th></tr></thead>
            <tbody>{s.by_district.slice(0, 10).map((d) => (
              <tr key={d.name}><td>{d.name}</td><td>{d.total}</td><td>{d.verified}</td><td>{d.pending}</td>
                <td><div className="progress" title={`${d.progress_pct}%`}><i style={{ width: `${d.progress_pct}%` }} /></div></td></tr>))}
            </tbody></table>
        </div>
      </div>

      <div className="grid two">
        <div className="card">
          <h2>Error statistics (failed validation rules)</h2>
          {s.error_statistics.length === 0 ? <p className="muted">No validation failures.</p> :
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={s.error_statistics}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="rule" tick={{ fontSize: 10 }} interval={0} angle={-20} height={60} /><YAxis allowDecimals={false} /><Tooltip />
                <Bar dataKey="count">{s.error_statistics.map((e, i) => <Cell key={i} fill={e.severity === 'error' ? '#c0392b' : '#d98c00'} />)}</Bar></BarChart>
            </ResponsiveContainer>}
        </div>
        <div className="card">
          <h2>Field-level confidence</h2>
          <table><thead><tr><th>Field</th><th>Avg confidence</th><th>Needs review</th></tr></thead>
            <tbody>{s.low_confidence_fields.map((f) => (
              <tr key={f.field}><td>{f.field}</td><td>{f.avg_confidence}%</td><td>{f.needs_review}</td></tr>))}</tbody></table>
          <h2 style={{ marginTop: 14 }}>Document types</h2>
          <div className="row">{typeData.map((t, i) => <span key={t.name} className="badge" style={{ background: PALETTE[i % PALETTE.length], color: '#fff' }}>{t.name}: {t.value}</span>)}</div>
          <h2 style={{ marginTop: 14 }}>Languages</h2>
          <div className="row">{Object.entries(s.language_breakdown).map(([k, v]) => <span key={k} className="badge info">{k}: {v}</span>)}</div>
        </div>
      </div>
    </>
  )
}
