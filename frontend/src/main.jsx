import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './styles.css'
import Layout from './components/Layout'
import { currentUser } from './api/client'

const Login = lazy(() => import('./pages/Login'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Upload = lazy(() => import('./pages/Upload'))
const Documents = lazy(() => import('./pages/Documents'))
const Verify = lazy(() => import('./pages/Verify'))
const Audit = lazy(() => import('./pages/Audit'))
const Users = lazy(() => import('./pages/Users'))
const Lookup = lazy(() => import('./pages/Lookup'))
const Database = lazy(() => import('./pages/Database'))
const Village = lazy(() => import('./pages/Village'))
const Search = lazy(() => import('./pages/Search'))

const Private = ({ children }) => (currentUser() ? children : <Navigate to="/login" replace />)

const Fallback = () => (
  <div style={{ padding: '2rem', color: '#64748b' }}>Loading…</div>
)

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Suspense fallback={<Fallback />}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Private><Layout /></Private>}>
            <Route index element={<Dashboard />} />
            <Route path="upload" element={<Upload />} />
            <Route path="documents" element={<Documents />} />
            <Route path="documents/:id" element={<Verify />} />
            <Route path="lookup" element={<Lookup />} />
            <Route path="database" element={<Database />} />
            <Route path="village/:name" element={<Village />} />
            <Route path="search" element={<Search />} />
            <Route path="audit" element={<Audit />} />
            <Route path="users" element={<Users />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  </React.StrictMode>,
)
