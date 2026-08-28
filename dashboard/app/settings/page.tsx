"use client";

import { useEffect, useState } from "react";
import { getBase, getToken, setConnection, usePolled } from "@/lib/api";
import type { StatusPayload } from "@/lib/api";
import { HealthBanner } from "@/components/ui";

export default function SettingsPage() {
  const [base, setBase] = useState("");
  const [token, setToken] = useState("");
  const [saved, setSaved] = useState(false);
  const { data, health, reload } = usePolled<StatusPayload>("/api/status", 10000);

  useEffect(() => {
    setBase(getBase());
    setToken(getToken());
  }, []);

  function save() {
    setConnection(base.trim(), token.trim());
    setSaved(true);
    reload();
    setTimeout(() => setSaved(false), 2500);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <div className="subtle">How this browser reaches your local daemon</div>
        </div>
      </div>

      <HealthBanner health={health} heartbeat={data?.daemon?.last_heartbeat} />

      <div className="panel" style={{ marginBottom: 18, maxWidth: 560 }}>
        <h2>Connection</h2>
        <div className="subtle" style={{ marginBottom: 12 }}>
          The daemon listens on loopback only. This page talks to it directly from your
          browser, so it works when the browser runs on the same machine as the daemon.
        </div>

        <label className="subtle">Daemon address</label>
        <input
          type="text"
          value={base}
          onChange={(e) => setBase(e.target.value)}
          placeholder="http://127.0.0.1:8765"
          style={{ marginBottom: 12 }}
        />

        <label className="subtle">API token</label>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="paste the output of: researchos token"
          style={{ marginBottom: 12 }}
        />

        <div className="row">
          <button className="primary" onClick={save}>
            Save
          </button>
          {saved ? <span className="subtle">Saved to this browser.</span> : null}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 18, maxWidth: 560 }}>
        <h2>Safety</h2>
        <table>
          <tbody>
            <tr>
              <td className="subtle">Paid API fallback</td>
              <td>
                <span className="badge ok">OFF</span>
              </td>
            </tr>
            <tr>
              <td className="subtle">Kaggle accounts</td>
              <td>
                <span className="badge ok">single account only</span>
              </td>
            </tr>
            <tr>
              <td className="subtle">Inbound ports</td>
              <td>
                <span className="badge ok">none; loopback only</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div className="subtle" style={{ marginTop: 10 }}>
          These are enforced in the daemon, not here. This panel reports them; it cannot
          change them.
        </div>
      </div>

      <div className="panel" style={{ maxWidth: 560 }}>
        <h2>Project</h2>
        <table>
          <tbody>
            <tr>
              <td className="subtle">Active project</td>
              <td className="mono">{data?.project?.id || "—"}</td>
            </tr>
            <tr>
              <td className="subtle">Mode</td>
              <td>{data?.project?.mode || "—"}</td>
            </tr>
            <tr>
              <td className="subtle">Methodology version</td>
              <td className="mono">{data?.project?.methodology_version || "—"}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}
