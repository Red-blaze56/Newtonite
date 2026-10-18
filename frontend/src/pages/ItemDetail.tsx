import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  label,
  PRIORITIES,
  timeAgo,
  type ActivityEvent,
  type ItemDetail as Item,
  type Priority,
  type Status,
} from "../api";
import { useAuth } from "../auth";
import { PriorityBadge, StatusBadge } from "../components/Badges";

type Notice = { kind: "error" | "info"; text: string } | null;

function transitionLabel(from: Status, to: Status): string {
  if (to === "in_progress") return from === "open" ? "Start work" : from === "awaiting_approval" ? "Reject" : "Reopen";
  if (to === "awaiting_approval") return "Request approval";
  if (to === "resolved") return from === "awaiting_approval" ? "Approve" : "Resolve";
  if (to === "closed") return "Close";
  return "Move back to open";
}

export default function ItemDetail() {
  const id = Number(useParams().id);
  const { me } = useAuth();
  const qc = useQueryClient();
  const [notice, setNotice] = useState<Notice>(null);

  // Polling keeps the page close to the truth while someone else works on the same item.
  const itemQ = useQuery({ queryKey: ["item", id], queryFn: () => api.getItem(id), refetchInterval: 10_000 });
  const activityQ = useQuery({ queryKey: ["activity", id], queryFn: () => api.activity(id), refetchInterval: 10_000 });
  const item = itemQ.data;
  const membersQ = useQuery({
    queryKey: ["members", item?.team_id],
    queryFn: () => api.teamMembers(item!.team_id),
    enabled: !!item,
    staleTime: 5 * 60_000,
  });
  const names = new Map((membersQ.data ?? []).map((m) => [m.id, m.name]));
  const nameOf = (uid: number | null | undefined) => (uid == null ? "nobody" : (names.get(uid) ?? `user #${uid}`));

  // One helper for every action: optimistic update, then reconcile with the server's decision.
  function useAction<V>(fn: (v: V) => Promise<Item>, optimistic?: (item: Item, v: V) => Item) {
    return useMutation({
      mutationFn: fn,
      onMutate: async (v: V) => {
        await qc.cancelQueries({ queryKey: ["item", id] }); // don't let a poll overwrite our optimistic state
        const previous = qc.getQueryData<Item>(["item", id]);
        if (previous && optimistic) qc.setQueryData(["item", id], optimistic(previous, v));
        return { previous };
      },
      onError: (err, _v, ctx) => {
        // The server said no. Show what is actually true: its copy of the item if it sent one
        // (version conflict, already claimed), otherwise roll back to what we had.
        if (err instanceof ApiError && err.details?.current) qc.setQueryData(["item", id], err.details.current);
        else if (ctx?.previous) qc.setQueryData(["item", id], ctx.previous);
        setNotice({ kind: "error", text: err instanceof Error ? err.message : "Something went wrong" });
      },
      onSuccess: (fresh) => {
        qc.setQueryData(["item", id], fresh);
        setNotice(null);
      },
      onSettled: () => {
        qc.invalidateQueries({ queryKey: ["activity", id] });
        qc.invalidateQueries({ queryKey: ["items"] });
        qc.invalidateQueries({ queryKey: ["counts"] });
      },
    });
  }

  const claim = useAction(
    () => api.claim(id),
    (it) => ({ ...it, assignee: me ? { id: me.id, name: me.name } : it.assignee, allowed: { ...it.allowed, claim: false } }),
  );
  const transition = useAction(
    (v: { to: Status; version: number; note?: string }) => api.transition(id, v.to, v.version, v.note),
    (it, v) => ({ ...it, status: v.to, allowed: { ...it.allowed, transitions: [] } }),
  );
  const assign = useAction((v: { assignee: number | null; version: number }) => api.assign(id, v.assignee, v.version));

  if (itemQ.isLoading) return <div className="muted">Loading…</div>;
  if (itemQ.error) return <div className="alert error">{(itemQ.error as Error).message} <Link to="/">Back</Link></div>;
  if (!item) return null;

  const busy = claim.isPending || transition.isPending || assign.isPending;
  const doTransition = (to: Status) => {
    let note: string | undefined;
    if (item.status === "awaiting_approval" && to === "in_progress") {
      note = window.prompt("Why are you rejecting this? (required)") ?? undefined;
      if (!note?.trim()) return;
    }
    transition.mutate({ to, version: item.version, note });
  };

  return (
    <div className="detail">
      <div className="detail-main">
        <Link to="/" className="muted small">← Back to dashboard</Link>
        <h1>
          <span className="muted">#{item.id}</span> {item.title}
        </h1>
        <div className="row gap">
          <StatusBadge s={item.status} />
          <PriorityBadge p={item.priority} />
          <span className="badge-pill">{label(item.type)}</span>
          <span className="muted">{item.team_name}</span>
          {item.requires_approval && <span className="badge-pill approval">needs lead approval</span>}
          <span className="muted small">v{item.version}</span>
        </div>

        {notice && <div className={`alert ${notice.kind}`}>{notice.text}</div>}

        <div className="actions">
          {item.allowed.claim && <button className="btn primary" disabled={busy} onClick={() => claim.mutate(undefined)}>Claim</button>}
          {item.allowed.transitions.map((to) => (
            <button key={to} className="btn" disabled={busy} onClick={() => doTransition(to)}>
              {transitionLabel(item.status, to)}
            </button>
          ))}
          {item.allowed.release && (
            <button className="btn" disabled={busy} onClick={() => assign.mutate({ assignee: null, version: item.version })}>
              Release
            </button>
          )}
          {item.allowed.assign && (
            <select
              value={item.assignee?.id ?? ""}
              disabled={busy}
              onChange={(e) => assign.mutate({ assignee: e.target.value ? Number(e.target.value) : null, version: item.version })}
            >
              <option value="">— unassigned —</option>
              {(membersQ.data ?? [])
                .filter((m) => m.role === "member" || m.role === "lead")
                .map((m) => <option key={m.id} value={m.id}>Assign: {m.name}</option>)}
            </select>
          )}
        </div>

        <EditPanel item={item} onSaved={(fresh) => { qc.setQueryData(["item", id], fresh); qc.invalidateQueries({ queryKey: ["activity", id] }); }} />

        <h2>Activity</h2>
        <CommentBox itemId={id} canComment={item.allowed.comment} />
        <ul className="timeline">
          {activityQ.data?.events.map((e) => <TimelineEntry key={e.id} e={e} nameOf={nameOf} />)}
        </ul>
      </div>

      <aside className="detail-side card">
        <dl>
          <dt>Assignee</dt><dd>{item.assignee?.name ?? <span className="muted">unassigned</span>}</dd>
          <dt>Created by</dt><dd>{item.created_by.name}</dd>
          <dt>Due</dt><dd className={item.overdue ? "overdue" : ""}>{item.due_at ? new Date(item.due_at).toLocaleString() : "—"}</dd>
          <dt>Created</dt><dd>{new Date(item.created_at).toLocaleString()}</dd>
          <dt>Updated</dt><dd>{timeAgo(item.updated_at)}</dd>
        </dl>
      </aside>
    </div>
  );
}

