import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Shell } from './components/Shell'
import { Loading } from './components/ui'
import { useAuth } from './lib/auth'
import type { Role } from './lib/api'

import { Login } from './routes/Login'
import { PatientHome } from './routes/patient/PatientHome'
import { Book } from './routes/patient/Book'
import { MyTurn } from './routes/patient/MyTurn'
import { DoctorConsole } from './routes/doctor/DoctorConsole'
import { AdminDashboard } from './routes/admin/AdminDashboard'
import { Kiosk } from './routes/kiosk/Kiosk'
import { Board } from './routes/board/Board'

function RequireAuth({ children, roles }: { children: ReactNode; roles?: Role[] }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <Loading />
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />
  if (roles && !roles.includes(user.role)) return <Navigate to="/" replace />
  return <>{children}</>
}

/** Send each role to the surface it actually works in. */
function RoleHome() {
  const { user } = useAuth()
  if (user?.role === 'doctor') return <Navigate to="/doctor" replace />
  if (user?.role === 'admin' || user?.role === 'health_dept') return <Navigate to="/admin" replace />
  return <PatientHome />
}

export function App() {
  return (
    <Routes>
      {/* Unauthenticated */}
      <Route path="/login" element={<Login />} />

      {/* Provisioned devices — no user session, no chrome */}
      <Route path="/kiosk" element={<Kiosk />} />
      <Route path="/board/:doctorId" element={<Board />} />

      {/* Signed-in surfaces */}
      <Route
        path="/"
        element={
          <RequireAuth>
            <Shell>
              <RoleHome />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/book"
        element={
          <RequireAuth>
            <Shell>
              <Book />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/my-turn"
        element={
          <RequireAuth>
            <Shell>
              <MyTurn />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/doctor"
        element={
          <RequireAuth roles={['doctor', 'admin', 'staff']}>
            <Shell wide>
              <DoctorConsole />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/admin"
        element={
          <RequireAuth roles={['admin', 'health_dept', 'staff']}>
            <Shell wide>
              <AdminDashboard />
            </Shell>
          </RequireAuth>
        }
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
