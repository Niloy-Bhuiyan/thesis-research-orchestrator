"use client";

import Link from "next/link";
import { use } from "react";
import { type Experiment, type RunRow, usePolled } from "@/lib/api";
import { Badge, Empty, HealthBanner, duration, shortTime } from "@/components/ui";

interface Detail {
  experiment: Experiment;
  runs: RunRow[];
  metrics: { id: number; name: string; value: number; split: string | null; seed: number | null }[];
  children: Experiment[];
}

export default function ExperimentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data, health } = usePolled<Detail>(`/api/experiments/${id}`, 6000);

  if (!data) {
    return (
      <>
        <h1>{id}</h1>
        <HealthBanner health={health} />
        <Empty>Loading, or no such experiment.</Empty>
      </>
    );
  }

  const exp = data.experiment;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="mono">{exp.id}</h1>
          <div className="subtle">{exp.hypothesis || "No hypothesis recorded"}</div>
        </div>
        <Badge>{exp.status}</Badge>
      </div>

      <HealthBanner health={health} />

      <div className="grid cols-2" style={{ marginBottom: 18 }}>
        <div className="panel">
          <h2>Provenance</h2>
          <table>
            <tbody>
              <tr>
                <td className="subtle">Parent</td>
                <td className="mono">
                  {exp.parent_id ? (
                    <Link href={`/experiments/${exp.parent_id}`}>{exp.parent_id}</Link>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
              <tr>
                <td className="subtle">Git commit</td>
                <td className="mono">{exp.git_sha || "—"}</td>
              </tr>
              <tr>
                <td className="subtle">Methodology</td>
                <td className="mono">{exp.methodology_version || "—"}</td>
              </tr>
              <tr>
                <td className="subtle">Mode at creation</td>
                <td>{exp.mode}</td>
              </tr>
              <tr>
                <td className="subtle">Provider</td>
                <td>{exp.provider || "—"}</td>
              </tr>
              <tr>
                <td className="subtle">Retries</td>
                <td className="num">{exp.retry_count}</td>
              </tr>
              <tr>
                <td className="subtle">Failure class</td>
                <td>{exp.failure_class ? <Badge>{exp.failure_class}</Badge> : "—"}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="panel">
          <h2>Metrics</h2>
          {data.metrics.length === 0 ? (
            <Empty>No metrics reported by this experiment.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Value</th>
                  <th>Split</th>
                  <th>Seed</th>
                </tr>
              </thead>
              <tbody>
                {data.metrics.map((metric) => (
                  <tr key={metric.id}>
                    <td className="mono">{metric.name}</td>
                    <td className="num">{metric.value}</td>
                    <td>{metric.split || "—"}</td>
                    <td className="num">{metric.seed ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <h2>Runs</h2>
        {data.runs.length === 0 ? (
          <Empty>No execution attempts recorded.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Attempt</th>
                <th>Backend</th>
                <th>Kernel</th>
                <th>Accelerator</th>
                <th>Status</th>
                <th>Started</th>
                <th>Runtime</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((run) => (
                <tr key={run.id}>
                  <td className="num">{run.attempt}</td>
                  <td>{run.backend}</td>
                  <td className="mono">{run.kernel_ref || "—"}</td>
                  <td>{run.accelerator || "—"}</td>
                  <td>
                    <Badge>{run.status}</Badge>
                  </td>
                  <td className="mono">{shortTime(run.started_at)}</td>
                  <td className="num">{duration(run.started_at, run.ended_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>Derived experiments</h2>
        {data.children.length === 0 ? (
          <Empty>Nothing has branched from this experiment.</Empty>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {data.children.map((child) => (
              <li key={child.id} className="mono">
                <Link href={`/experiments/${child.id}`}>{child.id}</Link>{" "}
                <span className="subtle">{child.status}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
