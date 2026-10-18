// Who is logged in. The token is kept in React state (so logging in/out re-renders the app) and
// mirrored to localStorage (so a page reload keeps you logged in). The user, with their team
// roles, is re-fetched from /me on load, so role changes made on the server show up.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, tokenStore, type Me, type Role } from "./api";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  roleIn: (teamId: number) => Role | null;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [token, setToken] = useState<string | null>(tokenStore.get());
  // The token is part of the key: a different login is a different "me", never a cached one.
  const meQuery = useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, retry: false, staleTime: 60_000 });

  const logout = useCallback(() => {
    tokenStore.clear();
    setToken(null);
    qc.clear(); // drop every cached item: the next user must never see the previous user's data
  }, [qc]);

  useEffect(() => setUnauthorizedHandler(logout), [logout]);

  const login = async (email: string, password: string) => {
    const { access_token, user } = await api.login(email, password);
    tokenStore.set(access_token);
    qc.clear();
    qc.setQueryData(["me", access_token], user); // login already returned the user: no extra request
    setToken(access_token);
  };

  const me = token ? (meQuery.data ?? null) : null;
  const roleIn = (teamId: number): Role | null =>
    me?.is_admin ? "admin" : (me?.teams.find((t) => t.id === teamId)?.role ?? null);

  return (
    <AuthContext.Provider value={{ me, loading: !!token && meQuery.isLoading, login, logout, roleIn }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
