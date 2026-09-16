import { useState } from 'react'
import { lookupRecords } from '../api/client'

export default function Lookup() {
  const [q, setQ] = useState({ village: '', khasra_number: '', survey_number: '' })
  const [rows, setRows] = useState(null)
  const search = async (e) => { e.preventDefault(); const { data } = await lookupRecords({ village: q.village, khasra_number: q.khasra_number || undefined, survey_number: q.survey_number || undefined }); setRows(data) }
  return (
    <>
      <h1>Parcel lookup <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>(same API used by citizen-service &amp; GIS integrations)</span></h1>
      <form className="card toolbar" onSubmit={search}>
        <div><label>Village *</label><input type="text" required value={q.village} onChange={(e) => setQ({ ...q, village: e.target.value })} /></div>
        <div><label>Khasra no</label><input type="text" value={q.khasra_number} onChange={(e) => setQ({ ...q, khasra_number: e.target.value })} /></div>
        <div><label>Survey no</label><input type="text" value={q.survey_number} onChange={(e) => setQ({ ...q, survey_number: e.target.value })} /></div>
        <button className="btn">Search verified records</button>
        <a className="btn ghost" href="/api/v1/integration/records/export.csv" onClick={(e) => { e.preventDefault(); alert('Use the API with a bearer token: GET /api/v1/integration/records/export.csv') }}>CSV export API</a>
      </form>
      {rows && <div className="card" style={{ marginTop: 14 }}><table>
        <thead><tr><th>Owner</th><th>Village</th><th>Khasra</th><th>Survey</th><th>Khata</th><th>Area (m²)</th><th>Class</th><th>Mutation</th><th>Verified</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id}><td>{r.owner_name}</td><td>{r.village}</td><td>{r.khasra_number}</td><td>{r.survey_number}</td><td>{r.khata_number}</td><td>{r.plot_area_sqm}</td><td>{r.land_classification}</td><td>{r.mutation_number} {r.mutation_date}</td><td>{r.verified_by}</td></tr>)}
          {rows.length === 0 && <tr><td colSpan={9} className="muted">No verified record found.</td></tr>}</tbody>
      </table></div>}
    </>
  )
}
