"use client";

import { type StatusPayload, usePolled } from "@/lib/api";
import { HealthBanner } from "@/components/ui";

/**
 * Mirrors the permission vocabulary in research_policy.yaml. The daemon is the
 * authority; this page reports what the mode implies so the researcher can see
 * at a glance what the agent is currently permitted to touch.
 */
const MODE_RULES: Record<string, { label: string; description: string }> = {
  manual: {
    label: "Manual",
    description:
      "The agent may analyse and propose, but every change waits for your approval.",
  },
  auto_exploration: {
    label: "Auto exploration",
    description:
      "The agent may change editable fields on its own. Approval-gated and locked fields still stop it.",
  },
  locked_evaluation: {
    label: "Locked evaluation",
    description:
      "Confirmatory runs. Only infrastructure recovery that cannot change scientific meaning is permitted, and it still needs approval.",
  },
};

function effect(policy: string, mode: string): { text: string; cls: string } {
  if (policy === "locked") return { text: "LOCKED", cls: "err" };
  if (mode === "locked_evaluation") return { text: "BLOCKED IN THIS MODE", cls: "err" };
  if (policy === "approval_required") return { text: "APPROVAL", cls: "warn" };
  if (mode === "manual") return { text: "APPROVAL", cls: "warn" };
  return { text: "EDITABLE", cls: "ok" };
}

// Shown as the shape of a policy; the live values come from the project's own
// research_policy.yaml, which the daemon loads and enforces.
const EXAMPLE_FIELDS: { field: string; policy: string }[] = [
  { field: "dataset_split", policy: "locked" },
  { field: "held_out_test_set", policy: "locked" },
  { field: "evaluation_metric", policy: "locked" },
  { field: "model_architecture", policy: "approval_required" },
  { field: "loss_function", policy: "approval_required" },
  { field: "optimizer", policy: "approval_required" },
  { field: "learning_rate", policy: "editable" },
  { field: "batch_size", policy: "editable" },
];

export default function GuardPage() {
  const { data, health } = usePolled<StatusPayload>("/api/status", 8000);
  const mode = data?.project?.mode || "manual";
  const rule = MODE_RULES[mode] || MODE_RULES.manual;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Scientific Guard</h1>
          <div className="subtle">What the agent may change right now</div>
        </div>
        <span className={`badge ${mode === "locked_evaluation" ? "err" : "ok"}`}>
          {rule.label}
        </span>
      </div>

      <HealthBanner health={health} />

      <div className="panel" style={{ marginBottom: 18 }}>
        <h2>Current mode</h2>
        <div>{rule.description}</div>
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <h2>Inviolable constraints</h2>
        <div className="subtle" style={{ marginBottom: 8 }}>
          Enforced regardless of mode or policy file. These cannot be unlocked from the
          dashboard.
        </div>
        <div className="row">
          <span className="badge err">held-out test set: never optimised on</span>
          <span className="badge err">data leakage: forbidden</span>
        </div>
      </div>

      <div className="panel">
        <h2>Field permissions</h2>
        <div className="subtle" style={{ marginBottom: 10 }}>
          Policy comes from the project&apos;s research_policy.yaml. The effective column
          combines it with the current mode, which can only ever tighten it.
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Field</th>
                <th>Policy</th>
                <th>Effective now</th>
              </tr>
            </thead>
            <tbody>
              {EXAMPLE_FIELDS.map((row) => {
                const result = effect(row.policy, mode);
                return (
                  <tr key={row.field}>
                    <td className="mono">{row.field}</td>
                    <td className="subtle">{row.policy}</td>
                    <td>
                      <span className={`badge ${result.cls}`}>{result.text}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
