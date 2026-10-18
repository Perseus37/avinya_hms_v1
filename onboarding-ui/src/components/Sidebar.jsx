import React from 'react';
import { BarChart4, Building2, Sparkles, Database, GitBranch, Network, Shield, Palette, KeyRound, Loader2, RefreshCw, Terminal, ChevronRight } from 'lucide-react';
import { THEMES } from '../lib/constants';

export default function Sidebar({ theme, setTheme, activeView, setActiveView, masterKey, setMasterKey, controlPlaneKey, setControlPlaneKey, syncAll, loading, apiLogs, setDevtoolsOpen }) {
  return (
    <aside className="w-full md:w-72 md:h-screen md:sticky md:top-0 theme-panel border-r flex flex-col justify-between p-4 shrink-0 md:overflow-y-auto select-none no-scrollbar">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl theme-accent-bg flex items-center justify-center font-black text-lg shadow-lg">A</div>
            <div>
              <div className="font-black tracking-tight text-sm">AVINYA NEXUS</div>
              <div className="text-[10px] font-mono theme-text-muted">CLOUD CONTROL OS v5.1</div>
            </div>
          </div>
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-ping" />
        </div>

        <div className="theme-elevated p-2.5 rounded-xl space-y-2">
          <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-wider theme-text-muted">
            <span className="flex items-center gap-1"><Palette className="w-3 h-3" /> UI Theme Engine</span>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {THEMES.map((t) => (
              <button
                key={t.id} onClick={() => setTheme(t.id)}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-semibold flex items-center gap-2 transition border ${theme === t.id ? "theme-panel font-bold border-current" : "opacity-70 hover:opacity-100 border-transparent"}`}
              >
                <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: t.dot }} />
                <span className="truncate">{t.name}</span>
              </button>
            ))}
          </div>
        </div>

        <nav className="space-y-1.5">
          <div className="text-[10px] font-bold uppercase tracking-wider theme-text-muted px-2 mb-2">Control Plane Modules</div>
          {[
            { id: "command-center", label: "Executive Telemetry & BI", sub: "ARR, GMV & Radial Gauges", icon: BarChart4 },
            { id: "fleet-matrix", label: "Hospital Fleet Explorer", sub: "Provisioned Tenants", icon: Building2 },
            { id: "provision-studio", label: "4-Step Onboarding Studio", sub: "Visual Blueprint & Quote", icon: Sparkles },
            { id: "migration-studio", label: "Visual ETL Studio", sub: "Legacy Data Migration", icon: Database },
            { id: "canary-migrations", label: "Canary & Schema Rollout", sub: "2-Stage Alembic Pipeline", icon: GitBranch },
            { id: "federation-mesh", label: "Zero-Knowledge MPI Mesh", sub: "Sovereignty & Hash Verifier", icon: Network },
            { id: "audit-vault", label: "Forensic Audit Ledger", sub: "Immutable Events", icon: Shield },
          ].map((item) => {
            const Icon = item.icon;
            const active = activeView === item.id;
            return (
              <button
                key={item.id} onClick={() => setActiveView(item.id)}
                className={`w-full text-left px-3.5 py-3 rounded-xl transition flex items-center justify-between ${active ? "theme-accent-bg shadow-lg" : "hover:opacity-85 theme-elevated"}`}
              >
                <div className="flex items-center gap-3">
                  <Icon className="w-4 h-4 shrink-0" />
                  <div>
                    <div className="text-xs font-bold leading-none">{item.label}</div>
                    <div className={`text-[10px] mt-1 font-mono ${active ? "opacity-90" : "theme-text-muted"}`}>{item.sub}</div>
                  </div>
                </div>
                <ChevronRight className="w-3.5 h-3.5 opacity-60" />
              </button>
            );
          })}
        </nav>
      </div>

      <div className="space-y-3 pt-4 border-t border-slate-500/20">
        <div className="space-y-1.5">
          <label className="text-[10px] font-bold uppercase font-mono theme-text-muted flex items-center gap-1">
            <KeyRound className="w-3 h-3" /> Master Vendor Key
          </label>
          <input
            type="password" value={masterKey} onChange={(e) => setMasterKey(e.target.value)}
            className="w-full theme-input rounded-xl px-3 py-2 text-xs font-mono"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-[10px] font-bold uppercase font-mono theme-text-muted flex items-center gap-1">
            <KeyRound className="w-3 h-3" /> Control Plane Secret (if configured)
          </label>
          <input
            type="password" value={controlPlaneKey} onChange={(e) => setControlPlaneKey(e.target.value)}
            className="w-full theme-input rounded-xl px-3 py-2 text-xs font-mono"
          />
        </div>
        <div className="flex gap-2">
          <button onClick={() => syncAll(false)} disabled={loading} className="flex-1 theme-accent-bg font-bold py-2 rounded-xl text-xs flex items-center justify-center gap-1.5 shadow-md transition-opacity disabled:opacity-70">
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            <span>Sync Cluster</span>
          </button>
          <button onClick={() => setDevtoolsOpen((v) => !v)} title="Toggle API Latency & Payload Console" className="px-3 py-2 rounded-xl theme-elevated text-xs font-mono flex items-center gap-1 transition-colors hover:text-cyan-400">
            <Terminal className="w-3.5 h-3.5" />
            <span>{apiLogs?.length || 0}</span>
          </button>
        </div>
      </div>
    </aside>
  );
}