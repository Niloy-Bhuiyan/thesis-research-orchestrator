"use client";

import Link from "next/link";
import { type Experiment, usePolled } from "@/lib/api";
import { Empty, HealthBanner } from "@/components/ui";

/** Render the experiment forest as an indented tree, best branch marked. */
function renderTree(
  experiments: Experiment[],
  bestId: string | null,
  parentId: string | null = null,
  depth = 0,
): React.ReactNode[] {
  return experiments
    .filter((e) => e.parent_id === parentId)
    .flatMap((exp) => {
      const rail = depth === 0 ? "" : `${"   ".repeat(depth - 1)}+-- `;
      const isBest = exp.id === bestId;
      return [
        <div key={exp.id}>
          <span className="rail">{rail}</span>
          <Link href={`/experiments/${exp.id}`}>
            <span className={isBest ? "best" : "node"}>{exp.id}</span>
          </Link>
          <span className="rail">
            {"  "}
            {exp.status}
            {exp.primary_metric !== null ? `  ${exp.primary_metric}` : ""}
            {isBest ? "  <- best" : ""}
          </span>
        </div>,
        ...renderTree(experiments, bestId, exp.id, depth + 1),
      ];
    });
}

export default function LineagePage() {
  const { data, health } = usePolled<Experiment[]>("/api/experiments", 8000);
  const experiments = data || [];

  const scored = experiments.filter((e) => e.primary_metric !== null);
  const best =
    scored.length > 0
      ? scored.reduce((a, b) =>
          (a.metric_direction === "minimize"
            ? b.primary_metric! < a.primary_metric!
            : b.primary_metric! > a.primary_metric!)
            ? b
            : a,
        )
      : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Lineage</h1>
          <div className="subtle">
            Every experiment and how it descends from the one before it
          </div>
        </div>
      </div>

      <HealthBanner health={health} />

      <div className="panel">
        {experiments.length === 0 ? (
          <Empty>No experiments to graph yet.</Empty>
        ) : (
          <div className="lineage">{renderTree(experiments, best?.id ?? null)}</div>
        )}
      </div>
    </>
  );
}
