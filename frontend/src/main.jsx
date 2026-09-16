import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './styles.css'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Upload from './pages/Upload'
import Documents from './pages/Documents'
import Verify from './pages/Verify'
import Audit from './pages/Audit'
import Users from './pages/Users'
import Lookup from './pages/Lookup'
import { currentUser } from './api/client'

const Private = ({ children }) => (currentUser() ? children : <Navigate to="/login" replace />)

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Private><Layout /></Private>}>
          <Route index element={<Dashboard />} />
          <Route path="upload" element={<Upload />} />
          <Route path="documents" element={<Documents />} />
          <Route path="documents/:id" element={<Verify />} />
          <Route path="lookup" element={<Lookup />} />
          <Route path="audit" element={<Audit />} />
          <Route path="users" element={<Users />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
