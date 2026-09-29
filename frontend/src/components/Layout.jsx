import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { currentUser, logout } from '../api/client'

const NAV = [
  { to: '/', label: 'Dashboard', roles: ['admin', 'verifier', 'operator', 'viewer'] },
  { to: '/upload', label: 'Upload documents', roles: ['admin', 'verifier', 'operator'] },
  { to: '/documents', label: 'Documents & review', roles: ['admin', 'verifier', 'operator', 'viewer'] },
  { to: '/lookup', label: 'Parcel lookup', roles: ['admin', 'verifier', 'operator', 'viewer'] },
  { to: '/database', label: 'Records database', roles: ['admin', 'verifier', 'operator', 'viewer'] },
  { to: '/search', label: 'Search everything', roles: ['admin', 'verifier', 'operator', 'viewer'] },
  { to: '/audit', label: 'Audit trail', roles: ['admin', 'verifier'] },
  { to: '/users', label: 'Users & roles', roles: ['admin'] },
]

function QuickSearch() {
  const [q, setQ] = useState('')
  const nav = useNavigate()
  const go = (e) => {
    e.preventDefault()
    if (q.trim().length < 2) return
    nav(`/search?q=${encodeURIComponent(q.trim())}`)
    setQ('')
  }
  return (
    <form onSubmit={go} className="sidebar-search">
      <input type="text" value={q} onChange={(e) => setQ(e.target.value)}
             placeholder="Search owner, khasra, village…" />
    </form>
  )
}

export default function Layout() {
  const user = currentUser()
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">Bhulekh-AI<small>Intelligent Land Record Digitization &amp; Validation</small></div>
        <QuickSearch />
        <nav className="nav">
          {NAV.filter((n) => n.roles.includes(user.role)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}>{n.label}</NavLink>
          ))}
        </nav>
        <div className="user">
          <div><b>{user.full_name || user.username}</b></div>
          <div>Role: {user.role}</div>
          <button className="btn sm ghost" style={{ color: '#fff', borderColor: '#fff' }} onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main><Outlet /></main>
    </div>
  )
}

export const Badge = ({ v }) => <span className={`badge ${v}`}>{String(v).replace('_', ' ')}</span>

export const Conf = ({ v }) => {
  const cls = v >= 85 ? 'hi' : v >= 60 ? 'mid' : 'lo'
  return (
    <span className={`conf ${cls}`} title={`${v}% confidence`}>
      <span className="bar"><i style={{ width: `${Math.min(v, 100)}%` }} /></span>
      <span>{Math.round(v)}%</span>
    </span>
  )
}
