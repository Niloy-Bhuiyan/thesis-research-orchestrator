"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type EventRow,
  type ProposalRow,
  type StatusPayload,
  sendCommand,
  usePolled,
} from "@/lib/api";
import { Badge, Empty, HealthBanner, Stat, metricText, shortTime } from "@/components/ui";

export default function DashboardPage() {
  const { data, health, reload } = usePolled<StatusPayload>("/api/status", 4000);
  const { data: events } = usePolled<EventRow[]>("/api/events", 6000);
  const { data: proposals, reload: reloadProposals } =
    usePolled<ProposalRow[]>("/api/proposals", 6000);
  const [busy, setBusy] = useState(false);

  const daemon = data?.daemon;
  const pending = (proposals || []).filter((p) => p.status === "pending");
  const online = health === "online";

  async function command(type: string, confirm = false) {
    setBusy(true);
    try {
      await sendCommand(type, {}, confirm);
      await Promise.all([reload(), reloadProposals()]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <div className="subtle">
            {data?.project ? `${data.project.name} · ${data.project.mode}` : "No project"}
          </div>
        </div>
        <div className="row">
          <button onClick={() => command("pause")} disabled={!online || busy}>
            Pause
          </button>
          <button onClick={() => command("resume")} disabled={!online || busy}>
            Resume
          </button>
        </div>
      </div>

      <HealthBanner health={health} heartbeat={daemon?.last_heartbeat} />

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <Stat
          label="Local runner"
          value={
            <>
              <span className={`dot ${online ? (daemon?.status === "running" ? "ok" : "warn") : "err"}`} />
              {online ? daemon?.status || "unknown" : "offline"}
            </>
          }
          meta={`heartbeat ${shortTime(daemon?.last_heartbeat)}`}
        />
        <Stat
          label="Active experiment"
          value={data?.active_experiment?.id || "—"}
          mono
          meta={data?.active_experiment?.status || "nothing running"}
        />
        <Stat
          label="Best result"
          value={data?.best_experiment ? metricText(data.best_experiment) : "—"}
          mono
          meta={data?.best_experiment?.id || "no completed experiment yet"}
        />
        <Stat
          label="Session"
          value={`${daemon?.experiments_this_session ?? 0} experiments`}
          meta={`${daemon?.consecutive_failures ?? 0} consecutive failures`}
        />
      </div>

      {daemon?.pause_reason ? (
        <div className="panel" style={{ marginBottom: 18 }}>
          <h2>Paused</h2>
          <div className="subtle">{daemon.pause_reason}</div>
        </div>
      ) : null}

      <div className="grid cols-2">
        <div className="panel">
          <h2>Pending approvals</h2>
          {pending.length === 0 ? (
            <Empty>Nothing is waiting on you.</Empty>
          ) : (
            pending.map((proposal) => (
              <div key={proposal.id} style={{ marginBottom: 12 }}>
                <div className="row" style={{ marginBottom: 4 }}>
                  <span className="mono">{proposal.experiment_id}</span>
                  <Badge>{proposal.kind}</Badge>
                  <Badge>{proposal.scientific_impact}</Badge>
                </div>
                <div style={{ marginBottom: 6 }}>{proposal.summary}</div>
                {proposal.detail ? (
                  <div className="subtle" style={{ marginBottom: 6 }}>
                    {proposal.detail}
                  </div>
                ) : null}
                <div className="row">
                  <button
                    className="primary"
                    disabled={!online || busy}
                    onClick={() => command("approve")}
                  >
                    Approve
                  </button>
                  <button disabled={!online || busy} onClick={() => command("reject")}>
                    Reject
                  </button>
                  <Link href={`/experiments/${proposal.experiment_id}`}>
                    <button disabled={!online}>Detail</button>
                  </Link>
                </div>
              </div>
            ))
          )}
        </div>

        <div className="panel">
          <h2>Recent activity</h2>
          {!events || events.length === 0 ? (
            <Empty>No events recorded yet.</Empty>
          ) : (
            events.slice(0, 12).map((event) => (
              <div className="event" key={event.id}>
                <span className="ts">{shortTime(event.created_at)}</span>
                <span className="kind">{event.kind}</span>
                <span>{event.message}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}
