"use client";

/**
 * Client for the local daemon API.
 *
 * The dashboard talks to the daemon on the machine the browser runs on. When
 * the daemon is off we say so explicitly rather than rendering an empty page
 * that looks like "no experiments".
 */

import { useCallback, useEffect, useState } from "react";

const DEFAULT_BASE = "http://127.0.0.1:8765";

export function getBase(): string {
  if (typeof window === "undefined") return DEFAULT_BASE;
  return localStorage.getItem("researchos.base") || DEFAULT_BASE;
}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("researchos.token") || "";
}

export function setConnection(base: string, token: string) {
  localStorage.setItem("researchos.base", base);
  localStorage.setItem("researchos.token", token);
}

export type Health = "online" | "offline" | "unauthorized" | "unreachable_from_cloud";

/**
 * Whether this page can reach a loopback daemon at all.
 *
 * A page served over https cannot fetch http://127.0.0.1: Chrome gates
 * public-to-private requests behind a permission prompt, and opting in with
 * Access-Control-Allow-Private-Network is not sufficient. So the deployed
 * dashboard can never show local data, and saying "offline" there would be
 * misleading - the daemon may be running perfectly.
 */
export function canReachLocalDaemon(): boolean {
  if (typeof window === "undefined") return true;
  const { protocol, hostname } = window.location;
  if (protocol !== "https:") return true;
  return hostname === "localhost" || hostname === "127.0.0.1";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (response.status === 401) throw new Error("unauthorized");
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json() as Promise<T>;
}

export async function sendCommand(type: string, params?: object, confirm?: boolean) {
  return request<{ accepted: boolean; command_id: string }>("/api/commands", {
    method: "POST",
    body: JSON.stringify({ type, params: params || {}, confirm: !!confirm }),
  });
}

/** Poll an endpoint, tracking daemon reachability separately from data. */
export function usePolled<T>(path: string, intervalMs = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [health, setHealth] = useState<Health>("offline");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!canReachLocalDaemon()) {
      setHealth("unreachable_from_cloud");
      setLoading(false);
      return;
    }
    try {
      await fetch(`${getBase()}/api/health`, { cache: "no-store" });
    } catch {
      setHealth("offline");
      setLoading(false);
      return;
    }
    try {
      setData(await request<T>(path));
      setHealth("online");
    } catch (error) {
      setHealth(
        error instanceof Error && error.message === "unauthorized"
          ? "unauthorized"
          : "online",
      );
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    load();
    const timer = setInterval(load, intervalMs);
    return () => clearInterval(timer);
  }, [load, intervalMs]);

  return { data, health, loading, reload: load };
}

// ---- shared shapes ----

export interface Experiment {
  id: string;
  project_id: string;
  parent_id: string | null;
  hypothesis: string | null;
  status: string;
  mode: string;
  git_sha: string | null;
  methodology_version: string | null;
  primary_metric_name: string | null;
  primary_metric: number | null;
  metric_direction: string | null;
  failure_class: string | null;
  provider: string | null;
  retry_count: number;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface DaemonState {
  status: string;
  pid: number | null;
  last_heartbeat: string | null;
  active_experiment_id: string | null;
  pause_reason: string | null;
  experiments_this_session: number;
  consecutive_failures: number;
}

export interface StatusPayload {
  daemon: DaemonState;
  project: { id: string; name: string; mode: string; methodology_version: string | null } | null;
  active_experiment: Experiment | null;
  best_experiment: Experiment | null;
}

export interface EventRow {
  id: number;
  level: string;
  kind: string;
  message: string;
  experiment_id: string | null;
  created_at: string;
}

export interface RunRow {
  id: string;
  experiment_id: string;
  attempt: number;
  backend: string;
  kernel_ref: string | null;
  accelerator: string | null;
  internet_enabled: number | null;
  status: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface ProposalRow {
  id: string;
  experiment_id: string;
  kind: string;
  summary: string;
  detail: string | null;
  target_field: string | null;
  scientific_impact: string | null;
  status: string;
  created_at: string;
}

export interface ProviderCall {
  provider: string;
  outcome: string;
  model: string | null;
  task: string | null;
  started_at: string;
}
