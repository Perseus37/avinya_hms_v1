import React, { useState, useMemo } from 'react';
import { LayoutGrid, List, ArrowUpRight, Loader2 } from 'lucide-react';
import { fmtInr } from '../lib/constants';

export default function FleetExplorer({ tenants, inspectHospital, busyKey }) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [fleetLayout, setFleetLayout] = useState("grid");

  // Memoized filter logic for instant search across thousands of tenants
  const filteredTenants = useMemo(() => {
    const q = search.toLowerCase();
    return tenants.filter((t) => {
      const match = t.hospital_name.toLowerCase().includes(q) ||
                    t.subdomain_slug.toLowerCase().includes(q) ||
                    t.branch_code.toLowerCase().includes(q);
      if (!match) return false;

      if (statusFilter === "ACTIVE") return t.is_active && !t.maintenance_mode;
      if (statusFilter === "MAINTENANCE") return t.is_active && t.maintenance_mode;
      if (statusFilter === "SUSPENDED") return !t.is_active;
      return true;
    });
  }, [tenants, search, statusFilter]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-black tracking-tight">Hospital Tenant Explorer</h1>
          <p className="text-xs theme-text-muted">Click Command Drawer on any hospital to switch packages, rotate keys, clone sandboxes, or restore snapshots.</p>
        </div>
        <div className="w-full lg:w-auto flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search hospital..."
            className="theme-input rounded-xl px-3.5 py-2 text-xs w-full sm:w-64"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="theme-input rounded-xl px-3 py-2 text-xs"
          >
            <option value="ALL">All ({tenants.length})</option>
            <option value="ACTIVE">Active</option>
            <option value="MAINTENANCE">Maintenance</option>
            <option value="SUSPENDED">Suspended</option>
          </select>
          <div className="theme-elevated p-1 rounded-xl flex">
            <button
              onClick={() => setFleetLayout("grid")}
              className={`p-1.5 rounded-lg transition-colors ${fleetLayout === "grid" ? "theme-accent-bg" : "hover:bg-slate-500/10"}`}
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button
              onClick={() => setFleetLayout("list")}
              className={`p-1.5 rounded-lg transition-colors ${fleetLayout === "list" ? "theme-accent-bg" : "hover:bg-slate-500/10"}`}
            >
              <List className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {fleetLayout === "grid" ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filteredTenants.length === 0 && (
            <div className="col-span-full py-12 text-center text-slate-500 text-sm">
              No hospital tenants match your current search filters.
            </div>
          )}
          {filteredTenants.map((t) => (
            <div key={t.id} className="theme-panel rounded-2xl p-5 flex flex-col justify-between space-y-4 hover:scale-[1.01] transition-transform">
              <div>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-black text-base flex items-center gap-2">
                      <span>{t.hospital_name}</span>
                      {t.subdomain_slug === "default" && <span className="px-2 py-0.5 rounded text-[9px] font-mono theme-accent-bg">CANARY</span>}
                    </div>
                    <div className="text-xs font-mono theme-text-muted mt-0.5">{t.subdomain_slug} &bull; {t.branch_code}</div>
                  </div>
                  <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${!t.is_active ? "bg-rose-500/20 text-rose-500" : t.maintenance_mode ? "bg-amber-500/20 text-amber-500" : "bg-emerald-500/20 text-emerald-500"}`}>
                    {!t.is_active ? "SUSPENDED" : t.maintenance_mode ? "MAINTENANCE" : "ONLINE"}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2.5 mt-4 theme-elevated p-3.5 rounded-xl text-xs font-mono">
                  <div>
                    <div className="text-[10px] theme-text-muted">DATABASE</div>
                    <div className="font-bold truncate" title={t.db_name}>{t.db_name}</div>
                  </div>
                  <div>
                    <div className="text-[10px] theme-text-muted">MODULES</div>
                    <div className="font-bold text-emerald-500">{t.active_modules_count} Active</div>
                  </div>
                  <div>
                    <div className="text-[10px] theme-text-muted">PACKAGE</div>
                    <div className="font-bold theme-accent-text truncate" title={t.pricing_model}>{t.pricing_model}</div>
                  </div>
                  <div>
                    <div className="text-[10px] theme-text-muted">CONTRACT FEE</div>
                    <div className="font-bold">{fmtInr(t.base_license_fee_paise)}</div>
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-slate-500/20 mt-2">
                <a
                  href={`${t.tenant_url}/docs`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-mono theme-text-muted hover:underline flex items-center gap-1 transition-opacity hover:opacity-80"
                >
                  <span>Open Swagger</span> <ArrowUpRight className="w-3.5 h-3.5" />
                </a>
                <button
                  onClick={() => inspectHospital(t.subdomain_slug)}
                  disabled={busyKey === `inspect-${t.subdomain_slug}`}
                  className="theme-accent-bg px-4 py-2 rounded-xl text-xs font-bold flex items-center gap-1.5 shadow disabled:opacity-70 transition-opacity"
                >
                  {busyKey === `inspect-${t.subdomain_slug}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                  <span>Command Drawer &rarr;</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="theme-panel rounded-2xl text-xs font-mono overflow-x-auto">
          <table className="w-full min-w-[760px] text-left">
            <thead>
              <tr className="border-b border-slate-500/20 text-[11px] uppercase theme-text-muted">
                <th className="py-3 px-4">Hospital</th>
                <th className="py-3 px-4">Subdomain / DB</th>
                <th className="py-3 px-4">Tier</th>
                <th className="py-3 px-4">Modules</th>
                <th className="py-3 px-4">Fee</th>
                <th className="py-3 px-4 text-right">Manage</th>
              </tr>
            </thead>
            <tbody>
              {filteredTenants.length === 0 && (
                <tr>
                  <td colSpan="6" className="py-8 text-center text-slate-500 font-sans">
                    No hospital tenants match your current search filters.
                  </td>
                </tr>
              )}
              {filteredTenants.map((t) => (
                <tr key={t.id} className="border-b border-slate-500/10 theme-row-hover transition-colors">
                  <td className="py-3 px-4 font-bold">{t.hospital_name}</td>
                  <td className="py-3 px-4">
                    {t.subdomain_slug} <span className="theme-text-muted">({t.db_name})</span>
                  </td>
                  <td className="py-3 px-4 theme-accent-text">{t.pricing_model}</td>
                  <td className="py-3 px-4">{t.active_modules_count} Active</td>
                  <td className="py-3 px-4 font-bold">{fmtInr(t.base_license_fee_paise)}</td>
                  <td className="py-3 px-4 text-right">
                    <button
                      onClick={() => inspectHospital(t.subdomain_slug)}
                      disabled={busyKey === `inspect-${t.subdomain_slug}`}
                      className="theme-accent-bg px-3 py-1.5 rounded-lg font-bold flex items-center gap-1.5 ml-auto shadow-sm disabled:opacity-70 transition-opacity"
                    >
                      {busyKey === `inspect-${t.subdomain_slug}` ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                      <span>Manage</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}