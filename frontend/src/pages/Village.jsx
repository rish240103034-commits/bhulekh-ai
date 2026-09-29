import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge } from '../components/Layout'
import { getVillageSummary } from '../api/client'

// Consolidated per-village view: aggregate every LandRecord + LandParcel that
// mentions this village, so a district officer or a demo audience can see the
// coverage of a single settlement at a glance.
export default function Village() {
  const { name } = useParams()
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setSummary(null); setError(null)
    getVillageSummary(name).then(({ data }) => setSummary(data))
      .catch((e) => setError(e.response?.status === 404
        ? `No digitized records for village "${name}" yet.`
        : (e.response?.data?.detail || 'Failed to load village summary.')))
  }, [name])

  if (error) return <div className="alert warn">{error}</div>
  if (!summary) return <p>Loading village summary…</p>

  const fmtArea = (sqm) => sqm >= 10000 ? `${(sqm / 10000).toFixed(3)} ha` : `${sqm.toFixed(0)} m²`
  const coverage = summary.record_count > 0
    ? Math.round(100 * summary.verified_count / summary.record_count) : 0

  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h1 style={{ margin: 0 }}>{summary.village}</h1>
          <span className="muted">
            {summary.tehsil ? `Tehsil ${summary.tehsil} · ` : ''}
            {summary.district ? `${summary.district} district · ` : ''}
            {summary.state || 'Unknown state'}
          </span>
        </div>
        <Link className="btn ghost sm" to="/database">← Back to records database</Link>
      </div>

      <div className="grid kpi" style={{ marginBottom: 14 }}>
        <div className="card kpi">
          <div className="label">Digitized records</div>
          <div className="value">{summary.record_count}</div>
          <div className="sub">{summary.parcel_count} parcel row(s) across those documents</div>
        </div>
        <div className="card kpi">
          <div className="label">Verified</div>
          <div className="value">{summary.verified_count}</div>
          <div className="sub">{summary.pending_review_count} still pending human review</div>
        </div>
        <div className="card kpi">
          <div className="label">Verification coverage</div>
          <div className="value">{coverage}%</div>
          <div className="progress" style={{ marginTop: 6 }}>
            <i style={{ width: `${coverage}%` }} />
          </div>
        </div>
        <div className="card kpi">
          <div className="label">Total land area</div>
          <div className="value">{summary.total_area_ha.toFixed(3)}</div>
          <div className="sub">hectares digitised · {summary.khasra_count} unique khasras</div>
        </div>
      </div>

      <div className="grid two">
        <div className="card">
          <h2>Top land holders</h2>
          {summary.owners.length === 0 && <p className="muted">No owner names extracted yet.</p>}
          {summary.owners.length > 0 && (
            <table>
              <thead>
                <tr><th>Owner</th><th style={{ width: 90 }}>Records</th><th>Khasras</th><th style={{ width: 100 }}>Total area</th></tr>
              </thead>
              <tbody>
                {summary.owners.map((o) => (
                  <tr key={o.owner_name}>
                    <td><b>{o.owner_name}</b></td>
                    <td>{o.records}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {o.khasras.slice(0, 8).join(', ')}
                      {o.khasras.length > 8 ? ` +${o.khasras.length - 8} more` : ''}
                    </td>
                    <td>{fmtArea(o.total_area_sqm)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h2>Source documents</h2>
          <table>
            <thead>
              <tr><th>Uploaded</th><th>Status</th><th>Khasra</th><th>Owner</th><th style={{ width: 90 }}>Conf.</th><th></th></tr>
            </thead>
            <tbody>
              {summary.documents.map((d) => (
                <tr key={d.id}>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {d.created_at ? new Date(d.created_at).toLocaleDateString() : '—'}
                  </td>
                  <td><Badge v={d.status || 'uploaded'} /></td>
                  <td>{d.khasra || '—'}</td>
                  <td>{d.owner || '—'}</td>
                  <td>{d.confidence != null ? `${Math.round(d.confidence)}%` : '—'}</td>
                  <td><Link className="btn sm ghost" to={`/documents/${d.id}`}>Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
