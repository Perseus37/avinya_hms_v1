import React from 'react';
import { Loader2, GitBranch } from 'lucide-react';

export default function CanaryPipeline({ schemaParity, request, notify, syncAll, masterKey, busyKey }) {
  const executeFleetSync = async () => {
    if (!window.confirm("WARNING: This will apply the current Alembic head migration to ALL active cloud hospitals. Proceed?")) return;
    try {
      const d = await request("/api/tenants/sync-fleet", {
        method: "POST",
        body: JSON.stringify({ master_setup_key: masterKey.trim() }),
      }, "sync-all-fleet");
      notify(`Stage 2 Complete: Synced ${d.synced_tenants_count} hospitals.`);
      await syncAll(true);
    } catch (err) {
      notify(err.message, "err");
    }
  };

  const syncSingleTenant = async (slug) => {
    try {
      const d = await request(`/api/tenants/${slug}/sync`, {
        method: "POST",
        body: JSON.stringify({ master_setup_key: masterKey.trim() }),
      }, `sync-${slug}`);
      notify(d.message);
      await syncAll(true);
    } catch (err) {
      notify(err.message, "err");
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-black tracking-tight flex items-center gap-2">
            <GitBranch className="w-6 h-6 theme-accent-text" />
            2-Stage Canary &amp; Fleet Schema Pipeline
          </h1>
          <p className="text-xs theme-text-muted mt-1">
            Verify migrations on <span className="font-mono font-bold theme-text-main">hms_default</span> first, then push the verified Alembic head across the hospital fleet.
          </p>
        </div>
        <button
          onClick={executeFleetSync}
          disabled={busyKey === "sync-all-fleet"}
          className="theme-accent-bg px-4 py-2.5 rounded-xl text-xs font-black flex items-center gap-2 shadow-lg disabled:opacity-70 transition-opacity"
        >
          {busyKey === "sync-all-fleet" ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          <span>Stage 2: Push Verified Head to Entire Fleet</span>
        </button>
      </div>

      <div className="theme-panel rounded-2xl text-xs font-mono overflow-x-auto">
        <table className="w-full min-w-[900px] text-left">
          <thead>
            <tr className="border-b border-slate-500/20 text-[11px] uppercase theme-text-muted">
              <th className="py-3 px-4">Tenant</th>
              <th className="py-3 px-4">Database</th>
              <th className="py-3 px-4">Current Revision</th>
              <th className="py-3 px-4">Target Head</th>
              <th className="py-3 px-4">Status</th>
              <th className="py-3 px-4 text-right">Stage 1 Action</th>
            </tr>
          </thead>
          <tbody>
            {(!schemaParity?.parity || schemaParity.parity.length === 0) && (
              <tr>
                <td colSpan="6" className="py-8 text-center text-slate-500 font-sans">
                  No schema parity data available. Click "Sync Cluster" to refresh.
                </td>
              </tr>
            )}
            {(schemaParity?.parity || []).map((p) => (
              <tr key={p.subdomain_slug} className="border-b border-slate-500/10 theme-row-hover transition-colors">
                <td className="py-3 px-4 font-bold theme-text-main">
                  {p.subdomain_slug}
                  {p.subdomain_slug === "default" && (
                    <span className="ml-2 px-2 py-0.5 rounded text-[9px] theme-accent-bg">CANARY</span>
                  )}
                </td>
                <td className="py-3 px-4 theme-text-muted">{p.db_name}</td>
                <td className="py-3 px-4 theme-accent-text font-bold">{p.schema_version}</td>
                <td className="py-3 px-4 text-slate-500">{p.head_revision}</td>
                <td className="py-3 px-4">
                  <span className={`px-2.5 py-0.5 rounded text-[10px] font-bold ${
                    p.status === "UP_TO_DATE"
                      ? "bg-emerald-500/20 text-emerald-400"
                      : "bg-amber-500/20 text-amber-400"
                  }`}>
                    {(p.status || "UNKNOWN").replace(/_/g, " ")}
                  </span>
                </td>
                <td className="py-3 px-4 text-right">
                  <button
                    onClick={() => syncSingleTenant(p.subdomain_slug)}
                    disabled={busyKey === `sync-${p.subdomain_slug}`}
                    className="theme-elevated hover:brightness-110 px-3 py-1.5 rounded-lg font-bold flex items-center gap-1.5 ml-auto transition-colors disabled:opacity-50"
                  >
                    {busyKey === `sync-${p.subdomain_slug}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                    <span>Upgrade &amp; Sync</span>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}