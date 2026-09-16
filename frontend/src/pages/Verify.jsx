import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { currentUser, fetchBlobUrl, fileUrl, getDocument, reprocessDocument, verifyDocument } from '../api/client'
import { Badge, Conf } from '../components/Layout'

const FIELD_ORDER = ['owner_name', 'father_or_husband_name', 'state', 'district', 'tehsil', 'village', 'survey_number',
  'khasra_number', 'khata_number', 'plot_area', 'land_classification', 'ownership_type', 'mutation_number',
  'mutation_date', 'registration_number', 'registration_date', 'record_year']
const LABEL = (f) => f.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export default function Verify() {
  const { id } = useParams()
  const nav = useNavigate()
  const user = currentUser()
  const [doc, setDoc] = useState(null)
  const [values, setValues] = useState({})
  const [parcels, setParcels] = useState([])
  const [remarks, setRemarks] = useState('')
  const [page, setPage] = useState(1)
  const [processed, setProcessed] = useState(false)
  const [imgSrc, setImgSrc] = useState(null)
  const [hl, setHl] = useState(null)
  const [scale, setScale] = useState(1)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const imgRef = useRef()

  const load = async () => {
    const { data } = await getDocument(id)
    setDoc(data)
    const v = {}
    data.fields.forEach((f) => { v[f.field_name] = f.corrected_value ?? f.normalized_value ?? f.value ?? '' })
    setValues(v)
    setParcels((data.parcels || []).map((p) => ({ ...p })))
  }
  useEffect(() => { load() }, [id])
  useEffect(() => {
    if (!doc) return
    let alive = true
    // PDFs are shown via their rendered/processed page image; images can toggle original vs enhanced
    const useProcessed = processed || doc.mime_type === 'application/pdf'
    fetchBlobUrl(fileUrl(id, useProcessed, page)).then((u) => alive && setImgSrc(u)).catch(() => alive && setImgSrc(null))
    return () => { alive = false }
  }, [doc, page, processed])

  // still processing? poll
  useEffect(() => {
    if (doc && ['uploaded', 'processing'].includes(doc.status)) { const t = setTimeout(load, 2500); return () => clearTimeout(t) }
  }, [doc])

  if (!doc) return <p>Loading…</p>
  const fmap = Object.fromEntries(doc.fields.map((f) => [f.field_name, f]))
  const names = [...FIELD_ORDER, ...doc.fields.map((f) => f.field_name).filter((n) => !FIELD_ORDER.includes(n))]
  const canVerify = ['admin', 'verifier'].includes(user.role) && ['pending_review', 'auto_verified', 'extracted', 'verified'].includes(doc.status)

  const decide = async (decision) => {
    setBusy(true); setMsg(null)
    try {
      const corrections = names.filter((n) => (values[n] ?? '') !== (fmap[n]?.normalized_value ?? '') || (fmap[n] == null && values[n]))
        .map((n) => ({ field_name: n, value: values[n] || null }))
      const parcelPayload = parcels.map((p, i) => ({
        id: p.id && !String(p.id).startsWith('new-') ? p.id : null,
        row_index: i,
        parcel_number: p.parcel_number || null, area_text: p.area_text || null,
        land_classification: p.land_classification || null, crop: p.crop || null,
        owner_name: p.owner_name || null, possessor_name: p.possessor_name || null,
        father_name: p.father_name || null, share: p.share || null, remarks: p.remarks || null,
      }))
      await verifyDocument(id, { decision, corrections, parcels: parcelPayload, remarks })
      setMsg({ ok: true, text: `Document ${decision}d. ${corrections.length} correction(s) stored for model learning.` })
      await load()
    } catch (e) { setMsg({ ok: false, text: e.response?.data?.detail || 'Failed' }) } finally { setBusy(false) }
  }

  const highlightBox = (bbox, pageNo) => {
    if (!bbox || !imgRef.current) { setHl(null); return }
    // Bounding boxes are in enhanced-image coordinates, so show that view to line them up.
    if (!processed && doc.mime_type !== 'application/pdf') { setProcessed(true) }
    const r = imgRef.current.naturalWidth ? imgRef.current.clientWidth / imgRef.current.naturalWidth : 1
    setHl({ left: bbox.x * r, top: bbox.y * r, width: bbox.w * r, height: bbox.h * r })
    if (pageNo && pageNo !== page) setPage(pageNo)
  }

  const highlight = (f) => highlightBox(f?.bbox, f?.page)

  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div><h1 style={{ margin: 0 }}>{doc.original_filename}</h1>
          <span className="muted">{doc.doc_type} · {doc.language} · {doc.page_count} page(s) · quality {doc.quality_score}% · {doc.processing_ms} ms{parcels.length > 0 ? ` · ${parcels.length} parcels` : ''}</span></div>
        <div className="row"><Badge v={doc.status} /><Conf v={doc.overall_confidence} />
          <button className="btn sm ghost" onClick={() => nav('/documents')}>Back</button>
          {['admin', 'operator', 'verifier'].includes(user.role) && <button className="btn sm ghost" onClick={async () => { setBusy(true); await reprocessDocument(id); await load(); setBusy(false) }} disabled={busy}>Re-run AI</button>}
        </div>
      </div>
      {doc.error && <div className="alert err">{doc.error}</div>}
      {doc.diagnostics?.warning && <div className="alert warn">{doc.diagnostics.warning}</div>}
      {msg && <div className={`alert ${msg.ok ? 'ok' : 'err'}`}>{msg.text}</div>}

      <div className="verify">
        <div className="card">
          <div className="row" style={{ marginBottom: 8 }}>
            <label style={{ margin: 0 }}><input type="checkbox" checked={processed} onChange={(e) => setProcessed(e.target.checked)} /> show enhanced (OCR) image</label>
            {doc.page_count > 1 && <span className="row">Page <button className="btn sm ghost" onClick={() => setPage(Math.max(1, page - 1))}>‹</button>{page}/{doc.page_count}<button className="btn sm ghost" onClick={() => setPage(Math.min(doc.page_count, page + 1))}>›</button></span>}
            <span className="row">Zoom <input type="range" min="0.5" max="2.5" step="0.1" value={scale} onChange={(e) => setScale(+e.target.value)} /></span>
          </div>
          <div className="imgbox">
            {imgSrc ? <div style={{ position: 'relative', width: `${scale * 100}%` }}>
              <img ref={imgRef} src={imgSrc} alt="document" onLoad={() => setHl(null)} />
              {hl && <div className="hl" style={hl} />}</div> : <p className="muted" style={{ padding: 20 }}>Image not available (still processing?)</p>}
          </div>
          <details style={{ marginTop: 10 }}><summary className="muted">Raw OCR text</summary><pre className="ocr">{doc.ocr_text}</pre></details>
          {doc.diagnostics && (
            <details style={{ marginTop: 6 }}>
              <summary className="muted">Extraction diagnostics — why the page read the way it did</summary>
              <pre className="ocr">{JSON.stringify(doc.diagnostics, null, 2)}</pre>
            </details>
          )}
        </div>

        <div className="card">
          <h2>Extracted fields <span className="muted" style={{ fontWeight: 400 }}>— click a field to locate it on the scan</span></h2>
          {names.map((n) => {
            const f = fmap[n]
            const review = f ? f.needs_review && !f.corrected_value : true
            return (
              <div key={n} className={`fieldrow ${review ? 'review' : ''}`} onClick={() => highlight(f)}>
                <div className="name">{LABEL(n)}{f?.source === 'human' && ' ✓'}{f?.source === 'learned' && ' ⟲'}</div>
                <input type="text" value={values[n] ?? ''} disabled={!canVerify} placeholder={f ? '' : 'not found — enter manually'}
                  onChange={(e) => setValues({ ...values, [n]: e.target.value })} title={f?.value ? `OCR raw: ${f.value}` : ''} />
                <div>{f ? <Conf v={f.confidence} /> : <span className="badge warning">missing</span>}</div>
              </div>)
          })}

          <h2 style={{ marginTop: 16 }}>Validation results</h2>
          <table><tbody>
            {doc.validations.map((v, i) => (
              <tr key={i}><td style={{ width: 90 }}><span className={`badge ${v.passed ? 'info' : v.severity}`}>{v.passed ? 'pass' : v.severity}</span></td>
                <td><b className="muted" style={{ fontSize: 11 }}>{v.rule_id}</b><br />{v.message}</td></tr>))}
          </tbody></table>

          {doc.record && <p className="muted" style={{ fontSize: 12 }}>Canonical record: {doc.record.village} · khasra {doc.record.khasra_number || '—'} · survey {doc.record.survey_number || '—'} · {doc.record.plot_area_sqm ? `${doc.record.plot_area_sqm} m²` : 'area n/a'} · {doc.record.is_verified ? `verified by ${doc.record.verified_by}` : 'unverified'}</p>}

        </div>
      </div>

      {(parcels.length > 0 || canVerify) && (
        <div className="card" style={{ marginTop: 16 }}>
          {parcels.length > 0 && (
            <ParcelTable parcels={parcels} setParcels={setParcels} canEdit={canVerify}
                         onLocate={(p) => highlightBox(p.bbox, p.page)} />
          )}
          {canVerify && (
            <>
              <div className="field" style={{ marginTop: 14, maxWidth: 620 }}>
                <label>Verifier remarks</label>
                <textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} />
              </div>
              <div className="row">
                <button className="btn ok" disabled={busy} onClick={() => decide('approve')}>Approve &amp; commit record</button>
                <button className="btn err" disabled={busy} onClick={() => decide('reject')}>Reject</button>
              </div>
            </>
          )}
        </div>
      )}
    </>
  )
}


