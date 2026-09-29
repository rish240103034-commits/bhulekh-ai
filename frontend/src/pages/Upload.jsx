import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getHealth, uploadDocuments } from '../api/client'

const LANGS = [
  ['eng+hin', 'English + Hindi'], ['hin+eng', 'Hindi (primary) + English'], ['mar+eng', 'Marathi + English'],
  ['guj+eng', 'Gujarati + English'], ['ben+eng', 'Bengali + English'], ['tam+eng', 'Tamil + English'],
  ['tel+eng', 'Telugu + English'], ['kan+eng', 'Kannada + English'], ['mal+eng', 'Malayalam + English'],
  ['pan+eng', 'Punjabi + English'], ['ori+eng', 'Odia + English'], ['urd+eng', 'Urdu + English'], ['eng', 'English only'],
]

export default function Upload() {
  const [files, setFiles] = useState([])
  const [meta, setMeta] = useState({ language: 'eng+hin', state: '', district: '', doc_type: 'unknown' })
  const [drag, setDrag] = useState(false)
  const [progress, setProgress] = useState(0)
  const [msg, setMsg] = useState(null)
  const [dupInfo, setDupInfo] = useState(null)
  const [health, setHealth] = useState(null)
  const nav = useNavigate()

  useEffect(() => { getHealth().then((r) => setHealth(r.data)).catch(() => setHealth(null)) }, [])

  // Warn before the upload, not after: a document in a script whose pack is absent
  // comes back empty, which is easy to mistake for the model failing to read it.
  const usable = health?.tesseract?.languages_usable || []
  const needed = meta.language.split('+').filter((l) => l)
  const missing = health ? needed.filter((l) => !usable.includes(l)) : []

  const pick = (list) => setFiles([...files, ...Array.from(list)])

  const submit = async (allowDuplicate = false) => {
    if (!files.length) return
    setMsg(null); setDupInfo(null)
    try {
      const { data } = await uploadDocuments(files, { ...meta, allow_duplicate: allowDuplicate ? 'true' : '' },
                                               (e) => setProgress(Math.round((e.loaded / e.total) * 100)))
      setMsg({ ok: true, text: `${data.length} document(s) queued for AI processing.` })
      setFiles([])
      setTimeout(() => nav('/documents'), 1200)
    } catch (e) {
      const payload = e.response?.data?.detail
      // Structured duplicate response: show a link to the existing document.
      if (e.response?.status === 409 && payload && typeof payload === 'object'
          && payload.code === 'duplicate_upload') {
        setDupInfo(payload)
      } else {
        setMsg({ ok: false, text: (typeof payload === 'string' ? payload : payload?.detail) || 'Upload failed' })
      }
    } finally { setProgress(0) }
  }

  return (
    <>
      <h1>Upload land records</h1>
      <div className="grid two">
        <div className="card">
          <div className={`dropzone ${drag ? 'active' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files) }}>
            <p><b>Drag &amp; drop</b> scanned PDFs / images here</p>
            <p className="muted">PNG, JPEG, TIFF, WEBP, PDF · up to 50 MB each · multi-page PDFs supported</p>
            <input type="file" multiple accept="image/*,application/pdf" onChange={(e) => pick(e.target.files)} />
          </div>
          {files.length > 0 && (
            <table style={{ marginTop: 12 }}><tbody>
              {files.map((f, i) => <tr key={i}><td>{f.name}</td><td className="muted">{(f.size / 1024).toFixed(0)} KB</td>
                <td><button className="btn sm ghost" onClick={() => setFiles(files.filter((_, j) => j !== i))}>remove</button></td></tr>)}
            </tbody></table>
          )}
          {progress > 0 && <div className="progress" style={{ marginTop: 10 }}><i style={{ width: `${progress}%` }} /></div>}
          {msg && <div className={`alert ${msg.ok ? 'ok' : 'err'}`} style={{ marginTop: 10 }}>{msg.text}</div>}
          {dupInfo && (
            <div className="alert warn" style={{ marginTop: 10 }}>
              <b>Duplicate detected.</b> {dupInfo.detail}
              <div style={{ marginTop: 6, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <Link className="btn sm ghost" to={`/documents/${dupInfo.existing_document_id}`}>Open existing document</Link>
                <button className="btn sm" onClick={() => submit(true)}>Upload anyway (allow duplicate)</button>
                <button className="btn sm ghost" onClick={() => setDupInfo(null)}>Cancel</button>
              </div>
            </div>
          )}
          <button className="btn" style={{ marginTop: 12 }} disabled={!files.length} onClick={() => submit(false)}>Upload &amp; process {files.length ? `(${files.length})` : ''}</button>
        </div>
        <div className="card">
          <h2>Document metadata</h2>
          {missing.length > 0 && (
            <div className="alert warn">
              <b>Language pack not installed: {missing.join(', ')}.</b> Documents in that script
              will extract nothing and tabular records will not be detected. Installed and working:{' '}
              {usable.join(', ') || 'none'}. Re-run the Tesseract installer and tick the language
              under “Additional language data”, then restart the API.
            </div>
          )}
          <div className="field"><label>Language(s) of the document</label>
            <select value={meta.language} onChange={(e) => setMeta({ ...meta, language: e.target.value })}>{LANGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
          <div className="field"><label>Document type (auto-detected if unknown)</label>
            <select value={meta.doc_type} onChange={(e) => setMeta({ ...meta, doc_type: e.target.value })}>
              <option value="unknown">Auto-detect</option><option value="ror">Record of Rights / Khatauni / 7-12</option>
              <option value="khasra">Khasra register</option><option value="mutation">Mutation order</option>
              <option value="sale_deed">Sale / registration deed</option><option value="map">Cadastral map</option></select></div>
          <div className="field"><label>State</label><input type="text" value={meta.state} onChange={(e) => setMeta({ ...meta, state: e.target.value })} placeholder="e.g. Uttar Pradesh" /></div>
          <div className="field"><label>District</label><input type="text" value={meta.district} onChange={(e) => setMeta({ ...meta, district: e.target.value })} placeholder="e.g. Lucknow" /></div>
          <p className="muted" style={{ fontSize: 12 }}>
            Pipeline: image enhancement (denoise · deskew · CLAHE · adaptive binarisation) → multi-pass multilingual OCR →
            NLP field extraction → normalisation → business-rule &amp; cross-database validation → confidence scoring →
            auto-verify or route to human review.
          </p>
        </div>
      </div>
    </>
  )
}
