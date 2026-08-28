"use client";

/**
 * Cloud mode.
 *
 * Only coordination metadata is synced upward, so this view deliberately
 * cannot show experiments, metrics or logs. That is a design choice, not a
 * missing feature: research artifacts stay on the researcher's machine. The UI
 * says so rather than rendering an empty table that implies data was lost.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { useCallback, useEffect, useState } from "react";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const cloudConfigured = Boolean(URL && ANON);

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (!cloudConfigured) throw new Error("cloud mode is not configured");
  if (!client) client = createClient(URL!, ANON!);
  return client;
}

export interface Runner {
  id: string;
  label: string | null;
  status: string;
  project_id: string | null;
  mode: string | null;
  active_experiment: string | null;
  pause_reason: string | null;
  last_heartbeat: string | null;
}

export interface CloudEvent {
  id: number;
  runner_id: string;
  level: string;
  kind: string;
  message: string;
  experiment_id: string | null;
  occurred_at: string;
}

export interface CloudProposal {
  id: string;
  local_id: string;
  experiment_id: string;
  kind: string | null;
  summary: string;
  detail: string | null;
  target_field: string | null;
  scientific_impact: string | null;
  status: string;
  created_at: string;
}

/** A heartbeat older than this means the laptop is almost certainly off. */
export const STALE_HEARTBEAT_MS = 3 * 60 * 1000;

export function isStale(heartbeat: string | null): boolean {
  if (!heartbeat) return true;
  return Date.now() - new Date(heartbeat).getTime() > STALE_HEARTBEAT_MS;
}

export function useSession() {
  const [email, setEmail] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!cloudConfigured) {
      setReady(true);
      return;
    }
    const client = supabase();
    client.auth.getSession().then(({ data }) => {
      setEmail(data.session?.user?.email ?? null);
      setReady(true);
    });
    const { data: sub } = client.auth.onAuthStateChange((_event, session) => {
      setEmail(session?.user?.email ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  return { email, ready, signedIn: Boolean(email) };
}

export function useCloud(runnerId: string | null) {
  const [runners, setRunners] = useState<Runner[]>([]);
  const [events, setEvents] = useState<CloudEvent[]>([]);
  const [proposals, setProposals] = useState<CloudProposal[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!cloudConfigured) return;
    const client = supabase();
    try {
      const [r, e, p] = await Promise.all([
        client.from("runners").select("*"),
        client
          .from("events")
          .select("*")
          .order("id", { ascending: false })
          .limit(50),
        client
          .from("proposals")
          .select("*")
          .eq("status", "pending")
          .order("created_at", { ascending: false }),
      ]);
      if (r.error) throw r.error;
      setRunners(r.data as Runner[]);
      setEvents((e.data || []) as CloudEvent[]);
      setProposals((p.data || []) as CloudProposal[]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "cloud read failed");
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 6000);
    return () => clearInterval(timer);
  }, [load]);

  const active = runnerId
    ? runners.find((r) => r.id === runnerId) || null
    : runners[0] || null;

  return { runners, active, events, proposals, error, reload: load };
}

/** Queue a command for the daemon to drain on its next poll. */
export async function queueCommand(
  runnerId: string,
  type: string,
  params: object = {},
) {
  const client = supabase();
  const { data: userData } = await client.auth.getUser();
  const owner = userData.user?.id;
  if (!owner) throw new Error("not signed in");
  const { error } = await client
    .from("commands")
    .insert({ owner, runner_id: runnerId, type, params });
  if (error) throw error;
}