// --- editing -----------------------------------------------------------------------------------

type Fields = { title: string; description: string; priority: Priority; due: string };

// <input type="datetime-local"> wants local time "YYYY-MM-DDTHH:mm", without a timezone.
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const fieldsOf = (it: Item): Fields => ({
  title: it.title,
  description: it.description,
  priority: it.priority,
  due: toLocalInput(it.due_at),
});

function EditPanel({ item, onSaved }: { item: Item; onSaved: (i: Item) => void }) {
  // The draft remembers the version it was started from. Background polling may show newer data,
  // but saving always says "I am changing version N": the server rejects it if N is outdated.
  // It also remembers the original values, so only fields the user actually changed are sent.
  // After a conflict, "keep my edits" therefore overwrites only those fields, not everyone's.
  const [draft, setDraft] = useState<{ base: number; original: Fields; edited: Fields } | null>(null);
  const [conflict, setConflict] = useState<Item | null>(null);
  const save = useMutation({
    mutationFn: () => {
      const { base, original, edited } = draft!;
      const changes: Record<string, unknown> = {};
      if (edited.title !== original.title) changes.title = edited.title;
      if (edited.description !== original.description) changes.description = edited.description;
      if (edited.priority !== original.priority) changes.priority = edited.priority;
      if (edited.due !== original.due) changes.due_at = edited.due ? new Date(edited.due).toISOString() : null;
      return api.updateItem(item.id, { expected_version: base, ...changes });
    },
    onSuccess: (fresh) => {
      setDraft(null);
      setConflict(null);
      onSaved(fresh);
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "version_conflict") setConflict(err.details.current);
    },
  });

  if (!item.allowed.edit) return null;
  if (!draft) {
    return (
      <button
        className="btn small"
        onClick={() => setDraft({ base: item.version, original: fieldsOf(item), edited: fieldsOf(item) })}
      >
        Edit details
      </button>
    );
  }

  const edit = (patch: Partial<Fields>) => setDraft({ ...draft, edited: { ...draft.edited, ...patch } });
  // Re-base my changes on their latest version: their values, with only my changed fields on top.
  const keepMine = (latest: Item) => {
    const mine = Object.fromEntries(
      (Object.keys(draft.edited) as (keyof Fields)[])
        .filter((k) => draft.edited[k] !== draft.original[k])
        .map((k) => [k, draft.edited[k]]),
    );
    const theirs = fieldsOf(latest);
    setDraft({ base: latest.version, original: theirs, edited: { ...theirs, ...mine } });
    setConflict(null);
  };

  return (
    <div className="card edit">
      {conflict && (
        <div className="alert error">
          <strong>Someone changed this item while you were editing</strong> (now v{conflict.version}: “{conflict.title}”, priority{" "}
          {conflict.priority}). Your draft is kept.
          <div className="row gap">
            <button className="btn small" onClick={() => { setDraft(null); setConflict(null); onSaved(conflict); }}>Discard my edits</button>
            <button className="btn small" onClick={() => keepMine(conflict)}>
              Apply my changes on top of theirs
            </button>
          </div>
        </div>
      )}
      <label>Title<input value={draft.edited.title} onChange={(e) => edit({ title: e.target.value })} /></label>
      <label>Description<textarea rows={4} value={draft.edited.description} onChange={(e) => edit({ description: e.target.value })} /></label>
      <div className="row gap">
        <label>Priority
          <select value={draft.edited.priority} onChange={(e) => edit({ priority: e.target.value as Priority })}>
            {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        <label>Due<input type="datetime-local" value={draft.edited.due} onChange={(e) => edit({ due: e.target.value })} /></label>
      </div>
      {save.error && !conflict && <div className="alert error">{(save.error as Error).message}</div>}
      <div className="row gap">
        <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>Save (editing v{draft.base})</button>
        <button className="btn" onClick={() => { setDraft(null); setConflict(null); }}>Cancel</button>
      </div>
    </div>
  );
}

