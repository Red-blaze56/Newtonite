import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, timeAgo } from "../api";

// Bell with unread count. Polls every 15s: notifications are created by the background worker,
// so the browser has to ask. (Websockets were deliberately left out of v1.)
export default function Notifications() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data } = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 15_000 });
  const markRead = useMutation({
    mutationFn: api.markRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const markAll = useMutation({
    mutationFn: api.markAllRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const unread = data?.unread_count ?? 0;
  return (
    <div className="notif">
      <button className="btn small" onClick={() => setOpen(!open)} aria-label="Notifications">
        🔔 {unread > 0 && <span className="badge">{unread}</span>}
      </button>
      {open && (
        <div className="notif-panel card">
          <div className="row">
            <strong>Notifications</strong>
            <span className="spacer" />
            {unread > 0 && <button className="link" onClick={() => markAll.mutate()}>Mark all read</button>}
          </div>
          {data?.notifications.length === 0 && <p className="muted">Nothing yet.</p>}
          {data?.notifications.map((n) => (
            <button
              key={n.id}
              className={`notif-item ${n.read ? "" : "unread"}`}
              onClick={() => {
                if (!n.read) markRead.mutate(n.id);
                if (n.item_id) navigate(`/items/${n.item_id}`);
                setOpen(false);
              }}
            >
              <div>{n.message}</div>
              <div className="muted small">{timeAgo(n.created_at)}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
