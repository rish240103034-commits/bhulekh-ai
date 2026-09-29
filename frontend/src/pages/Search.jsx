import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { globalSearch } from '../api/client'

const KIND_LABEL = {
  record: 'Verified record', field: 'Extracted field',
  ocr: 'Raw OCR text', parcel: 'Parcel row',
}
const KIND_BADGE = { record: 'verified', field: 'info', ocr: 'uploaded', parcel: 'pending_review' }

// Global search across records, extracted fields, parcels, and raw OCR text.
// The reviewer/officer can type a name, a khasra number, a village or any word
// they remember seeing — and land at the right document.
export default function Search() {
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') || '')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const run = async (query) => {
    if (!query || query.trim().length < 2) { setData(null); return }
    setLoading(true); setError(null)
    try {
      const { data } = await globalSearch(query.trim())
      setData(data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Search failed.')
      setData(null)
    } finally { setLoading(false) }
  }

  useEffect(() => { run(params.get('q') || '') /* eslint-disable-next-line */ }, [params])

  const submit = (e) => {
    e.preventDefault()
    setParams(q ? { q } : {})
  }

  const groupedByDoc = (hits) => {
    const g = new Map()
    for (const h of hits) {
      if (!g.has(h.document_id)) g.set(h.document_id, [])
      g.get(h.document_id).push(h)
    }
    return [...g.entries()]
  }

  return (
    <>
      <h1>Search everything</h1>
      <form className="card" onSubmit={submit} style={{ marginBottom: 14 }}>
        <div className="row" style={{ gap: 8 }}>
          <input type="text" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="Owner, khasra, village, mutation number, or any word from an OCR page…"
                 autoFocus style={{ flex: 1 }} />
          <button className="btn" disabled={q.trim().length < 2}>Search</button>
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Searches records, per-parcel data, extracted fields, and raw OCR text — de-duplicated per document.
        </div>
      </form>

      {loading && <p className="muted">Searching…</p>}
      {error && <div className="alert err">{error}</div>}
      {data && !loading && (
        <>
          <div className="muted" style={{ marginBottom: 10 }}>
            <b>{data.hits.length}</b> match{data.hits.length === 1 ? '' : 'es'} for <code>{data.query}</code>
          </div>
          {data.hits.length === 0 && (
            <div className="card">
              <p className="muted">Nothing matched. Try a shorter or partial term — e.g. just the family name, or a khasra digit sequence.</p>
            </div>
          )}
          {groupedByDoc(data.hits).map(([docId, hits]) => (
            <div key={docId} className="card" style={{ marginBottom: 10 }}>
              <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
                <div>
                  <Link to={`/documents/${docId}`} style={{ fontWeight: 600 }}>{hits[0].extra?.filename || docId}</Link>
                  {hits[0].extra?.status && <span className={`badge ${KIND_BADGE[hits[0].kind] || 'info'}`} style={{ marginLeft: 8 }}>{hits[0].extra.status.replace('_', ' ')}</span>}
                </div>
                <Link className="btn sm ghost" to={`/documents/${docId}`}>Open</Link>
              </div>
              {hits.map((h, i) => (
                <div key={i} style={{ padding: '6px 0', borderTop: i === 0 ? 'none' : '1px dashed var(--line)' }}>
                  <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>{KIND_LABEL[h.kind] || h.kind}</div>
                  <div style={{ fontSize: 13 }}>{h.snippet}</div>
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </>
  )
}
