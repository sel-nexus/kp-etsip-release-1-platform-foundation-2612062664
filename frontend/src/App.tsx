import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth/AuthContext';
import LoginPage from './auth/LoginPage';
import ProfilePage from './auth/ProfilePage';
import PortfolioList from './portfolios/PortfolioList';
import PortfolioDetail from './portfolios/PortfolioDetail';
import PortfolioForm from './portfolios/PortfolioForm';
import ApplicationList from './applications/ApplicationList';
import ApplicationDetail from './applications/ApplicationDetail';
import ApplicationForm from './applications/ApplicationForm';

/** Restrict routes to a live session and the initial password change. */
function Protected() {
  const { user, loading, signOut } = useAuth();
  const location = useLocation();
  if (loading) return <main className="content" role="status">Checking session…</main>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.mustChangePassword && location.pathname !== '/profile') return <Navigate to="/profile" replace />;
  return (
    <>
      <header className="header">
        <Link className="brand" to={user.mustChangePassword ? '/profile' : `/${user.defaultLandingPage}`}>KP ETSIP <span>Platform Foundation</span></Link>
        <nav aria-label="Account">
          {!user.mustChangePassword && <Link to="/portfolios">Portfolios</Link>}
          {!user.mustChangePassword && <Link to="/applications">Applications</Link>}
          <Link to="/profile">My profile</Link>
          <button className="text-button" onClick={() => { void signOut().catch(() => { console.error('Sign-out could not be confirmed by the server'); }); }}>Sign out</button>
        </nav>
      </header>
      <Routes>
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/portfolios" element={<PortfolioList />} />
        <Route path="/portfolios/new" element={<PortfolioForm />} />
        <Route path="/portfolios/:id" element={<PortfolioDetail />} />
        <Route path="/portfolios/:id/edit" element={<PortfolioForm />} />
        <Route path="/dashboard" element={<Destination title="Executive Dashboard" />} />
        <Route path="/applications" element={<ApplicationList />} />
        <Route path="/applications/new" element={<ApplicationForm />} />
        <Route path="/applications/:id" element={<ApplicationDetail />} />
        <Route path="/applications/:id/edit" element={<ApplicationForm />} />
        <Route path="*" element={<Navigate to={`/${user.defaultLandingPage}`} replace />} />
      </Routes>
    </>
  );
}

/** Identify the saved destination without presenting unfinished functionality. */
function Destination({ title }: { title: string }) {
  return (
    <main className="content">
      <p className="eyebrow">YOUR WORKSPACE</p>
      <h1>{title}</h1>
      <p>This section is not available in the authentication release.</p>
      <Link to="/profile">Manage your profile</Link>
    </main>
  );
}

/** Mount the application's single router and authentication provider. */
export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/*" element={<Protected />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
