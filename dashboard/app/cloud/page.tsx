"use client";

import { useState } from "react";
import {
  cloudConfigured,
  isStale,
  queueCommand,
  supabase,
  useCloud,
  useSession,
} from "@/lib/supabase";
import { Badge, Empty, shortTime } from "@/components/ui";

function SignIn() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    try {
      const { error } = await supabase().auth.signInWithOtp({
        email: email.trim(),
        options: { emailRedirectTo: window.location.href },
      });
      if (error) throw error;
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "sign-in failed");
    }
  }

  return (
    <div className="panel" style={{ maxWidth: 420 }}>
      <h2>Sign in</h2>
      <div className="subtle" style={{ marginBottom: 12 }}>
        A one-time link is emailed to you. No password is stored by this app.
      </div>
      {sent ? (
        <div>Check your email for the sign-in link.</div>
      ) : (
        <>
          <input
            type="text"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ marginBottom: 10 }}
          />
          <button className="primary" onClick={send} disabled={!email.includes("@")}>
            Send link
          </button>
          {error ? (
            <div className="subtle" style={{ marginTop: 8, color: "var(--err)" }}>
              {error}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

export default function CloudPage() {
  const { signedIn, ready, email } = useSession();
  const runnerId = process.env.NEXT_PUBLIC_RUNNER_ID || null;
  const { active, events, proposals, error, reload } = useCloud(runnerId);
  const [busy, setBusy] = useState(false);

  if (!cloudConfigured) {
    return (
      <>
        <h1>Remote control</h1>
        <div className="banner">
          <strong>Cloud mode is not configured.</strong> Set
          NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to enable remote
          control. Locally you do not need it: the other pages talk to the daemon
          directly.
        </div>
      </>
    );
  }

  if (!ready) return <div className="empty">Checking session…</div>;
  if (!signedIn) {
    return (
      <>
        <div className="page-head">
          <h1>Remote control</h1>
        </div>
        <SignIn />
      </>
    );
  }

  const offline = !active || isStale(active.last_heartbeat);

  async function command(type: string) {
    if (!active) return;
    setBusy(true);
    try {
      await queueCommand(active.id, type);
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Remote control</h1>
          <div className="subtle">Signed in as {email}</div>
        </div>
        <div className="row">
          <button onClick={() => command("pause")} disabled={busy || !active}>
            Pause
          </button>
          <button onClick={() => command("resume")} disabled={busy || !active}>
            Resume
          </button>
        </div>
      </div>

      {error ? <div className="banner">{error}</div> : null}

      {offline ? (
        <div className="banner">
          <strong>Local runner offline.</strong> No recent heartbeat
          {active?.last_heartbeat ? ` since ${shortTime(active.last_heartbeat)}` : ""}.
          Nothing is executing. Commands you queue here will run when the daemon next
          comes online, or expire after an hour.
        </div>
      ) : null}

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <div className="panel stat">
          <div className="label">Runner</div>
          <div className="value" style={{ fontSize: 15 }}>
            <span className={`dot ${offline ? "err" : "ok"}`} />
            {active ? active.status : "none registered"}
          </div>
          <div className="meta">heartbeat {shortTime(active?.last_heartbeat)}</div>
        </div>
        <div className="panel stat">
          <div className="label">Project</div>
          <div className="value mono">{active?.project_id || "—"}</div>
          <div className="meta">{active?.mode || "unknown mode"}</div>
        </div>
        <div className="panel stat">
          <div className="label">Active experiment</div>
          <div className="value mono">{active?.active_experiment || "—"}</div>
        </div>
        <div className="panel stat">
          <div className="label">Pending approvals</div>
          <div className="value">{proposals.length}</div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <h2>Approvals</h2>
        {proposals.length === 0 ? (
          <Empty>Nothing is waiting on you.</Empty>
        ) : (
          proposals.map((proposal) => (
            <div key={proposal.id} style={{ marginBottom: 14 }}>
              <div className="row" style={{ marginBottom: 4 }}>
                <span className="mono">{proposal.experiment_id}</span>
                <Badge>{proposal.kind}</Badge>
                <Badge>{proposal.scientific_impact}</Badge>
              </div>
              <div>{proposal.summary}</div>
              {proposal.detail ? <div className="subtle">{proposal.detail}</div> : null}
              <div className="row" style={{ marginTop: 6 }}>
                <button className="primary" disabled={busy} onClick={() => command("approve")}>
                  Approve
                </button>
                <button disabled={busy} onClick={() => command("reject")}>
                  Reject
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <h2>Recent activity</h2>
        {events.length === 0 ? (
          <Empty>No events synced yet.</Empty>
        ) : (
          events.slice(0, 20).map((event) => (
            <div className="event" key={event.id}>
              <span className="ts">{shortTime(event.occurred_at)}</span>
              <span className="kind">{event.kind}</span>
              <span>{event.message}</span>
            </div>
          ))
        )}
      </div>

      <div className="panel">
        <h2>What is not shown here</h2>
        <div className="subtle">
          Experiments, metrics, logs, code and artifacts are deliberately not synced to
          the cloud. They stay on your machine. Open the dashboard on that machine to see
          them. This page carries only what remote control needs.
        </div>
      </div>
    </>
  );
}
