"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { type Experiment, usePolled } from "@/lib/api";
import { Badge, Empty, HealthBanner, duration, shortTime } from "@/components/ui";

export default function ExperimentsPage() {
  const { data, health } = usePolled<Experiment[]>("/api/experiments", 6000);
  const [query, setQuery] = useState("");

  const experiments = data || [];

  /** Best value so far, used to show each result's delta against it. */
  const best = useMemo(() => {
    const scored = experiments.filter((e) => e.primary_metric !== null);
    if (scored.length === 0) return null;
    const minimize = scored[0].metric_direction === "minimize";
    return scored.reduce((a, b) =>
      (minimize ? b.primary_metric! < a.primary_metric! : b.primary_metric! > a.primary_metric!)
        ? b
        : a,
    );
  }, [experiments]);

  const filtered = experiments.filter((e) => {
    const haystack = `${e.id} ${e.hypothesis || ""} ${e.status} ${e.failure_class || ""}`;
    return haystack.toLowerCase().includes(query.toLowerCase());
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Experiments</h1>
          <div className="subtle">
            {experiments.length} recorded, including failures and rejections
          </div>
        </div>
        <div style={{ width: 240 }}>
          <input
            type="text"
            placeholder="Filter by id, hypothesis, status"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <HealthBanner health={health} />

      {filtered.length === 0 ? (
        <Empty>
          {experiments.length === 0
            ? "No experiments yet. Create one with the CLI or start the daemon in auto mode."
            : "No experiments match that filter."}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Parent</th>
                <th>Hypothesis</th>
                <th>Status</th>
                <th>Metric</th>
                <th>Δ best</th>
                <th>Retries</th>
                <th>Failure</th>
                <th>Runtime</th>
                <th>Commit</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((exp) => {
                const delta =
                  best && exp.primary_metric !== null && best.primary_metric !== null
                    ? exp.primary_metric - best.primary_metric
                    : null;
                return (
                  <tr key={exp.id}>
                    <td className="mono">
                      <Link href={`/experiments/${exp.id}`}>{exp.id}</Link>
                    </td>
                    <td className="mono">{exp.parent_id || "—"}</td>
                    <td>{exp.hypothesis || <span className="subtle">—</span>}</td>
                    <td>
                      <Badge>{exp.status}</Badge>
                    </td>
                    <td className="num">
                      {exp.primary_metric === null ? "—" : exp.primary_metric}
                    </td>
                    <td className="num">
                      {delta === null ? "—" : delta === 0 ? "best" : delta.toFixed(4)}
                    </td>
                    <td className="num">{exp.retry_count}</td>
                    <td>
                      {exp.failure_class ? (
                        <Badge>{exp.failure_class}</Badge>
                      ) : (
                        <span className="subtle">—</span>
                      )}
                    </td>
                    <td className="num">{duration(exp.started_at, exp.ended_at)}</td>
                    <td className="mono">{exp.git_sha ? exp.git_sha.slice(0, 7) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
