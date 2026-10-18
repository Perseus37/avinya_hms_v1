import React, { useState, useMemo, useEffect, useRef } from "react";
import { X, Loader2 } from "lucide-react";
import KeyGatedFeeOverride from "./KeyGatedFeeOverride";
import {
  calculateDynamicPrice,
  fmtInr,
  formatRupees,
  formatRupeesInput,
} from "../lib/constants";

export default function HospitalDrawer({ tenant, onClose, request, notify, masterKey, busyKey, refreshHospital, catalogs }) {
  const { modules = [], bundles = [] } = catalogs || {};
  const [drawerOutput, setDrawerOutput] = useState(null);
  const [snapshots, setSnapshots] = useState(tenant.snapshots || []);
  const [snapshotCreating, setSnapshotCreating] = useState(false);
  const [restoringFilename, setRestoringFilename] = useState(null);
  const outputTimerRef = useRef(null);
  const snapshotPollRef = useRef(false);
  const restorePollRef = useRef(false);

  useEffect(() => {
    setSnapshots(tenant.snapshots || []);
  }, [tenant.snapshots]);

  useEffect(() => () => {
    snapshotPollRef.current = false;
    restorePollRef.current = false;
    if (outputTimerRef.current) clearTimeout(outputTimerRef.current);
  }, []);

  const copyToClipboard = (text) => {
    if (!navigator.clipboard) {
      const textArea = document.createElement("textarea");
      textArea.value = text;
      document.body.appendChild(textArea);
      textArea.select();
      try { document.execCommand('copy'); notify("Copied JSON payload securely."); }
      catch (err) { notify("Failed to copy text.", "err"); }
      document.body.removeChild(textArea);
      return;
    }
    navigator.clipboard.writeText(text).then(() => notify("Copied JSON payload securely.")).catch(() => notify("Failed to copy.", "err"));
  };

  const getInitialModules = (tnt) => {
    const dbMods = tnt?.enabled_modules || {};
    const isAll = dbMods.all === true || Object.keys(dbMods).length === 0;
    const map = {};
    modules.forEach(m => { map[m.code] = isAll ? true : (dbMods[m.code] === true); });
    return map;
  };

  const detectCustomFeeState = (tnt) => {
    if (!tnt) return false;
    const dbFee = tnt.base_license_fee_paise || 0;
    const bundleObj = bundles.find(b => b.code === (tnt.pricing_model || "BUNDLE_TOTAL_HOSPITAL"));
    const stdFee = bundleObj ? bundleObj.bundle_annual_price_paise : 0;
    return dbFee !== stdFee;
  };

  const [drwForm, setDrwForm] = useState({
    pricing_model: tenant.pricing_model || "BUNDLE_TOTAL_HOSPITAL",
    billing_cycle: tenant.billing_cycle || "ANNUAL_LICENSE",
    federation_sharing_mode: tenant.hospital_policy?.federation_sharing_mode || "FULL_MESH",
    customFeeUnlocked: detectCustomFeeState(tenant),
    custom_fee_inr: formatRupeesInput((tenant.base_license_fee_paise || 0) / 100),
    max_beds: tenant.quota_limits?.max_beds ?? 500,
    max_users: tenant.quota_limits?.max_users ?? 200,
    sandbox_slug: `${tenant.subdomain_slug}-uat`.substring(0, 50),
    enabled_modules: getInitialModules(tenant),
    rot_jwt: true,
    rot_backup: true,
    rot_fed: true,
  });

  // FIX: Anchor strictly to subdomain_slug to prevent background API polling from wiping unsaved form states
  useEffect(() => {
    if (tenant) {
      setDrwForm(prev => ({
        ...prev,
        pricing_model: tenant.pricing_model || "BUNDLE_TOTAL_HOSPITAL",
        billing_cycle: tenant.billing_cycle || "ANNUAL_LICENSE",
        federation_sharing_mode: tenant.hospital_policy?.federation_sharing_mode || "FULL_MESH",
        customFeeUnlocked: detectCustomFeeState(tenant),
        custom_fee_inr: formatRupeesInput((tenant.base_license_fee_paise || 0) / 100),
        max_beds: tenant.quota_limits?.max_beds ?? 500,
        max_users: tenant.quota_limits?.max_users ?? 200,
        enabled_modules: getInitialModules(tenant),
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenant?.subdomain_slug]);

  const triggerOutputDestruct = (data) => {
    setDrawerOutput(data);
    if (outputTimerRef.current) clearTimeout(outputTimerRef.current);
    outputTimerRef.current = setTimeout(() => { setDrawerOutput(null); notify("Cleared sensitive data from screen."); }, 60000);
  };

  const drawerQuote = useMemo(() => {
    const enabledList = Object.keys(drwForm.enabled_modules).filter(k => drwForm.enabled_modules[k]);
    return calculateDynamicPrice(
      drwForm.pricing_model, enabledList, drwForm.billing_cycle,
      drwForm.max_beds, drwForm.customFeeUnlocked, drwForm.custom_fee_inr,
      modules, bundles
    );
  }, [drwForm, modules, bundles]);

  const handleDrawerPackageChange = (newModel) => {
    const bundleObj = bundles.find((b) => b.code === newModel);
    let nextMods = { ...drwForm.enabled_modules };
    if (bundleObj && newModel !== "CUSTOM_A_LA_CARTE") {
      nextMods = Object.fromEntries(modules.map((m) => [m.code, bundleObj.modules.includes(m.code)]));
    }
    setDrwForm((p) => ({ ...p, pricing_model: newModel, enabled_modules: nextMods, customFeeUnlocked: false, custom_fee_inr: "" }));
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex justify-end">
      <div className="w-full max-w-3xl theme-panel h-full overflow-y-auto no-scrollbar p-6 space-y-6 border-l">
        <div className="flex items-start justify-between border-b border-slate-500/20 pb-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-black">{tenant.hospital_name}</h2>
              <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${!tenant.is_active ? "bg-rose-500/20 text-rose-500" : tenant.maintenance_mode ? "bg-amber-500/20 text-amber-500" : "bg-emerald-500/20 text-emerald-500"}`}>
                {!tenant.is_active ? "SUSPENDED" : tenant.maintenance_mode ? "MAINTENANCE" : "ACTIVE"}
              </span>
            </div>
            <p className="text-xs font-mono theme-text-muted mt-1">
              Subdomain: {tenant.subdomain_slug} &bull; Branch: {tenant.branch_code} &bull; DB: {tenant.db_name}
            </p>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg theme-elevated hover:opacity-80">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-bold uppercase tracking-wider theme-accent-text">1. Live Hospital Data-Plane Telemetry</div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
            <div className="theme-elevated p-3.5 rounded-xl">
              <div className="text-[10px] theme-text-muted uppercase">Staff Users</div>
              <div className="text-lg font-black mt-0.5">{tenant.metrics?.users_count ?? 0} / {drwForm.max_users}</div>
            </div>
            <div className="theme-elevated p-3.5 rounded-xl">
              <div className="text-[10px] theme-text-muted uppercase">Beds (Occ/Cap)</div>
              <div className="text-lg font-black theme-accent-text mt-0.5">{tenant.metrics?.occupied_beds_count ?? 0} / {tenant.metrics?.total_beds_count ?? 0}</div>
            </div>
            <div className="theme-elevated p-3.5 rounded-xl">
              <div className="text-[10px] theme-text-muted uppercase">MPI Patients</div>
              <div className="text-lg font-black mt-0.5">{tenant.metrics?.patients_count ?? 0}</div>
            </div>
            <div className="theme-elevated p-3.5 rounded-xl">
              <div className="text-[10px] theme-text-muted uppercase">Active Encounters</div>
              <div className="text-lg font-black text-emerald-500 mt-0.5">{tenant.metrics?.active_encounters_count ?? 0}</div>
            </div>
            <div className="theme-elevated p-3.5 rounded-xl col-span-2">
              <div className="text-[10px] theme-text-muted uppercase">Hospital Patient Gross Billed</div>
              <div className="text-lg font-black mt-0.5">{fmtInr(tenant.metrics?.gross_revenue_paise)}</div>
            </div>
            <div className="theme-elevated p-3.5 rounded-xl col-span-2">
              <div className="text-[10px] theme-text-muted uppercase">Hospital Patient Collected (Paid)</div>
              <div className="text-lg font-black text-emerald-500 mt-0.5">{fmtInr(tenant.metrics?.collected_revenue_paise)}</div>
            </div>
          </div>
        </div>

        <div className="theme-elevated p-5 rounded-2xl space-y-4 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><span className="font-bold uppercase tracking-wider theme-accent-text">2. Commercial Package, Quotas & Sovereignty</span></div>
            <button
              onClick={async () => {
                try {
                  const overridePaise = (drwForm.customFeeUnlocked && drwForm.custom_fee_inr !== "") ? Math.round(Number(drwForm.custom_fee_inr) * 100) : null;
                  await request(`/api/tenants/${tenant.subdomain_slug}/entitlements`, {
                    method: "PATCH",
                    body: JSON.stringify({
                      pricing_model: drwForm.pricing_model,
                      billing_cycle: drwForm.billing_cycle,
                      base_license_fee_paise: overridePaise,
                      enabled_modules: drwForm.enabled_modules,
                      federation_sharing_mode: drwForm.federation_sharing_mode,
                      // FIX: Safe Number casts during payload generation, not during typing
                      quota_limits: { max_beds: Number(drwForm.max_beds) || 0, max_users: Number(drwForm.max_users) || 0 },
                      maintenance_mode: tenant.maintenance_mode,
                      maintenance_notice: tenant.maintenance_notice,
                    }),
                  }, "save-ent");
                  notify(`Updated package (${drwForm.pricing_model}) & rules!`);
                  await refreshHospital(tenant.subdomain_slug);
                } catch (err) { notify(err.message, "err"); }
              }}
              disabled={busyKey === "save-ent"}
              className="theme-accent-bg px-4 py-2 rounded-xl font-black flex items-center gap-1.5 shadow"
            >
              {busyKey === "save-ent" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>Save Configuration</span>
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 border-b border-slate-500/20 pb-4">
            <div className="theme-panel p-3 rounded-lg">
              <div className="text-[10px] uppercase theme-text-muted">Provisioning Blueprint</div>
              <div className="font-bold mt-1">{tenant.provisioning_config?.blueprint_archetype || "Not recorded"}</div>
            </div>
            <div className="theme-panel p-3 rounded-lg">
              <div className="text-[10px] uppercase theme-text-muted">Initial Data Seeds</div>
              <div className="font-bold mt-1">
                {tenant.provisioning_config
                  ? ["seed_icd10", "seed_allergies", "seed_complaints"]
                      .filter((seed) => tenant.provisioning_config[seed])
                      .map((seed) => seed.replace("seed_", "").toUpperCase())
                      .join(", ") || "None"
                  : "Not recorded"}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-5 gap-3 border-b border-slate-500/20 pb-4">
            <div className="sm:col-span-2">
              <label className="block text-[10px] uppercase theme-text-muted mb-1">Pricing Model</label>
              <select value={drwForm.pricing_model} onChange={(e) => handleDrawerPackageChange(e.target.value)} className="w-full theme-input rounded-lg p-2">
                {bundles.map((b) => (<option key={b.code} value={b.code}>{b.code}</option>))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className="block text-[10px] uppercase theme-text-muted mb-1">Billing Cycle</label>
              <select value={drwForm.billing_cycle} onChange={(e) => setDrwForm((p) => ({ ...p, billing_cycle: e.target.value, customFeeUnlocked: false, custom_fee_inr: "" }))} className="w-full theme-input rounded-lg p-2">
                <option value="ANNUAL_LICENSE">ANNUAL_LICENSE</option>
                <option value="MONTHLY_SUBSCRIPTION">MONTHLY_SUBSCRIPTION</option>
                <option value="PER_ACTIVE_BED_MONTHLY">PER_ACTIVE_BED_MONTHLY</option>
                <option value="ONE_TIME_PERPETUAL">ONE_TIME_PERPETUAL</option>
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className="block text-[10px] uppercase theme-text-muted mb-1">Federation Sovereignty</label>
              <select value={drwForm.federation_sharing_mode} onChange={(e) => setDrwForm((p) => ({ ...p, federation_sharing_mode: e.target.value }))} className="w-full theme-input rounded-lg p-2">
                <option value="FULL_MESH">FULL_MESH</option>
                <option value="CORPORATE_GROUP_ONLY">CORPORATE_GROUP_ONLY</option>
                <option value="EMERGENCY_ALLERGIES_ONLY">EMERGENCY_ALLERGIES_ONLY</option>
                <option value="ISOLATED_SILO">ISOLATED_SILO</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] uppercase theme-text-muted mb-1">Max Beds Quota</label>
              <input type="number" value={drwForm.max_beds} onChange={(e) => setDrwForm((p) => ({ ...p, max_beds: e.target.value }))} className="w-full theme-input rounded-lg p-2 font-mono" />
            </div>
            <div>
              <label className="block text-[10px] uppercase theme-text-muted mb-1">Max Users Quota</label>
              <input type="number" value={drwForm.max_users} onChange={(e) => setDrwForm((p) => ({ ...p, max_users: e.target.value }))} className="w-full theme-input rounded-lg p-2 font-mono" />
            </div>
          </div>

          <KeyGatedFeeOverride
            autoFeeInr={drawerQuote.autoCycleFee} suffix={drawerQuote.suffix}
            isUnlocked={drwForm.customFeeUnlocked} onUnlockChange={(v) => setDrwForm((p) => ({ ...p, customFeeUnlocked: v }))}
            customFeeInr={drwForm.custom_fee_inr} onCustomFeeChange={(val) => setDrwForm((p) => ({ ...p, custom_fee_inr: val }))}
            masterKey={masterKey} notify={notify}
          />

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-2">
            {modules.map((m) => (
              <label key={m.code} className="flex items-center gap-1.5 theme-panel p-2 rounded-lg cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!drwForm.enabled_modules[m.code]}
                  onChange={(e) => setDrwForm((p) => ({
                    ...p, pricing_model: "CUSTOM_A_LA_CARTE", customFeeUnlocked: false, custom_fee_inr: "",
                    enabled_modules: { ...p.enabled_modules, [m.code]: e.target.checked },
                  }))}
                />
                <span className="text-[11px]">{m.name}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="theme-elevated p-5 rounded-2xl space-y-3 text-xs no-scrollbar">
          <div className="flex items-center justify-between">
            <span className="font-bold uppercase tracking-wider text-emerald-500">3. AES-256 Snapshot Vault ({snapshots.length})</span>
            <button
              onClick={async () => {
                const existingFiles = new Set(snapshots.map((snapshot) => snapshot.filename));
                snapshotPollRef.current = true;
                setSnapshotCreating(true);
                try {
                  const auditBefore = await request(
                    `/api/fleet/audit-logs?limit=10&target=${encodeURIComponent(tenant.subdomain_slug)}`,
                  );
                  const latestAuditId = Math.max(0, ...(auditBefore.items || []).map((item) => item.id));
                  const d = await request(`/api/tenants/${tenant.subdomain_slug}/backups/trigger`, { method: "POST" }, "snap-trig");
                  notify(d.message);
                  for (let attempt = 0; attempt < 30 && snapshotPollRef.current; attempt += 1) {
                    await new Promise((resolve) => setTimeout(resolve, 2000));
                    if (!snapshotPollRef.current) return;
                    const result = await request(`/api/tenants/${tenant.subdomain_slug}/backups`);
                    const latestSnapshots = result.snapshots || [];
                    setSnapshots(latestSnapshots);
                    if (latestSnapshots.some((snapshot) => !existingFiles.has(snapshot.filename))) {
                      notify("Encrypted snapshot is ready.");
                      return;
                    }
                    const audit = await request(
                      `/api/fleet/audit-logs?limit=10&target=${encodeURIComponent(tenant.subdomain_slug)}`,
                    );
                    const failure = (audit.items || []).find(
                      (item) => item.id > latestAuditId && item.action === "BACKUP_FAILED",
                    );
                    if (failure) {
                      notify("Snapshot creation failed. See the Forensic Audit Ledger and service logs.", "err");
                      return;
                    }
                  }
                  if (snapshotPollRef.current) {
                    notify("Snapshot is still processing. Refresh the vault in a moment.", "err");
                  }
                } catch (err) { notify(err.message, "err"); }
                finally {
                  snapshotPollRef.current = false;
                  setSnapshotCreating(false);
                }
              }}
              disabled={busyKey === "snap-trig" || snapshotCreating}
              className="bg-emerald-600 hover:bg-emerald-500 text-white px-3.5 py-1.5 rounded-lg font-bold flex items-center gap-1.5 transition-colors"
            >
              {(busyKey === "snap-trig" || snapshotCreating) && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>+ Take Snapshot Now</span>
            </button>
          </div>
          <div className="space-y-2 max-h-40 overflow-y-auto font-mono pr-2">
            {snapshots.map((s) => (
              <div key={s.filename} className="theme-panel p-2.5 rounded-xl flex items-center justify-between">
                <div>
                  <div className="text-[11px] font-bold">{s.filename}</div>
                  <div className="text-[10px] theme-text-muted">{(s.size_bytes / 1024).toFixed(1)} KB &bull; {new Date(s.created_at * 1000).toLocaleString()}</div>
                </div>
                <button
                  onClick={async () => {
                    if (!window.confirm(`Restore ${s.filename} into ${tenant.db_name}?`)) return;
                    restorePollRef.current = true;
                    setRestoringFilename(s.filename);
                    try {
                      const auditBefore = await request(
                        `/api/fleet/audit-logs?limit=10&target=${encodeURIComponent(tenant.subdomain_slug)}`,
                      );
                      const latestAuditId = Math.max(0, ...(auditBefore.items || []).map((item) => item.id));
                      const d = await request(`/api/tenants/${tenant.subdomain_slug}/backups/${encodeURIComponent(s.filename)}/restore`, { method: "POST" }, `rest-${s.filename}`);
                      notify(d.message);
                      for (let attempt = 0; attempt < 60 && restorePollRef.current; attempt += 1) {
                        await new Promise((resolve) => setTimeout(resolve, 5000));
                        if (!restorePollRef.current) return;
                        const audit = await request(
                          `/api/fleet/audit-logs?limit=10&target=${encodeURIComponent(tenant.subdomain_slug)}`,
                        );
                        const outcome = (audit.items || []).find(
                          (item) => item.id > latestAuditId && ["RESTORE_COMPLETED", "RESTORE_FAILED"].includes(item.action),
                        );
                        if (outcome) {
                          if (outcome.action === "RESTORE_COMPLETED") {
                            notify("Snapshot restore completed.");
                            await refreshHospital(tenant.subdomain_slug);
                          } else {
                            notify("Snapshot restore failed. See the Forensic Audit Ledger and service logs.", "err");
                          }
                          return;
                        }
                      }
                      if (restorePollRef.current) notify("Restore is still processing. Check the Forensic Audit Ledger shortly.");
                    } catch (err) { notify(err.message, "err"); }
                    finally {
                      restorePollRef.current = false;
                      setRestoringFilename(null);
                    }
                  }}
                  disabled={Boolean(restoringFilename)}
                  className="px-3 py-1 rounded-lg bg-amber-500/20 text-amber-500 hover:bg-amber-500/30 transition-colors font-bold text-[10px]"
                >{restoringFilename === s.filename ? "Restoring..." : restoringFilename ? "Restore queued" : "Restore into DB"}</button>
              </div>
            ))}
            {snapshots.length === 0 && (
               <div className="text-slate-500 text-center py-2">No encrypted .dump.enc snapshots found for this hospital yet.</div>
            )}
          </div>
        </div>

        <div className="theme-elevated p-5 rounded-2xl space-y-4 text-xs">
          <div className="font-bold uppercase tracking-wider text-rose-500">4. Zero-Trust Action Deck</div>

          <div className="theme-panel p-3.5 rounded-xl space-y-2.5 border border-slate-500/10">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-bold">Granular Cryptographic Key Rotation</div>
              <button
                onClick={async () => {
                  if (!window.confirm(`Rotate selected keys for '${tenant.subdomain_slug}'? Active staff sessions will require re-login.`)) return;
                  try {
                    const d = await request(`/api/tenants/${tenant.subdomain_slug}/rotate-keys`, {
                      method: "POST",
                      body: JSON.stringify({ rotate_jwt_secret: drwForm.rot_jwt, rotate_backup_key: drwForm.rot_backup, rotate_federation_key: drwForm.rot_fed }),
                    }, "rot-keys");
                    triggerOutputDestruct(d);
                    await refreshHospital(tenant.subdomain_slug);
                  } catch (err) { notify(err.message, "err"); }
                }}
                disabled={busyKey === "rot-keys"}
                className="theme-accent-bg px-3.5 py-1.5 rounded-lg font-bold flex items-center gap-1.5 transition-colors"
              >
                {busyKey === "rot-keys" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                <span>Rotate Selected Keys</span>
              </button>
            </div>
            <div className="flex flex-wrap gap-4 text-[11px]">
              <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={drwForm.rot_jwt} onChange={(e) => setDrwForm(p => ({ ...p, rot_jwt: e.target.checked }))} /> <span>JWT Secret</span></label>
              <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={drwForm.rot_backup} onChange={(e) => setDrwForm(p => ({ ...p, rot_backup: e.target.checked }))} /> <span>Backup Key</span></label>
              <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={drwForm.rot_fed} onChange={(e) => setDrwForm(p => ({ ...p, rot_fed: e.target.checked }))} /> <span>Federation API Key</span></label>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button
              onClick={async () => {
                try {
                  const d = await request(`/api/tenants/${tenant.subdomain_slug}/reset-admin-password`, { method: "POST" }, "reset-pwd");
                  triggerOutputDestruct(d);
                } catch (err) { notify(err.message, "err"); }
              }}
              className="theme-panel p-3.5 rounded-xl text-left hover:opacity-90 transition-opacity"
            >
              <div className="font-bold text-amber-500">Break-Glass Password Reset</div>
              <div className="text-[10px] theme-text-muted mt-0.5">Unlocks superuser account and generates 1-time recovery password.</div>
            </button>

            <div className="theme-panel p-3.5 rounded-xl flex items-center justify-between gap-2">
              <div><div className="font-bold">Maintenance Freeze</div><div className="text-[10px] theme-text-muted">Status: {tenant.maintenance_mode ? "FROZEN" : "CLEAR"}</div></div>
              <button
                onClick={async () => {
                  try {
                    await request(`/api/tenants/${tenant.subdomain_slug}/entitlements`, {
                      method: "PATCH",
                      body: JSON.stringify({
                        pricing_model: tenant.pricing_model || "BUNDLE_TOTAL_HOSPITAL",
                        billing_cycle: tenant.billing_cycle || "ANNUAL_LICENSE",
                        base_license_fee_paise: tenant.base_license_fee_paise,
                        enabled_modules: tenant.enabled_modules || {},
                        quota_limits: tenant.quota_limits,
                        maintenance_mode: !tenant.maintenance_mode,
                        maintenance_notice: "Emergency platform freeze.",
                      }),
                    }, "save-maint");
                    notify(`Maintenance mode toggled.`);
                    await refreshHospital(tenant.subdomain_slug);
                  } catch (err) { notify(err.message, "err"); }
                }}
                disabled={busyKey === "save-maint"}
                className={`px-3 py-1.5 rounded-lg font-mono font-bold text-[11px] transition-colors border ${tenant.maintenance_mode ? "bg-amber-500/20 text-amber-500 border-amber-500/40" : "theme-elevated"}`}
              >{busyKey === "save-maint" ? "..." : tenant.maintenance_mode ? "Disable Freeze" : "Enable Freeze"}</button>
            </div>

            <div className="theme-panel p-3.5 rounded-xl flex items-center gap-2">
              <input type="text" maxLength={50} value={drwForm.sandbox_slug} onChange={(e) => setDrwForm((p) => ({ ...p, sandbox_slug: e.target.value }))} placeholder="sandbox-slug" className="theme-input rounded-lg px-2.5 py-1.5 font-mono text-xs flex-1" />
              <button
                onClick={async () => {
                  if(!drwForm.sandbox_slug) return;
                  try {
                    const d = await request(`/api/tenants/${tenant.subdomain_slug}/clone-sandbox`, {
                      method: "POST", body: JSON.stringify({ sandbox_subdomain_slug: drwForm.sandbox_slug.trim().toLowerCase() }),
                    }, "clone-sbx");
                    triggerOutputDestruct(d);
                    await refreshHospital(tenant.subdomain_slug);
                  } catch (err) { notify(err.message, "err"); }
                }}
                disabled={busyKey === "clone-sbx"}
                className="theme-accent-bg px-3 py-1.5 rounded-lg font-bold"
              >{busyKey === "clone-sbx" ? "..." : "Clone UAT"}</button>
            </div>

            <button
              onClick={async () => {
                try {
                  const d = await request(`/api/tenants/${tenant.subdomain_slug}/status`, {
                    method: "PATCH", body: JSON.stringify({ is_active: !tenant.is_active }),
                  }, "toggle-cb");
                  notify(d.message);
                  await refreshHospital(tenant.subdomain_slug);
                } catch (err) { notify(err.message, "err"); }
              }}
              disabled={busyKey === "toggle-cb"}
              className={`p-3.5 rounded-xl border text-left font-bold transition-colors ${tenant.is_active ? "bg-rose-500/15 hover:bg-rose-500/25 border-rose-500/40 text-rose-500" : "bg-emerald-500/15 hover:bg-emerald-500/25 border-emerald-500/40 text-emerald-500"}`}
            >
              <div>{busyKey === "toggle-cb" ? "Executing..." : tenant.is_active ? "Suspend Tenant (Circuit-Breaker)" : "Reactivate Hospital Tenant"}</div>
              <div className="text-[10px] opacity-80 mt-0.5">{tenant.is_active ? "Blocks all API traffic with HTTP 403." : "Restores normal API access."}</div>
            </button>
          </div>

          {drawerOutput && (
            <div className="theme-panel p-4 rounded-xl border-2 border-emerald-500/40 space-y-2 relative">
              <div className="flex items-center justify-between">
                <span className="font-bold text-emerald-500 uppercase text-[11px]">Security Vault (Auto-clears in 60s)</span>
                <div className="flex gap-2">
                  <button onClick={() => setDrawerOutput(null)} className="px-2 py-0.5 rounded text-slate-400 hover:text-white text-[10px] font-bold transition-colors">Clear</button>
                  <button onClick={() => copyToClipboard(JSON.stringify(drawerOutput, null, 2))} className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 text-[10px] font-bold transition-colors">Copy JSON</button>
                </div>
              </div>
              <pre className="font-mono text-[11px] overflow-x-auto text-slate-200 selection:bg-emerald-500/30">{JSON.stringify(drawerOutput, null, 2)}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}