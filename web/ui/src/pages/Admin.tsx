import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, ShieldCheck } from "lucide-react";
import { api } from "../api";
import { Truncate } from "../components/Truncate";
import { PanelError, PanelLoading, EmptyState } from "../components/StateBlock";

export function Admin() {
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const isAdmin = session.data?.is_admin === true;
  const limits = useQuery({
    queryKey: ["rate-limits"],
    queryFn: api.rateLimits,
    enabled: isAdmin
  });
  const reset = useMutation({
    mutationFn: api.resetRateLimit,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rate-limits"] });
      queryClient.invalidateQueries({ queryKey: ["me"] });
    }
  });

  if (session.isLoading) {
    return (
      <div className="page">
        <AdminHeader />
        <PanelLoading label="Checking access" lines={3} tall />
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="page">
        <AdminHeader />
        <EmptyState tall icon={ShieldCheck}>Admin access required.</EmptyState>
      </div>
    );
  }

  return (
    <div className="page">
      <AdminHeader />

      <section className="dashboard-grid">
        <div className="panel">
          <div className="section-title">
            <ShieldCheck aria-hidden="true" />
            <h2>Admins</h2>
          </div>
          <div className="tag-row">
            {session.data?.admin_emails.map((email) => (
              <span key={email}>
                <Truncate>{email}</Truncate>
              </span>
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
              <dd>
                <Truncate>{session.data?.email ?? "Loading"}</Truncate>
              </dd>
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
        {limits.isLoading ? (
          <PanelLoading label="Loading rate limits" lines={4} />
        ) : limits.isError ? (
          <PanelError error={limits.error} onRetry={() => limits.refetch()} />
        ) : limits.data?.length ? (
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
                <strong data-label="User">
                  <Truncate>{record.email}</Truncate>
                </strong>
                <span data-label="Date">
                  <Truncate>{record.date}</Truncate>
                </span>
                <span data-label="Starts">
                  {record.count} / {record.limit}
                </span>
                <span data-label="Remaining">{record.remaining}</span>
                <span data-label="Last start">
                  <Truncate>{record.last_started_at ? formatDateTime(record.last_started_at) : "None"}</Truncate>
                </span>
                <div className="admin-action-cell" data-label="Reset">
                  <button className="secondary-action" type="button" onClick={() => reset.mutate(record.email)} disabled={reset.isPending}>
                    <RotateCcw aria-hidden="true" />
                    <span>Reset</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState>No limited users recorded today.</EmptyState>
        )}
        {reset.error && <div className="error-box">{reset.error.message}</div>}
      </section>
    </div>
  );
}

function AdminHeader() {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">Access control</p>
        <h1>Admin console</h1>
      </div>
    </header>
  );
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}
