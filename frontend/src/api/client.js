import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1' })

const TOKEN = 'token'
const REFRESH = 'refresh_token'
const USER = 'user'

api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem(TOKEN)
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

const storeSession = (data) => {
  localStorage.setItem(TOKEN, data.access_token)
  localStorage.setItem(REFRESH, data.refresh_token)
  const { access_token, refresh_token, ...user } = data // keep tokens out of the user blob
  localStorage.setItem(USER, JSON.stringify(user))
}

const clearSession = () => {
  localStorage.removeItem(TOKEN)
  localStorage.removeItem(REFRESH)
  localStorage.removeItem(USER)
}

const toLogin = () => {
  clearSession()
  if (!location.pathname.startsWith('/login')) location.href = '/login'
}

// One shared in-flight refresh, so a burst of 401s triggers a single rotation.
// Refresh tokens are single-use server-side; parallel refreshes would revoke each other.
let refreshing = null
const refreshSession = () => {
  if (!refreshing) {
    const refresh_token = localStorage.getItem(REFRESH)
    refreshing = (refresh_token
      ? axios.post('/api/v1/auth/refresh', { refresh_token }).then(({ data }) => storeSession(data))
      : Promise.reject(new Error('no refresh token'))
    ).finally(() => { refreshing = null })
  }
  return refreshing
}

const AUTH_PATHS = ['/auth/login', '/auth/refresh', '/auth/logout']

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const cfg = err.config
    const isAuthCall = AUTH_PATHS.some((p) => cfg?.url?.startsWith(p))
    if (err.response?.status === 401 && cfg && !cfg._retried && !isAuthCall) {
      cfg._retried = true
      try {
        await refreshSession()
      } catch {
        toLogin()
        return Promise.reject(err)
      }
      return api(cfg) // request interceptor attaches the new access token
    }
    if (err.response?.status === 401 && !isAuthCall) toLogin()
    return Promise.reject(err)
  },
)

export const login = async (username, password) => {
  const form = new URLSearchParams({ username, password })
  const { data } = await api.post('/auth/login', form)
  storeSession(data)
  return data
}

export const currentUser = () => {
  try { return JSON.parse(localStorage.getItem(USER)) } catch { return null }
}

export const logout = async () => {
  const refresh_token = localStorage.getItem(REFRESH)
  if (refresh_token) {
    // Best-effort server-side revocation; sign out locally regardless.
    try { await api.post('/auth/logout', { refresh_token }) } catch { /* ignore */ }
  }
  clearSession()
  location.href = '/login'
}

export const uploadDocuments = (files, meta, onProgress) => {
  const fd = new FormData()
  files.forEach((f) => fd.append('files', f))
  Object.entries(meta).forEach(([k, v]) => v && fd.append(k, v))
  return api.post('/documents/upload', fd, { onUploadProgress: onProgress })
}
export const listDocuments = (params) => api.get('/documents', { params })
export const getDocument = (id) => api.get(`/documents/${id}`)
export const verifyDocument = (id, body) => api.post(`/documents/${id}/verify`, body)
export const reprocessDocument = (id) => api.post(`/documents/${id}/reprocess`)
export const fileUrl = (id, processed = false, page = 1) =>
  `/api/v1/documents/${id}/file?processed=${processed}&page=${page}`
export const getStats = (params) => api.get('/dashboard/stats', { params })
export const getAudit = (params) => api.get('/audit', { params })
export const listUsers = () => api.get('/auth/users')
export const createUser = (body) => api.post('/auth/users', body)
export const toggleUser = (id) => api.patch(`/auth/users/${id}/toggle`)
export const lookupRecords = (params) => api.get('/integration/lookup', { params })
export const listRecords = (params) => api.get('/integration/records', { params })
export const listRecordsFlat = (params) => api.get('/integration/records_flat', { params })
export const getSuggestions = (params) => api.get('/integration/suggestions', { params })
export const getVillageSummary = (name) => api.get(`/integration/villages/${encodeURIComponent(name)}/summary`)
export const globalSearch = (q, limit = 30) => api.get('/documents/search', { params: { q, limit } })
export const getHealth = () => axios.get('/health')

// Authenticated file fetch (the <img> tag cannot send a bearer token)
export const fetchBlobUrl = async (url) => {
  const { data } = await api.get(url.replace('/api/v1', ''), { responseType: 'blob' })
  return URL.createObjectURL(data)
}