// --- activity ----------------------------------------------------------------------------------

function CommentBox({ itemId, canComment }: { itemId: number; canComment: boolean }) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const post = useMutation({
    mutationFn: () => api.comment(itemId, body),
    onSuccess: () => {
      setBody("");
      qc.invalidateQueries({ queryKey: ["activity", itemId] });
    },
  });
  if (!canComment) return null;
  return (
    <div className="comment-box">
      <textarea rows={2} placeholder="Add a comment…" value={body} onChange={(e) => setBody(e.target.value)} />
      <button className="btn" disabled={!body.trim() || post.isPending} onClick={() => post.mutate()}>Comment</button>
      {post.error && <div className="alert error">{(post.error as Error).message}</div>}
    </div>
  );
}

function TimelineEntry({ e, nameOf }: { e: ActivityEvent; nameOf: (id: number | null) => string }) {
  const p = e.payload;
  let text: ReactNode;
  switch (e.kind) {
    case "created":
      text = <>created this {label(p.type)} ({p.priority})</>;
      break;
    case "edited":
      text = (
        <>
          edited{" "}
          {Object.entries(p.changes as Record<string, { from: unknown; to: unknown }>)
            .map(([f, c]) => (f === "description" ? "the description" : `${label(f)}: ${String(c.from ?? "—")} → ${String(c.to ?? "—")}`))
            .join(", ")}
        </>
      );
      break;
    case "assignee_changed":
      text = p.via === "claim" ? <>claimed it</> : p.via === "release" ? <>released it</> : <>assigned it to {nameOf(p.to)}</>;
      break;
    case "status_changed":
      text = (
        <>
          moved it {label(p.from)} → <strong>{label(p.to)}</strong>
          {p.note && <div className="note">“{p.note}”</div>}
        </>
      );
      break;
    case "comment":
      text = <div className="comment-body">{p.body}</div>;
      break;
  }
  return (
    <li className={`event ${e.kind}`}>
      <strong>{e.actor.name}</strong> {text}
      <span className="muted small"> · {timeAgo(e.created_at)}</span>
    </li>
  );
}
