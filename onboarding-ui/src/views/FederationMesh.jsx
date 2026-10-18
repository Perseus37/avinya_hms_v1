import React, { useState } from 'react';
import { Loader2, Search, Network } from 'lucide-react';

export default function FederationMesh({ topology, request, notify, busyKey }) {
  const [mpiQuery, setMpiQuery] = useState("");
  const [mpiResult, setMpiResult] = useState(null);

  const searchIndex = async () => {
    if (!mpiQuery.trim()) return;
    try {
      const d = await request(`/api/mpi/lookup?query=${encodeURIComponent(mpiQuery.trim())}`, {}, "mpi-verify");
      setMpiResult(d);
    } catch (err) {
      notify(err.message, "err");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-black tracking-tight flex items-center gap-2">
          <Network className="w-6 h-6 theme-accent-text" />
          Zero-Knowledge Federation Mesh
        </h1>
        <p className="text-xs theme-text-muted mt-1">
          Verify SHA-256 patient routing pointers across hospitals without exposing clinical PII.
        </p>
      </div>

      <div className="theme-panel rounded-2xl p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs font-bold uppercase tracking-wider theme-accent-text">
            Live SHA-256 MPI Pointer Locator
          </div>
          <div className="w-full md:w-auto flex flex-col sm:flex-row gap-2">
            <input
              type="text"
              value={mpiQuery}
              onChange={(e) => setMpiQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && searchIndex()}
              placeholder="Phone, ABHA, or MRN..."
              className="theme-input rounded-xl px-3.5 py-2 text-xs font-mono w-full sm:w-64"
            />
            <button
              onClick={searchIndex}
              disabled={busyKey === "mpi-verify" || !mpiQuery.trim()}
              className="theme-accent-bg px-4 py-2 rounded-xl text-xs font-black flex items-center gap-1.5 shadow disabled:opacity-70 transition-opacity"
            >
              {busyKey === "mpi-verify" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
              <span>Search Index</span>
            </button>
          </div>
        </div>

        {mpiResult && (
          <div className="font-mono text-xs space-y-3 pt-3 border-t border-slate-500/20">
            <div className="flex items-center gap-2">
              <span className="text-slate-400">Matches:</span>
              <b className="theme-text-main">{mpiResult.matches_count}</b>
              <span className="text-slate-500 mx-2">&bull;</span>
              <span className="text-slate-400">Computed SHA-256 Hash:</span>
              <span className="theme-accent-text bg-slate-900 px-2 py-0.5 rounded">{mpiResult.computed_phone_sha256 || "N/A"}</span>
            </div>

            {mpiResult.pointers?.length === 0 && (
              <div className="text-slate-500 py-2">No active pointers found for this query in the global index.</div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {(mpiResult.pointers || []).map((p) => (
                <div key={p.id} className="theme-elevated p-3.5 rounded-xl flex items-center justify-between border border-slate-500/10 hover:border-slate-500/30 transition-colors">
                  <div>
                    <div className="font-bold text-sm theme-text-main">
                      {p.branch_code} <span className="text-slate-500">({p.subdomain_slug})</span> &bull; <span className="text-cyan-400">{p.source_mrn}</span>
                    </div>
                    <div className="text-[10px] text-slate-400 mt-1">
                      ABHA: {p.abha_number || "None Linked"}
                    </div>
                  </div>
                  <span className={`px-2.5 py-1 rounded text-[10px] font-bold ${
                    p.has_critical_allergies
                      ? "bg-rose-500/20 text-rose-400 border border-rose-500/30"
                      : "bg-emerald-500/10 text-emerald-500"
                  }`}>
                    {p.has_critical_allergies ? "CRITICAL ALLERGY" : "INDEXED"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {topology.length === 0 && (
          <div className="col-span-full py-8 text-center text-slate-500 text-sm">
            No active nodes in the federation topology. Provision a hospital to seed the mesh.
          </div>
        )}
        {topology.map((n) => (
          <div key={n.branch_code} className="theme-panel rounded-2xl p-5 space-y-3 hover:-translate-y-0.5 transition-transform">
            <div className="flex items-center justify-between">
              <span className="font-mono font-black text-sm theme-text-main">{n.branch_code}</span>
              <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                n.federation_sharing_mode === "ISOLATED_SILO" ? "bg-rose-500/20 text-rose-400" :
                n.federation_sharing_mode === "CORPORATE_GROUP_ONLY" ? "bg-amber-500/20 text-amber-400" :
                "bg-cyan-500/20 text-cyan-400"
              }`}>
                {(n.federation_sharing_mode || "UNKNOWN").replace(/_/g, " ")}
              </span>
            </div>
            <div className="text-xs font-bold theme-text-main">{n.hospital_name}</div>
            <div className="flex justify-between items-end text-xs font-mono theme-text-muted pt-3 border-t border-slate-500/20">
              <div>
                <div className="text-[9px] uppercase text-slate-500 mb-0.5">Key Prefix</div>
                <div className="theme-text-muted">{n.api_key_prefix}...</div>
              </div>
              <div className="text-right">
                <div className="text-[9px] uppercase text-slate-500 mb-0.5">Pointers</div>
                <div className="font-bold text-emerald-400">{n.indexed_pointers_count}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}