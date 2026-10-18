import { Link, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Notifications from "./components/Notifications";
import Dashboard from "./pages/Dashboard";
import ItemDetail from "./pages/ItemDetail";
import Login from "./pages/Login";
import NewItem from "./pages/NewItem";

export default function App() {
  const { me, loading, logout } = useAuth();

  if (loading) return <div className="center muted">Loading…</div>;
  if (!me) {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">Newtonite Ops</Link>
        <Link to="/items/new" className="btn primary small">+ New item</Link>
        <span className="spacer" />
        <Notifications />
        <span className="who" title={me.teams.map((t) => `${t.name}: ${t.role}`).join("\n")}>
          {me.name}
          <span className="muted"> · {me.is_admin ? "admin" : me.teams.map((t) => `${t.role} @ ${t.name}`).join(", ")}</span>
        </span>
        <button className="btn small" onClick={logout}>Log out</button>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/items/new" element={<NewItem />} />
          <Route path="/items/:id" element={<ItemDetail />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
