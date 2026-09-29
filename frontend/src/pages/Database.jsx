import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '../components/Layout'
import { listRecordsFlat } from '../api/client'

// A read-only showcase of the canonical LandRecord table, fanned out so each parcel of
// a multi-parcel document (e.g. a khasra sheet with 4 khasra rows) becomes its own row.
// A document with no parcel table appears once, as its header row.
export default function Database() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState({ state: '', district: '', village: '', verifiedOnly: false, q: '' })

  const load = async () => {
    setLoading(true)
    try {
      const params = { verified_only: filter.verifiedOnly, size: 1000 }
      if (filter.state) params.state = filter.state
      if (filter.district) params.district = filter.district
      if (filter.village) params.village = filter.village
      const { data } = await listRecordsFlat(params)
      setRows(data)
    } finally { setLoading(false) }
  }
  useEffect(() => { load() /* eslint-disable-next-line */ }, [filter.state, filter.district, filter.village, filter.verifiedOnly])

  const filtered = useMemo(() => {
    if (!filter.q.trim()) return rows
    const q = filter.q.trim().toLowerCase()
    return rows.filter((r) => Object.values(r).some((v) => v != null && String(v).toLowerCase().includes(q)))
  }, [rows, filter.q])

  const stats = useMemo(() => {
    const docs = new Set(rows.map((r) => r.document_id))
    const parcels = rows.filter((r) => r.row_kind === 'parcel').length
    const verified = rows.filter((r) => r.is_verified).length
    const states = new Set(rows.map((r) => r.state).filter(Boolean))
    const districts = new Set(rows.map((r) => r.district).filter(Boolean))
    const totalArea = rows.reduce((a, r) => a + (r.plot_area_sqm || 0), 0)
    return { total: rows.length, docs: docs.size, parcels, verified,
             states: states.size, districts: districts.size, totalArea }
  }, [rows])

  const optionsFor = (key) => Array.from(new Set(rows.map((r) => r[key]).filter(Boolean))).sort()

  const fmtArea = (sqm) => {
    if (!sqm) return '—'
    return sqm >= 10000 ? `${(sqm / 10000).toFixed(3)} ha` : `${sqm.toFixed(0)} m²`
  }

  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h1 style={{ margin: 0 }}>Records database</h1>
          <span className="muted">
            Canonical LandRecord table, fanned out per parcel — what LRMS/DILRMP consumes via
            <code> /integration/records</code>. One document with N khasra rows shows as N rows here.
          </span>
        </div>
        <a className="btn ghost sm" href="/api/v1/integration/records/export.csv"
           onClick={(e) => { e.preventDefault(); alert('The CSV export is an API endpoint that needs a bearer token:\nGET /api/v1/integration/records/export.csv') }}>
          CSV export API
        </a>
      </div>

      <div className="grid kpi" style={{ marginBottom: 14 }}>
        <div className="card kpi">
          <div className="label">Rows</div>
          <div className="value">{stats.total}</div>
          <div className="sub">{stats.parcels} parcel entries from {stats.docs} document(s)</div>
        </div>
        <div className="card kpi">
          <div className="label">Verified</div>
          <div className="value">{stats.verified}</div>
          <div className="sub">{stats.total - stats.verified} pending review</div>
        </div>
        <div className="card kpi">
          <div className="label">Coverage</div>
          <div className="value">{stats.states}</div>
          <div className="sub">{stats.districts} districts</div>
        </div>
        <div className="card kpi">
          <div className="label">Total land area</div>
          <div className="value">{stats.totalArea ? `${(stats.totalArea / 10000).toFixed(2)}` : '—'}</div>
          <div className="sub">hectares digitised</div>
        </div>
      </div>

      <div className="card toolbar">
        <div>
          <label>State</label>
          <select value={filter.state} onChange={(e) => setFilter({ ...filter, state: e.target.value, district: '' })}>
            <option value="">All states</option>
            {optionsFor('state').map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label>District</label>
          <select value={filter.district} onChange={(e) => setFilter({ ...filter, district: e.target.value })}>
            <option value="">All districts</option>
            {optionsFor('district').map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label>Village</label>
          <input type="text" placeholder="Any village" value={filter.village} onChange={(e) => setFilter({ ...filter, village: e.target.value })} />
        </div>
        <div>
          <label>Free-text search</label>
          <input type="text" placeholder="Owner, khasra, crop…" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
        </div>
        <div style={{ minWidth: 'auto' }}>
          <label style={{ visibility: 'hidden' }}>_</label>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', margin: 0 }}>
            <input type="checkbox" checked={filter.verifiedOnly} onChange={(e) => setFilter({ ...filter, verifiedOnly: e.target.checked })} />
            Verified only
          </label>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14, overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>State</th><th>District</th><th>Tehsil</th><th>Village</th>
              <th>Khasra</th><th>Khata</th>
              <th>Owner</th><th>Father / Husband</th><th>Possessor</th>
              <th>Area</th><th>Class</th><th>Crop</th>
              <th>Year</th><th>Status</th><th></th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={15} className="muted">Loading…</td></tr>}
            {!loading && filtered.length === 0 && <tr><td colSpan={15} className="muted">No records match the current filters.</td></tr>}
            {!loading && filtered.map((r, i) => (
              <tr key={`${r.document_id}-${r.parcel_id || 'header'}-${i}`}>
                <td>{r.state || '—'}</td>
                <td>{r.district || '—'}</td>
                <td>{r.tehsil || '—'}</td>
                <td>{r.village
                  ? <Link to={`/village/${encodeURIComponent(r.village)}`} title="Village at a glance">{r.village}</Link>
                  : '—'}</td>
                <td>
                  {r.khasra_number || '—'}
                  {r.row_kind === 'parcel' && r.parcel_row != null && (
                    <span className="badge info" style={{ marginLeft: 6, fontSize: 10 }}>row {r.parcel_row + 1}</span>
                  )}
                </td>
                <td>{r.khata_number || '—'}</td>
                <td><b>{r.owner_name || '—'}</b></td>
                <td>{r.father_or_husband_name || '—'}</td>
                <td>{r.possessor_name || '—'}</td>
                <td>{fmtArea(r.plot_area_sqm)}</td>
                <td>{r.land_classification || '—'}</td>
                <td>{r.crop || '—'}</td>
                <td>{r.record_year || '—'}</td>
                <td><Badge v={r.is_verified ? 'verified' : 'pending_review'} /></td>
                <td>{r.document_id && <Link className="btn sm ghost" to={`/documents/${r.document_id}`}>Open</Link>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && filtered.length > 0 &&
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            Showing {filtered.length} of {rows.length} rows across {stats.docs} document{stats.docs === 1 ? '' : 's'}.
          </div>}
      </div>
    </>
  )
}