const PARCEL_COLUMNS = [
  ['parcel_number', 'Khasra / Survey no.', 110],
  ['area_text', 'Area', 90],
  ['land_classification', 'Land type', 110],
  ['crop', 'Crop', 100],
  ['owner_name', 'Owner', 150],
  ['possessor_name', 'Possessor', 150],
  ['remarks', 'Remarks', 120],
]

function ParcelTable({ parcels, setParcels, canEdit, onLocate }) {
  const update = (i, key, val) => setParcels(parcels.map((p, j) => (j === i ? { ...p, [key]: val } : p)))
  const addRow = () => setParcels([...parcels, { id: `new-${Date.now()}`, source: 'human', confidence: 100 }])
  const removeRow = (i) => setParcels(parcels.filter((_, j) => j !== i))
  const totalSqm = parcels.reduce((a, p) => a + (p.area_sqm || 0), 0)

  return (
    <>
      <h2>
        Parcel table <span className="muted" style={{ fontWeight: 400 }}>
          — {parcels.length} row(s) read from the khasra grid; click a row to locate it on the scan
        </span>
      </h2>
      <div style={{ overflowX: 'auto' }}>
        <table className="parcels">
          <thead>
            <tr>
              <th style={{ width: 28 }}>#</th>
              {PARCEL_COLUMNS.map(([k, label, w]) => <th key={k} style={{ minWidth: w }}>{label}</th>)}
              <th style={{ width: 96 }}>Confidence</th>
              {canEdit && <th style={{ width: 30 }} />}
            </tr>
          </thead>
          <tbody>
            {parcels.map((p, i) => (
              <tr key={p.id || i} className={p.needs_review ? 'review' : ''} onClick={() => onLocate(p)}>
                <td className="muted">{i + 1}</td>
                {PARCEL_COLUMNS.map(([k]) => (
                  <td key={k}>
                    <input type="text" value={p[k] ?? ''} disabled={!canEdit}
                      title={p.field_confidences?.[k === 'area_text' ? 'area' : k] != null
                        ? `OCR confidence ${Math.round(p.field_confidences[k === 'area_text' ? 'area' : k])}%` : ''}
                      onChange={(e) => update(i, k, e.target.value)} onClick={(e) => e.stopPropagation()} />
                  </td>
                ))}
                <td><Conf v={p.confidence} /></td>
                {canEdit && (
                  <td><button className="btn sm ghost" title="Remove this row"
                    onClick={(e) => { e.stopPropagation(); removeRow(i) }}>×</button></td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        {canEdit && <button className="btn sm ghost" onClick={addRow}>+ Add parcel row</button>}
        <span className="muted">
          Total of parcel areas: {totalSqm ? `${totalSqm.toFixed(0)} m² (${(totalSqm / 10000).toFixed(4)} ha)` : '—'}
        </span>
      </div>
    </>
  )
}
