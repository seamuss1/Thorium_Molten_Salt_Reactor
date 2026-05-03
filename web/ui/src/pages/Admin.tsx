import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, ShieldCheck } from "lucide-react";
import { api } from "../api";

export function Admin() {
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const limits = useQuery({
    queryKey: ["rate-limits"],
    queryFn: api.rateLimits,
    enabled: session.data?.is_admin === true
  });
  const reset = useMutation({
    mutationFn: api.resetRateLimit,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rate-limits"] });
      queryClient.invalidateQueries({ queryKey: ["me"] });
    }
  });

  if (session.data && !session.data.is_admin) {
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <p className="eyebrow">Access control</p>
            <h1>Admin console</h1>
          </div>
        </header>
        <div className="empty-panel tall">Admin access required.</div>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Access control</p>
          <h1>Admin console</h1>
        </div>
      </header>

      <section className="dashboard-grid">
        <div className="panel">
          <div className="section-title">
            <ShieldCheck aria-hidden="true" />
            <h2>Admins</h2>
          </div>
          <div className="tag-row">
            {session.data?.admin_emails.map((email) => (
              <span key={email}>{email}</span>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="section-title">
            <RotateCcw aria-hidden="true" />
            <h2>Daily starts</h2>
          </div>
          <dl className="fact-list">
            <div>
              <dt>Current user</dt>
              <dd>{session.data?.email ?? "Loading"}</dd>
            </div>
            <div>
              <dt>Limit</dt>
              <dd>{session.data?.daily_run_limit ?? "Unlimited"}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="panel admin-panel">
        <div className="section-title">
          <RotateCcw aria-hidden="true" />
          <h2>Rate limits</h2>
        </div>
        {limits.data?.length ? (
          <div className="admin-table">
            <div className="admin-row header">
              <span>User</span>
              <span>Date</span>
              <span>Starts</span>
              <span>Remaining</span>
              <span>Last start</span>
              <span>Reset</span>
            </div>
            {limits.data.map((record) => (
              <div className="admin-row" key={record.email}>
                <strong>{record.email}</strong>
                <span>{record.date}</span>
                <span>
                  {record.count} / {record.limit}
                </span>
                <span>{record.remaining}</span>
                <span>{record.last_started_at ? formatDateTime(record.last_started_at) : "None"}</span>
                <button
                  className="secondary-action"
                  type="button"
                  onClick={() => reset.mutate(record.email)}
                  disabled={reset.isPending}
                >
                  <RotateCcw aria-hidden="true" />
                  <span>Reset</span>
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-panel">No limited users recorded today.</div>
        )}
        {reset.error && <div className="error-box">{reset.error.message}</div>}
      </section>
    </div>
  );
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}
