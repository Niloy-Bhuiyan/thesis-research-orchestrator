"use client";

import type { Health } from "@/lib/api";

/** Map a domain status onto one of four visual tones. */
export function tone(status: string | null | undefined): "ok" | "warn" | "err" | "idle" {
  switch ((status || "").toLowerCase()) {
    case "completed":
    case "complete":
    case "running":
    case "available":
    case "approved":
    case "success":
      return "ok";
    case "failed":
    case "error":
    case "rejected":
    case "timeout":
    case "usage_limited":
    case "auth_required":
    case "unavailable":
      return "err";
    case "paused":
    case "pending":
    case "queued":
    case "submitted":
    case "rate_limited":
    case "unknown":
      return "warn";
    default:
      return "idle";
  }
}

export function Badge({ children }: { children: string | null | undefined }) {
  const text = children || "-";
  return <span className={`badge ${tone(text)}`}>{text}</span>;
}

export function Stat({
  label,
  value,
  meta,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  meta?: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="panel stat">
      <div className="label">{label}</div>
      <div className={`value${mono ? " mono" : ""}`}>{value}</div>
      {meta ? <div className="meta">{meta}</div> : null}
    </div>
  );
}

/**
 * The dashboard must never imply the research engine is running when the
 * laptop is off, so offline is stated plainly with the command to fix it.
 */
export function HealthBanner({
  health,
  heartbeat,
}: {
  health: Health;
  heartbeat?: string | null;
}) {
  if (health === "online") return null;
  if (health === "unauthorized") {
    return (
      <div className="banner">
        <strong>Not authorized.</strong> The daemon is reachable but the API token is
        wrong or missing. Run <code>researchos token</code> and paste it into Settings.
      </div>
    );
  }
  return (
    <div className="banner">
      <strong>Local runner offline.</strong> The daemon is not reachable on this
      machine, so nothing is executing. Last heartbeat: {heartbeat || "never"}. Start it
      with <code>researchos start</code>.
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function shortTime(value: string | null | undefined): string {
  if (!value) return "-";
  return value.replace("T", " ").replace("+00:00", "").slice(0, 19);
}

export function metricText(exp: {
  primary_metric: number | null;
  primary_metric_name: string | null;
}): string {
  if (exp.primary_metric === null) return "not reported";
  return `${exp.primary_metric_name || "metric"} ${exp.primary_metric}`;
}

export function duration(start: string | null, end: string | null): string {
  if (!start || !end) return "-";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (Number.isNaN(ms) || ms < 0) return "-";
  const minutes = Math.floor(ms / 60000);
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
