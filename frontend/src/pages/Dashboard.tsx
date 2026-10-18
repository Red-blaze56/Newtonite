import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  api,
  ITEM_TYPES,
  label,
  PRIORITIES,
  STATUSES,
  timeAgo,
  type ItemType,
  type ListParams,
  type Priority,
  type Status,
  type View,
} from "../api";
import { useAuth } from "../auth";
import { PriorityBadge, StatusBadge } from "../components/Badges";

const VIEWS: { key: View | ""; title: string; hint: string }[] = [
  { key: "assigned_to_me", title: "Assigned to me", hint: "Your open work" },
  { key: "unassigned_urgent", title: "Unassigned urgent", hint: "Critical/high with no owner" },
  { key: "awaiting_my_approval", title: "Awaiting my approval", hint: "Needs a lead's decision" },
  { key: "overdue", title: "Overdue", hint: "Past due, not resolved" },
  { key: "", title: "All items", hint: "Everything you can see" },
];

// Filters live in the URL (?view=...&q=...), so the back button and shared links work.
export default function Dashboard() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const rawView = params.get("view") ?? "assigned_to_me";
  const view = (rawView === "all" ? "" : rawView) as View | ""; // "all" = no view filter
  const filters: ListParams = {
    view: view || undefined,
    team_id: params.get("team") ? Number(params.get("team")) : undefined,
    status: params.get("status") ? [params.get("status") as Status] : undefined,
    priority: params.get("priority") ? [params.get("priority") as Priority] : undefined,
    type: params.get("type") ? [params.get("type") as ItemType] : undefined,
    q: params.get("q") ?? undefined,
    sort: (params.get("sort") as "updated" | "priority") ?? undefined,
    limit: 25,
  };

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  // Debounce the search box: query the server 300ms after typing stops, not on every key.
  const [search, setSearch] = useState(filters.q ?? "");
  useEffect(() => {
    const t = setTimeout(() => {
      if (search !== (params.get("q") ?? "")) set("q", search.trim());
    }, 300);
    return () => clearTimeout(t);
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const counts = useQuery({ queryKey: ["counts"], queryFn: api.counts, refetchInterval: 15_000 });
  const list = useInfiniteQuery({
    queryKey: ["items", filters],
    queryFn: ({ pageParam }) => api.listItems({ ...filters, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: 15_000,
  });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div>
      <div className="tiles">
        {VIEWS.map((v) => (
          <button key={v.key} className={`tile ${view === v.key ? "active" : ""}`} onClick={() => set("view", v.key || "all")}>
            <div className="tile-count">{v.key ? (counts.data?.[v.key] ?? "–") : "∞"}</div>
            <div className="tile-title">{v.title}</div>
            <div className="muted small">{v.hint}</div>
          </button>
        ))}
      </div>

      <div className="filters">
        <input placeholder="Search titles and descriptions…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={params.get("team") ?? ""} onChange={(e) => set("team", e.target.value)}>
          <option value="">All my teams</option>
          {me?.teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select value={params.get("status") ?? ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">Any status</option>
          {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
        <select value={params.get("priority") ?? ""} onChange={(e) => set("priority", e.target.value)}>
          <option value="">Any priority</option>
          {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={params.get("type") ?? ""} onChange={(e) => set("type", e.target.value)}>
          <option value="">Any type</option>
          {ITEM_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
        </select>
        <select value={params.get("sort") ?? ""} onChange={(e) => set("sort", e.target.value)}>
          <option value="">Default order</option>
          <option value="updated">Recently updated</option>
          <option value="priority">Priority</option>
        </select>
      </div>

      {list.isError && <div className="alert error">{(list.error as Error).message}</div>}
      <table className="items">
        <thead>
          <tr>
            <th>#</th><th>Title</th><th>Team</th><th>Type</th><th>Priority</th><th>Status</th><th>Assignee</th><th>Due</th><th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id}>
              <td className="muted">{i.id}</td>
              <td><Link to={`/items/${i.id}`}>{i.title}</Link></td>
              <td>{i.team_name}</td>
              <td>{label(i.type)}</td>
              <td><PriorityBadge p={i.priority} /></td>
              <td><StatusBadge s={i.status} /></td>
              <td>{i.assignee?.name ?? <span className="muted">unassigned</span>}</td>
              <td className={i.overdue ? "overdue" : ""}>{i.due_at ? new Date(i.due_at).toLocaleDateString() : ""}</td>
              <td className="muted">{timeAgo(i.updated_at)}</td>
            </tr>
          ))}
          {!list.isLoading && items.length === 0 && (
            <tr><td colSpan={9} className="muted center">Nothing here. 🎉</td></tr>
          )}
        </tbody>
      </table>
      <div className="center">
        {list.isLoading && <span className="muted">Loading…</span>}
        {list.hasNextPage && (
          <button className="btn" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
            {list.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        )}
      </div>
    </div>
  );
}
