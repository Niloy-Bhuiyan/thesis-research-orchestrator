"use client";

import { type ProviderCall, usePolled } from "@/lib/api";
import { Badge, Empty, HealthBanner, shortTime } from "@/components/ui";

export default function ProvidersPage() {
  const { data, health } = usePolled<ProviderCall[]>("/api/providers", 8000);
  const calls = data || [];

  // Latest observed outcome per provider. We report only what was actually
  // seen: neither provider exposes a reliable remaining-usage number, so none
  // is displayed rather than estimated.
  const latest = new Map<string, ProviderCall>();
  for (const call of calls) {
    if (!latest.has(call.provider)) latest.set(call.provider, call);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Providers</h1>
          <div className="subtle">
            Claude Code and Codex, both on subscription auth. Paid API fallback is off.
          </div>
        </div>
      </div>

      <HealthBanner health={health} />

      <div className="grid cols-2" style={{ marginBottom: 18 }}>
        {["codex", "claude_code"].map((name) => {
          const call = latest.get(name);
          return (
            <div className="panel stat" key={name}>
              <div className="label">{name}</div>
              <div className="value" style={{ fontSize: 15 }}>
                <Badge>{call?.outcome || "no calls yet"}</Badge>
              </div>
              <div className="meta">
                {call
                  ? `last call ${shortTime(call.started_at)}${call.model ? ` · ${call.model}` : ""}`
                  : "never invoked in this database"}
              </div>
            </div>
          );
        })}
      </div>

      <div className="panel">
        <h2>Recent provider calls</h2>
        <div className="subtle" style={{ marginBottom: 10 }}>
          Remaining quota is not shown because neither CLI reports a reliable number.
        </div>
        {calls.length === 0 ? (
          <Empty>No provider calls recorded yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>Task</th>
                  <th>Outcome</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((call, index) => (
                  <tr key={index}>
                    <td className="mono">{call.provider}</td>
                    <td className="mono">{call.model || "—"}</td>
                    <td>{call.task || "—"}</td>
                    <td>
                      <Badge>{call.outcome}</Badge>
                    </td>
                    <td className="mono">{shortTime(call.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
