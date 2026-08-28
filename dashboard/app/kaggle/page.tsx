"use client";

import Link from "next/link";
import { type RunRow, usePolled } from "@/lib/api";
import { Badge, Empty, HealthBanner, duration, shortTime } from "@/components/ui";

export default function KagglePage() {
  const { data, health } = usePolled<RunRow[]>("/api/runs", 8000);
  const runs = data || [];
  const active = runs.filter((r) => r.status === "running" || r.status === "submitted");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Kaggle</h1>
          <div className="subtle">GPU execution backend and run history</div>
        </div>
        <span className={`badge ${active.length ? "ok" : "idle"}`}>
          {active.length ? `${active.length} in flight` : "idle"}
        </span>
      </div>

      <HealthBanner health={health} />

      <div className="panel" style={{ marginBottom: 18 }}>
        <h2>Quota</h2>
        <div className="subtle">
          Kaggle does not expose remaining GPU hours through the CLI, so no figure is
          shown here. Quota exhaustion is detected when a push or status call reports it,
          and the system then packages an external run bundle instead of retrying.
        </div>
      </div>

      <div className="panel">
        <h2>Runs</h2>
        {runs.length === 0 ? (
          <Empty>No runs recorded yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Experiment</th>
                  <th>Attempt</th>
                  <th>Backend</th>
                  <th>Kernel</th>
                  <th>Accelerator</th>
                  <th>Internet</th>
                  <th>Status</th>
                  <th>Runtime</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td className="mono">
                      <Link href={`/experiments/${run.experiment_id}`}>
                        {run.experiment_id}
                      </Link>
                    </td>
                    <td className="num">{run.attempt}</td>
                    <td>{run.backend}</td>
                    <td className="mono">{run.kernel_ref || "—"}</td>
                    <td>{run.accelerator || "—"}</td>
                    <td>
                      {run.internet_enabled === null
                        ? "—"
                        : run.internet_enabled
                          ? "on"
                          : "off"}
                    </td>
                    <td>
                      <Badge>{run.status}</Badge>
                    </td>
                    <td className="num">{duration(run.started_at, run.ended_at)}</td>
                    <td className="mono">{shortTime(run.started_at)}</td>
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
