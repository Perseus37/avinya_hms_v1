import React from 'react';
import { Plus, Sparkles, Activity, RefreshCw } from 'lucide-react';
import RadialGauge from '../components/RadialGauge';
import { FLEET_REFRESH_INTERVAL_MS, fmtInr } from '../lib/constants';

export default function CommandCenter({ tenants, overview, analytics, setActiveView, inspectHospital, catalogs, syncAll, loading, lastSyncedAt, lastSyncError }) {
  // Extract dynamic module list from the backend catalog
  const { modules = [] } = catalogs;
  const activeTenantCount = tenants.filter((tenant) => tenant.is_active).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-black tracking-tight">Executive Cluster Telemetry & Commercial Intelligence</h1>
          <p className="text-xs theme-text-muted mt-0.5">Automatically refreshed SaaS ARR book, hospital GMV waterfall, radial capacity gauges, and module penetration.</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right text-[10px] theme-text-muted">
            <div className={lastSyncError ? "text-amber-500" : ""}>
              {lastSyncError ? `Refresh issue: ${lastSyncError}` : lastSyncedAt ? `Updated ${lastSyncedAt.toLocaleTimeString()}` : "Not synced yet"}
            </div>
            <button onClick={() => syncAll?.()} disabled={loading} className="inline-flex items-center gap-1 theme-accent-text disabled:opacity-50">
              <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} /> Refresh now
            </button>
          </div>
          <button onClick={() => setActiveView("provision-studio")} className="theme-accent-bg px-4 py-2.5 rounded-xl text-xs font-black uppercase tracking-wider flex items-center gap-2 shadow-lg">
            <Plus className="w-4 h-4" /> <span>Provision Hospital Tenant</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="theme-panel p-5 rounded-2xl flex items-center justify-between">
          <RadialGauge percentage={overview?.total_hospitals ? Math.round((overview.active_hospitals / overview.total_hospitals) * 100) : 100} label="Active Cloud Fleet" sublabel={`${overview?.active_hospitals ?? 0} / ${overview?.total_hospitals ?? 0} Nodes`} color="#10b981" />
        </div>
        <div className="theme-panel p-5 rounded-2xl flex items-center justify-between">
          <RadialGauge percentage={overview?.fleet_bed_occupancy_pct || 0} label="Cluster Bed Census" sublabel={`${overview?.occupied_beds ?? 0} / ${overview?.total_beds ?? 0} Beds`} color="#f59e0b" />
        </div>
        <div className="theme-panel p-5 rounded-2xl flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold uppercase theme-text-muted">Annual Recurring Revenue (ARR)</span>
            <Sparkles className="w-4 h-4 theme-accent-text" />
          </div>
          <div className="text-3xl font-black font-mono theme-accent-text mt-2">{overview ? fmtInr(overview.vendor_annual_license_arr_paise) : `₹0`}</div>
          <div className="text-[11px] font-mono theme-text-muted mt-1">
            Recurring contracts only; perpetual one-time value: {fmtInr(analytics?.one_time_license_value_paise ?? 0)}
          </div>
        </div>
        <div className="theme-panel p-5 rounded-2xl flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold uppercase theme-text-muted">Hospital Patient GMV</span>
            <Activity className="w-4 h-4 text-emerald-500" />
          </div>
          <div className="text-3xl font-black font-mono text-emerald-500 mt-2">{overview ? fmtInr(overview.fleet_gross_collected_revenue_paise) : `₹0`}</div>
          <div className="text-[11px] font-mono theme-text-muted mt-1">{overview?.global_patient_pointers_indexed ?? 0} MPI Pointers &bull; {overview?.encrypted_disaster_snapshots_count ?? 0} Backups</div>
        </div>
      </div>

      <section className="theme-panel rounded-2xl p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-xs font-bold uppercase tracking-wider theme-accent-text">Tenant Onboarding Funnel</h2>
            <p className="text-[11px] theme-text-muted mt-1">
              Snapshot at {analytics?.onboarding_funnel?.sampled_at ? new Date(analytics.onboarding_funnel.sampled_at).toLocaleTimeString() : "not available"}
            </p>
          </div>
          <div className="text-[11px] font-mono theme-text-muted">
            Median time to first admin login: {analytics?.onboarding_funnel?.median_seconds_to_first_admin_login == null
              ? "No sign-ins recorded"
              : `${Math.floor(analytics.onboarding_funnel.median_seconds_to_first_admin_login / 3600)}h ${Math.floor((analytics.onboarding_funnel.median_seconds_to_first_admin_login % 3600) / 60)}m`}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {(analytics?.onboarding_funnel?.stages || []).map((stage) => {
            const registeredCount = analytics?.onboarding_funnel?.stages?.find((item) => item.key === "registered")?.count || 0;
            const percentage = registeredCount > 0 ? Math.round((stage.count / registeredCount) * 100) : 0;
            return (
              <div key={stage.key} className="theme-elevated p-3 rounded-xl space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] theme-text-muted">{stage.label}</span>
                  <span className="text-xs font-mono font-black theme-accent-text">{stage.count}</span>
                </div>
                <div className="h-1.5 rounded-full theme-panel overflow-hidden">
                  <div className="h-full bg-emerald-500" style={{ width: `${percentage}%` }} />
                </div>
              </div>
            );
          })}
        </div>
        <div className="text-[10px] theme-text-muted">
          Recorded provisioning failures (all-time): {analytics?.onboarding_funnel?.provisioning_failures_recorded ?? 0}
          {" · "}Welcome-email failures: {analytics?.onboarding_funnel?.welcome_email_failures_recorded ?? 0}
          {" · "}Funnel excludes inactive tenants from database/configuration/login stages.
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-5 theme-panel rounded-2xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase tracking-wider theme-accent-text">SaaS ARR Share by Commercial Tier</h3>
            <span className="text-[11px] font-mono theme-text-muted">Auto-sync: {Math.round(FLEET_REFRESH_INTERVAL_MS / 1000)}s</span>
          </div>
          <div className="space-y-3">
            {Object.entries(analytics?.arr_by_pricing_model_paise || {}).map(([tier, paise]) => {
              const denominator = analytics?.annual_recurring_revenue_paise ?? overview?.vendor_annual_license_arr_paise ?? 0;
              const pct = denominator > 0 ? Math.min(100, Math.round((paise / denominator) * 100)) : 0;
              return (
                <div key={tier} className="space-y-1.5">
                  <div className="flex justify-between text-xs font-mono">
                    <span className="font-bold">{tier}</span>
                    <span>{fmtInr(paise)} ({pct}%)</span>
                  </div>
                  <div className="w-full h-2.5 rounded-full theme-elevated overflow-hidden">
                    <div className="h-full theme-accent-bg transition-all duration-700" style={{ width: `${pct > 0 ? Math.max(4, pct) : 0}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
          <div className="pt-4 border-t border-slate-500/20 space-y-2">
            <div className="text-xs font-bold uppercase tracking-wider theme-text-muted">Billing Cycle Mix</div>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(analytics?.tenants_by_billing_cycle || {}).map(([cycle, cnt]) => (
                <div key={cycle} className="theme-elevated p-2.5 rounded-xl flex items-center justify-between text-xs font-mono">
                  <span className="truncate text-[10px]">{cycle}</span>
                  <span className="font-black theme-accent-text">{cnt}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="lg:col-span-7 theme-panel rounded-2xl p-6 space-y-4">
          <div>
            <h3 className="text-xs font-bold uppercase tracking-wider theme-accent-text">Enterprise Module Penetration Matrix</h3>
            <p className="text-[11px] theme-text-muted">Active module entitlements across {analytics?.active_tenants ?? tenants.length} active hospitals</p>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {modules.map((m) => {
              const count = analytics?.module_adoption_counts?.[m.code] ?? 0;
              const total = Math.max(1, analytics?.active_tenants ?? activeTenantCount);
              const pct = Math.round((count / total) * 100);
              return (
                <div key={m.code} className="theme-elevated p-3 rounded-xl space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded theme-panel">Active</span>
                    <span className="text-xs font-mono font-black theme-accent-text">{count}/{total}</span>
                  </div>
                  <div className="text-xs font-bold truncate">{m.name}</div>
                  <div className="w-full h-1.5 rounded-full theme-panel overflow-hidden">
                    <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="theme-panel rounded-2xl p-6 space-y-4">
        <div>
          <h3 className="text-sm font-bold">Multi-Tenant Performance & Revenue Leaderboard</h3>
          <p className="text-xs theme-text-muted">Click any hospital row to open its full command drawer (Package, Quotas, Key Rotation, Snapshots & Circuit-Breaker).</p>
        </div>
        <div className="overflow-x-auto text-xs font-mono">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-slate-500/20 text-[11px] uppercase theme-text-muted">
                <th className="py-3 px-3">Hospital Tenant</th>
                <th className="py-3 px-3">Commercial Tier</th>
                <th className="py-3 px-3">Annualized ARR / One-time</th>
                <th className="py-3 px-3">MPI Patients</th>
                <th className="py-3 px-3">Bed Census</th>
                <th className="py-3 px-3">Settled GMV</th>
                <th className="py-3 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {(analytics?.hospital_leaderboard || tenants).map((h) => (
                <tr key={h.subdomain_slug} onClick={() => inspectHospital(h.subdomain_slug)} className="border-b border-slate-500/10 theme-row-hover cursor-pointer transition">
                  <td className="py-3 px-3 font-bold">
                    {h.hospital_name} <span className="theme-text-muted">({h.subdomain_slug})</span>
                  </td>
                  <td className="py-3 px-3 theme-accent-text">{h.pricing_model}</td>
                  <td className="py-3 px-3 text-emerald-500 font-bold">
                    {!h.is_active ? "Inactive" : h.billing_cycle === "ONE_TIME_PERPETUAL"
                      ? `${fmtInr(h.contract_fee_paise)} one-time`
                      : fmtInr(h.normalized_arr_paise ?? h.base_license_fee_paise)}
                  </td>
                  <td className="py-3 px-3">{h.patients_count ?? "--"}</td>
                  <td className="py-3 px-3">{h.occupied_beds ?? 0}/{h.total_beds ?? 0} ({h.occupancy_pct ?? 0}%)</td>
                  <td className="py-3 px-3 font-bold">{fmtInr(h.collected_gmv_paise ?? 0)}</td>
                  <td className="py-3 px-3 text-right">
                    <span className="px-2.5 py-1 rounded-lg theme-elevated text-[11px] font-bold">Manage &rarr;</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}