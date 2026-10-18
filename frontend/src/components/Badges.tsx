import { label, type Priority, type Status } from "../api";

export function PriorityBadge({ p }: { p: Priority }) {
  return <span className={`badge-pill prio-${p}`}>{p}</span>;
}

export function StatusBadge({ s }: { s: Status }) {
  return <span className={`badge-pill status-${s}`}>{label(s)}</span>;
}
