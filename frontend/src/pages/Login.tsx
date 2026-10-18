import { useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { useAuth } from "../auth";

const DEMO = [
  ["priya", "Lead of Payments Ops, member of Compliance"],
  ["marco", "Member of Payments Ops"],
  ["sam", "Member of Payments Ops and Customer Support"],
  ["dev", "Member of Platform Eng, viewer of Payments Ops"],
  ["victor", "Viewer (auditor) of Payments Ops and Compliance"],
  ["lena", "Lead of Platform Engineering"],
  ["admin", "Global admin"],
];

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("priya@newtonite.example");
  const [password, setPassword] = useState("demo1234");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>Newtonite Ops</h1>
        <label>
          Email
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <div className="alert error">{error}</div>}
        <button className="btn primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <details className="demo">
          <summary>Demo accounts (password: demo1234)</summary>
          <ul>
            {DEMO.map(([who, desc]) => (
              <li key={who}>
                <a href="#" onClick={(e) => { e.preventDefault(); setEmail(`${who}@newtonite.example`); }}>
                  {who}@newtonite.example
                </a>{" "}
                <span className="muted">{desc}</span>
              </li>
            ))}
          </ul>
        </details>
      </form>
    </div>
  );
}
