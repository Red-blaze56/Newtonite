// Typed client for the backend API. Every call goes through `request`, which:
//  * adds the bearer token,
//  * turns the server's {"error": {code, message, details}} into an ApiError,
//  * sends an Idempotency-Key on every write and retries once with the SAME key if the network
//    drops, so a retry can never perform the action twice.

export type Role = "viewer" | "member" | "lead" | "admin";
export type Status = "open" | "in_progress" | "awaiting_approval" | "resolved" | "closed";
export type Priority = "critical" | "high" | "medium" | "low";
export type ItemType = "incident" | "customer_issue" | "payment" | "compliance" | "task";
export type View = "assigned_to_me" | "unassigned_urgent" | "awaiting_my_approval" | "overdue";

export const STATUSES: Status[] = ["open", "in_progress", "awaiting_approval", "resolved", "closed"];
export const PRIORITIES: Priority[] = ["critical", "high", "medium", "low"];
export const ITEM_TYPES: ItemType[] = ["incident", "customer_issue", "payment", "compliance", "task"];

export interface TeamRole { id: number; name: string; role: Role }
export interface Me { id: number; email: string; name: string; is_admin: boolean; teams: TeamRole[] }
export interface UserRef { id: number; name: string }
export interface TeamMember { id: number; name: string; email: string; role: Role }

export interface ItemSummary {
  id: number;
  team_id: number;
  team_name: string;
  type: ItemType;
  title: string;
  priority: Priority;
  status: Status;
  assignee: UserRef | null;
  due_at: string | null;
  overdue: boolean;
  requires_approval: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface AllowedActions {
  edit: boolean;
  claim: boolean;
  assign: boolean;
  release: boolean;
  comment: boolean;
  transitions: Status[];
}

export interface ItemDetail extends ItemSummary {
  description: string;
  created_by: UserRef;
  allowed: AllowedActions;
}

export interface ItemPage { items: ItemSummary[]; next_cursor: string | null }

export interface ActivityEvent {
  id: number;
  kind: "created" | "edited" | "assignee_changed" | "status_changed" | "comment";
  actor: UserRef;
  payload: Record<string, any>;
  created_at: string;
}

export interface Counts { assigned_to_me: number; unassigned_urgent: number; awaiting_my_approval: number; overdue: number }

export interface Notification { id: number; item_id: number | null; message: string; read: boolean; created_at: string }
export interface NotificationPage { notifications: Notification[]; unread_count: number; next_cursor: string | null }

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: any = null,
  ) {
    super(message);
  }
}

const TOKEN_KEY = "newtonite.token";
export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isWrite = method !== "GET";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (isWrite) headers["Idempotency-Key"] = crypto.randomUUID(); // one key per user action

  let res: Response | undefined;
  for (let attempt = 1; ; attempt++) {
    try {
      res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
      break;
    } catch (networkError) {
      // The request may or may not have reached the server. Retrying with the same
      // Idempotency-Key is safe: the server replays the first result instead of acting twice.
      if (attempt >= 2) throw new ApiError(0, "network_error", "Could not reach the server. Check your connection.");
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error ?? { code: `http_${res.status}`, message: res.statusText };
    if (res.status === 401 && path !== "/auth/login") onUnauthorized();
    throw new ApiError(res.status, err.code, err.message, err.details);
  }
  return data as T;
}

function qs(params: Record<string, string | number | string[] | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export interface ListParams {
  view?: View;
  team_id?: number;
  status?: Status[];
  priority?: Priority[];
  type?: ItemType[];
  assignee?: string;
  q?: string;
  sort?: "updated" | "priority";
  cursor?: string;
  limit?: number;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; user: Me }>("POST", "/auth/login", { email, password }),
  me: () => request<Me>("GET", "/me"),
  teamMembers: (teamId: number) => request<TeamMember[]>("GET", `/teams/${teamId}/members`),

  listItems: (p: ListParams) => request<ItemPage>("GET", `/items${qs({ ...p })}`),
  counts: () => request<Counts>("GET", "/dashboard/counts"),
  getItem: (id: number) => request<ItemDetail>("GET", `/items/${id}`),
  activity: (id: number, cursor?: string) =>
    request<{ events: ActivityEvent[]; next_cursor: string | null }>("GET", `/items/${id}/activity${qs({ cursor })}`),

  createItem: (body: { team_id: number; type: ItemType; title: string; description: string; priority: Priority; due_at: string | null }) =>
    request<ItemDetail>("POST", "/items", body),
  updateItem: (id: number, body: Record<string, unknown> & { expected_version: number }) =>
    request<ItemDetail>("PATCH", `/items/${id}`, body),
  claim: (id: number) => request<ItemDetail>("POST", `/items/${id}/claim`),
  assign: (id: number, assignee_id: number | null, expected_version: number) =>
    request<ItemDetail>("POST", `/items/${id}/assign`, { assignee_id, expected_version }),
  transition: (id: number, to: Status, expected_version: number, note?: string) =>
    request<ItemDetail>("POST", `/items/${id}/transition`, { to, expected_version, note: note || null }),
  comment: (id: number, body: string) => request<ActivityEvent>("POST", `/items/${id}/comments`, { body }),

  notifications: () => request<NotificationPage>("GET", "/notifications?limit=15"),
  markRead: (id: number) => request<void>("POST", `/notifications/${id}/read`),
  markAllRead: () => request<void>("POST", "/notifications/read-all"),
};

// --- display helpers ---------------------------------------------------------------------------

export const label = (s: string) => s.replace(/_/g, " ");

export function timeAgo(iso: string): string {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
