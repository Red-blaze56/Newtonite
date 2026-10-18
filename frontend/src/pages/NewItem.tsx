import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ITEM_TYPES, label, PRIORITIES, type ItemType, type Priority } from "../api";
import { useAuth } from "../auth";

export default function NewItem() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  // Only teams where this user may create (viewers may not). The server enforces this anyway.
  const teams = (me?.teams ?? []).filter((t) => t.role !== "viewer");
  const [form, setForm] = useState({
    team_id: teams[0]?.id ?? 0,
    type: "task" as ItemType,
    title: "",
    description: "",
    priority: "medium" as Priority,
    due: "",
  });

  const create = useMutation({
    mutationFn: () =>
      api.createItem({
        team_id: form.team_id,
        type: form.type,
        title: form.title,
        description: form.description,
        priority: form.priority,
        due_at: form.due ? new Date(form.due).toISOString() : null,
      }),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["counts"] });
      navigate(`/items/${item.id}`);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!create.isPending) create.mutate(); // double-clicks are ignored here AND deduplicated by the server
  };

  if (teams.length === 0) return <div className="alert info">You are a viewer in all your teams, so you cannot create items.</div>;

  return (
    <form className="card narrow" onSubmit={submit}>
      <h1>New work item</h1>
      <label>Team
        <select value={form.team_id} onChange={(e) => setForm({ ...form, team_id: Number(e.target.value) })}>
          {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
      <div className="row gap">
        <label>Type
          <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as ItemType })}>
            {ITEM_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </select>
        </label>
        <label>Priority
          <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value as Priority })}>
            {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        <label>Due<input type="datetime-local" value={form.due} onChange={(e) => setForm({ ...form, due: e.target.value })} /></label>
      </div>
      {(form.type === "payment" || form.type === "compliance") && (
        <div className="alert info">{label(form.type)} items need a team lead's approval before they can be resolved.</div>
      )}
      <label>Title<input required maxLength={200} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
      <label>Description
        <textarea rows={5} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="What is it, why does it exist, what needs to happen?" />
      </label>
      {create.error && <div className="alert error">{(create.error as Error).message}</div>}
      <button className="btn primary" disabled={create.isPending || !form.title.trim()}>
        {create.isPending ? "Creating…" : "Create item"}
      </button>
    </form>
  );
}
