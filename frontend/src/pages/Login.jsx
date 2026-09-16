import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '../api/client'

export default function Login() {
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [err, setErr] = useState('')
  const nav = useNavigate()

  const submit = async (e) => {
    e.preventDefault()
    setErr('')
    try {
      await login(u, p)
      nav('/')
    } catch (ex) {
      setErr(ex.response?.status === 429
        ? 'Too many sign-in attempts. Please wait a minute and try again.'
        : 'Invalid credentials')
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1 style={{ color: 'var(--brand)' }}>Bhulekh-AI</h1>
        <p className="muted">Intelligent Land Record Digitization &amp; Validation System<br />Ministry of Rural Development · DoLR</p>
        {err && <div className="alert err">{err}</div>}
        <div className="field"><label>Username</label><input type="text" autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} /></div>
        <div className="field"><label>Password</label><input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} /></div>
        <button className="btn" style={{ width: '100%' }}>Sign in</button>
        {import.meta.env.DEV && (
          <p className="muted" style={{ fontSize: 12, marginTop: 14 }}>
            Demo accounts (dev seed only): admin/Admin@12345 · verifier/Verify@12345 ·
            operator/Operate@12345 · viewer/Viewer@12345
          </p>
        )}
      </form>
    </div>
  )
}
