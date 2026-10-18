import React, { useState, useMemo, useEffect } from 'react';
import { Layers, Loader2, Zap } from 'lucide-react';
import KeyGatedFeeOverride from '../components/KeyGatedFeeOverride';
import { INR, calculateDynamicPrice, fmtInr, formatRupees } from '../lib/constants';

export default function ProvisioningStudio({ request, notify, syncAll, busyKey, catalogs }) {
  const { modules = [], bundles = [], blueprints = [] } = catalogs || {};
  const [wizardStep, setWizardStep] = useState(1);
  const [provResult, setProvResult] = useState(null);
  const [isInitialized, setIsInitialized] = useState(false);

  const flagshipBundle = bundles.find((bundle) => bundle.code === "BUNDLE_TOTAL_HOSPITAL") || bundles[0];
  const flagshipBlueprint = blueprints.find((blueprint) => blueprint.key === "TIER_1_QUATERNARY_500_BED") || blueprints[0];
  const defaultBundle = flagshipBundle?.code || "BUNDLE_TOTAL_HOSPITAL";
  const defaultModules = flagshipBundle?.modules || [];
  const defaultBlueprint = flagshipBlueprint?.key || "TIER_1_QUATERNARY_500_BED";

  const [provForm, setProvForm] = useState({
    hospital_name: "", subdomain_slug: "", branch_code: "", admin_email: "",
    blueprint_archetype: defaultBlueprint,
    pricing_model: defaultBundle,
    billing_cycle: "ANNUAL_LICENSE",
    customFeeUnlocked: false, custom_fee_inr: "",
    quota_max_beds: 500, quota_max_users: 200, federation_sharing_mode: "FULL_MESH",
    seed_icd10: true, seed_allergies: true, seed_complaints: true,
    selected_modules: defaultModules,
  });

  // FIX: Track initialization independently to prevent infinite render loops on 0-module selections
  useEffect(() => {
    if (bundles.length > 0 && !isInitialized) {
      setProvForm(p => ({
        ...p,
        pricing_model: flagshipBundle.code,
        selected_modules: flagshipBundle.modules,
        blueprint_archetype: flagshipBlueprint?.key || p.blueprint_archetype
      }));
      setIsInitialized(true);
    }
  }, [bundles, blueprints, flagshipBundle, flagshipBlueprint, isInitialized]);

  const quote = useMemo(() => calculateDynamicPrice(
    provForm.pricing_model, provForm.selected_modules, provForm.billing_cycle,
    provForm.quota_max_beds, provForm.customFeeUnlocked, provForm.custom_fee_inr,
    modules, bundles
  ), [provForm, modules, bundles]);

  const activeBlueprintObj = useMemo(() =>
    blueprints.find((b) => b.key === provForm.blueprint_archetype) || { beds: 0, floors_count: 0, theatres_count: 0 }
  , [provForm.blueprint_archetype, blueprints]);
  const effectiveBedQuota = provForm.selected_modules.includes("ipd_adt")
    ? Number(provForm.quota_max_beds) || 0
    : 0;

  const executeProvisioning = async () => {
    try {
      // FIX: Explicitly allow 0 for 100% pilot discounts while blocking empty strings
      const customInr = provForm.customFeeUnlocked && provForm.custom_fee_inr !== "" ? Number(provForm.custom_fee_inr) : null;

      const data = await request("/api/provision", {
        method: "POST",
        body: JSON.stringify({
          hospital_name: provForm.hospital_name.trim(),
          subdomain_slug: provForm.subdomain_slug.trim().toLowerCase(),
          branch_code: provForm.branch_code.trim().toUpperCase(),
          admin_email: provForm.admin_email.trim().toLowerCase(),
          pricing_model: provForm.pricing_model,
          billing_cycle: provForm.billing_cycle,
          selected_modules: provForm.selected_modules,
          base_license_fee_paise: customInr !== null ? Math.round(customInr * 100) : null,
          quota_max_beds: Number(provForm.quota_max_beds) || 0,
          quota_max_users: Number(provForm.quota_max_users) || 200,
          blueprint_archetype: provForm.blueprint_archetype,
          federation_sharing_mode: provForm.federation_sharing_mode,
          seed_icd10: provForm.seed_icd10,
          seed_allergies: provForm.seed_allergies,
          seed_complaints: provForm.seed_complaints,
        }),
      }, "provision-exec");

      setProvResult(data);
      notify(`Provisioned ${data.hospital_name} (${data.database_name})!`);
      await syncAll(true);
    } catch (err) {
      notify(err.message, "err");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-black tracking-tight">Interactive Hospital Provisioning Studio</h1>
        <p className="text-xs theme-text-muted">Step-by-step cloud database creation, visual spatial blueprint preview, and key-gated fee overrides.</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { step: 1, title: "1. Identity & Sovereignty" },
          { step: 2, title: "2. Visual Blueprint" },
          { step: 3, title: "3. Bundle & Quotas" },
          { step: 4, title: "4. Launch Vault" },
        ].map((s) => (
          <button
            key={s.step} onClick={() => setWizardStep(s.step)}
            className={`p-3.5 rounded-2xl text-left font-bold text-xs border transition ${wizardStep === s.step ? "theme-accent-bg shadow-lg border-transparent" : "theme-panel opacity-75 hover:opacity-100"}`}
          >
            {s.title}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-7 theme-panel rounded-2xl p-6 space-y-5">

          {/* STEP 1: IDENTITY */}
          {wizardStep === 1 && (
            <div className="space-y-4 text-xs">
              <h3 className="text-base font-black">Step 1: Hospital Legal Identity & Sovereignty</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="sm:col-span-2">
                  <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">Hospital Legal Name</label>
                  <input
                    type="text" value={provForm.hospital_name}
                    onChange={(e) => {
                      const val = e.target.value;
                      const slug = val.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 24);
                      const pref = (val.replace(/[^A-Za-z]/g, "").toUpperCase() + "HMS").slice(0, 6);
                      setProvForm((p) => ({ ...p, hospital_name: val, subdomain_slug: slug, branch_code: val ? `${pref}-HYD-01` : "" }));
                    }}
                    placeholder="e.g. AIG Hospitals Gachibowli" className="w-full theme-input rounded-xl px-4 py-3 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">Tenant Subdomain Slug</label>
                  <input type="text" value={provForm.subdomain_slug} onChange={(e) => setProvForm(p => ({ ...p, subdomain_slug: e.target.value }))} placeholder="aig-gachibowli" className="w-full theme-input rounded-xl px-3.5 py-2.5 font-mono" />
                </div>
                <div>
                  <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">Branch Code</label>
                  <input type="text" value={provForm.branch_code} onChange={(e) => setProvForm(p => ({ ...p, branch_code: e.target.value }))} placeholder="AIGHMS-HYD-01" className="w-full theme-input rounded-xl px-3.5 py-2.5 font-mono uppercase" />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">Superuser Admin Email</label>
                  <input type="email" value={provForm.admin_email} onChange={(e) => setProvForm(p => ({ ...p, admin_email: e.target.value }))} placeholder="cto@aighospitals.com" className="w-full theme-input rounded-xl px-3.5 py-2.5" />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">Day-0 Federation Sovereignty Mode</label>
                  <select value={provForm.federation_sharing_mode} onChange={(e) => setProvForm(p => ({ ...p, federation_sharing_mode: e.target.value }))} className="w-full theme-input rounded-xl px-3.5 py-2.5">
                    <option value="FULL_MESH">FULL_MESH (Share Demographics & Allergies Across Network)</option>
                    <option value="CORPORATE_GROUP_ONLY">CORPORATE_GROUP_ONLY (Share Only With Sister Branches)</option>
                    <option value="EMERGENCY_ALLERGIES_ONLY">EMERGENCY_ALLERGIES_ONLY (Redacts Residential Address)</option>
                    <option value="ISOLATED_SILO">ISOLATED_SILO (100% Private / Zero Outbound Sharing)</option>
                  </select>
                </div>
              </div>
              <div className="flex justify-end pt-3">
                <button
                  onClick={() => {
                    if (!provForm.hospital_name || !provForm.subdomain_slug || !provForm.branch_code || !provForm.admin_email) return notify("Please fill in all Identity fields.", "err");
                    if (!/\S+@\S+\.\S+/.test(provForm.admin_email)) return notify("Please enter a valid Email.", "err");
                    setWizardStep(2);
                  }}
                  className="theme-accent-bg px-5 py-2.5 rounded-xl text-xs font-black shadow-lg"
                >Next: Select Spatial Blueprint &rarr;</button>
              </div>
            </div>
          )}

          {/* STEP 2: BLUEPRINT */}
          {wizardStep === 2 && (
            <div className="space-y-4">
              <h3 className="text-base font-black">Step 2: Choose Starter Spatial Topology</h3>
              <div className="grid grid-cols-1 gap-3">
                {blueprints.length === 0 && (
                  <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs">
                    <div className="font-bold text-amber-500">Blueprint catalog is not loaded.</div>
                    <p className="theme-text-muted mt-1">
                      Sign in with a named operator, then click Sync Cluster in the sidebar. Catalogs are seeded by API migration startup; no separate seed command is normally needed.
                    </p>
                  </div>
                )}
                {blueprints.map((bp) => (
                  <div
                    key={bp.key} onClick={() => setProvForm((p) => ({ ...p, blueprint_archetype: bp.key }))}
                    className={`p-4 rounded-2xl cursor-pointer transition flex items-center justify-between ${provForm.blueprint_archetype === bp.key ? "theme-accent-bg shadow-lg border-transparent" : "theme-elevated border border-slate-500/20"}`}
                  >
                    <div>
                      <div className="font-black text-sm">{bp.name}</div>
                      <div className="text-xs opacity-85 mt-0.5">{bp.description}</div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex justify-between pt-3">
                <button onClick={() => setWizardStep(1)} className="theme-elevated border border-slate-500/20 px-4 py-2 rounded-xl text-xs font-bold">&larr; Back</button>
                <button onClick={() => setWizardStep(3)} className="theme-accent-bg px-5 py-2.5 rounded-xl text-xs font-black shadow-lg">Next: Commercial Package &rarr;</button>
              </div>
            </div>
          )}

          {/* STEP 3: QUOTAS & PRICING */}
          {wizardStep === 3 && (
            <div className="space-y-4 text-xs">
              <h3 className="text-base font-black">Step 3: Commercial Bundle & Resource Quotas</h3>

              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {bundles.map((b) => (
                  <button
                    type="button" key={b.code}
                    onClick={() => setProvForm((p) => ({ ...p, pricing_model: b.code, selected_modules: b.code === "CUSTOM_A_LA_CARTE" ? p.selected_modules : b.modules, customFeeUnlocked: false }))}
                    className={`p-3 rounded-xl text-left transition border ${provForm.pricing_model === b.code ? "theme-accent-bg shadow-md border-transparent" : "theme-elevated border-slate-500/20"}`}
                  >
                    <div className="font-bold">{b.name}</div>
                    <div className="text-[10px] opacity-80 mt-0.5">{b.code.replace("BUNDLE_", "")}</div>
                  </button>
                ))}
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 theme-elevated border border-slate-500/20 p-3.5 rounded-2xl">
                {modules.map((m) => (
                  <label key={m.code} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox" checked={provForm.selected_modules.includes(m.code)}
                      onChange={(e) => {
                        const next = e.target.checked ? [...provForm.selected_modules, m.code] : provForm.selected_modules.filter((x) => x !== m.code);
                        setProvForm((p) => ({ ...p, pricing_model: "CUSTOM_A_LA_CARTE", selected_modules: next, customFeeUnlocked: false }));
                      }}
                    />
                    <span>{m.name}</span>
                  </label>
                ))}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-[10px] font-bold uppercase theme-text-muted mb-1">Billing Cycle</label>
                  <select value={provForm.billing_cycle} onChange={(e) => setProvForm(p => ({ ...p, billing_cycle: e.target.value }))} className="w-full theme-input rounded-xl p-2.5">
                    <option value="ANNUAL_LICENSE">Annual License</option>
                    <option value="MONTHLY_SUBSCRIPTION">Monthly Installment</option>
                    <option value="PER_ACTIVE_BED_MONTHLY">Per-Bed Monthly</option>
                    <option value="ONE_TIME_PERPETUAL">Perpetual Buyout</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase theme-text-muted mb-1">Max Beds Quota</label>
                  <input type="number" value={provForm.quota_max_beds} onChange={(e) => setProvForm(p => ({ ...p, quota_max_beds: e.target.value }))} className="w-full theme-input rounded-xl p-2.5 font-mono" />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase theme-text-muted mb-1">Max Staff Users</label>
                  <input type="number" value={provForm.quota_max_users} onChange={(e) => setProvForm(p => ({ ...p, quota_max_users: e.target.value }))} className="w-full theme-input rounded-xl p-2.5 font-mono" />
                </div>
              </div>

              <KeyGatedFeeOverride
                autoFeeInr={quote.autoCycleFee} suffix={quote.suffix}
                isUnlocked={provForm.customFeeUnlocked} onUnlockChange={(v) => setProvForm(p => ({ ...p, customFeeUnlocked: v }))}
                customFeeInr={provForm.custom_fee_inr} onCustomFeeChange={(val) => setProvForm(p => ({ ...p, custom_fee_inr: val }))}
                notify={notify}
              />

              <div className="flex justify-between pt-3">
                <button onClick={() => setWizardStep(2)} className="theme-elevated border border-slate-500/20 px-4 py-2 rounded-xl text-xs font-bold">&larr; Back</button>
                <button onClick={() => setWizardStep(4)} className="theme-accent-bg px-5 py-2.5 rounded-xl text-xs font-black shadow-lg">Next: Review & Execute &rarr;</button>
              </div>
            </div>
          )}

          {/* STEP 4: CONFIRMATION & VAULT */}
          {wizardStep === 4 && (
            <div className="space-y-4 text-xs">
              <h3 className="text-base font-black">Step 4: Confirm Zero-Touch Cloud Provisioning</h3>

              <div className="theme-elevated border border-slate-500/20 p-4 rounded-2xl grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono">
                <div><span className="theme-text-muted">Hospital:</span> <b>{provForm.hospital_name || "Unset"}</b></div>
                <div><span className="theme-text-muted">Target DB:</span> <b className="theme-accent-text">hms_{provForm.subdomain_slug || "tenant"}</b></div>
                <div><span className="theme-text-muted">Package:</span> <b>{provForm.pricing_model}</b></div>
                <div><span className="theme-text-muted">Billing:</span> <b>{provForm.billing_cycle}</b></div>
                <div><span className="theme-text-muted">Contract Fee:</span> <b className="text-emerald-500">{INR}{formatRupees(quote.finalFeeInr)} {quote.suffix}</b></div>
                <div><span className="theme-text-muted">Blueprint:</span> <b>{activeBlueprintObj.name || provForm.blueprint_archetype}</b></div>
                <div><span className="theme-text-muted">Quotas:</span> <b>{effectiveBedQuota} beds / {Number(provForm.quota_max_users) || 200} users</b></div>
                <div><span className="theme-text-muted">Federation:</span> <b>{provForm.federation_sharing_mode}</b></div>
                <div className="sm:col-span-2">
                  <span className="theme-text-muted">Modules:</span>{" "}
                  <b>{provForm.selected_modules.length ? provForm.selected_modules.join(", ") : "None selected"}</b>
                </div>
                <div className="sm:col-span-2">
                  <span className="theme-text-muted">Initial seeds:</span>{" "}
                  <b>{[
                    provForm.seed_icd10 && "ICD-10",
                    provForm.seed_allergies && "allergies",
                    provForm.seed_complaints && "complaints",
                  ].filter(Boolean).join(", ") || "None"}</b>
                </div>
              </div>

              <button
                onClick={executeProvisioning}
                disabled={busyKey === "provision-exec"}
                className="w-full theme-accent-bg py-3.5 rounded-2xl font-black uppercase tracking-wider flex items-center justify-center gap-2 shadow-xl"
              >
                {busyKey === "provision-exec" ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /><span>Creating PostgreSQL DB...</span></>
                ) : (
                  <><Zap className="w-4 h-4" /><span>Execute Zero-Touch Provisioning</span></>
                )}
              </button>

              {provResult && (
                <div className="p-4 rounded-2xl bg-emerald-500/15 border border-emerald-500/40 space-y-3 font-mono text-[11px]">
                  <div className="flex items-center justify-between border-b border-emerald-500/20 pb-2">
                    <span className="font-bold text-emerald-500 text-xs">PROVISIONED CREDENTIAL VAULT</span>
                    <button onClick={() => { navigator.clipboard.writeText(JSON.stringify(provResult, null, 2)); notify("Copied JSON."); }} className="px-2.5 py-1 rounded bg-emerald-500/20 text-emerald-500 font-bold hover:bg-emerald-500/30 transition-colors">Copy JSON</button>
                  </div>
                  <div><span className="opacity-70">URL:</span> <a href={provResult.subdomain_url} target="_blank" rel="noreferrer" className="text-emerald-400 font-bold hover:underline">{provResult.subdomain_url}</a></div>
                  <div><span className="opacity-70">Database:</span> {provResult.database_name}</div>
                  <div><span className="opacity-70">Admin Email:</span> {provResult.admin_email}</div>
                  <div className={provResult.email_dispatched ? "text-emerald-500" : "text-amber-500"}>
                    Welcome email: {provResult.email_dispatched ? "sent" : `not sent${provResult.email_error ? ` — ${provResult.email_error}` : ""}`}
                  </div>
                  <div><span className="opacity-70">One-Time Password:</span> <b className="theme-accent-text">{provResult.admin_password}</b></div>
                  <div><span className="opacity-70">Federation Key:</span> {provResult.federation_api_key}</div>

                  <div className="pt-3">
                    <button onClick={() => { setProvResult(null); setWizardStep(1); setProvForm(p => ({ ...p, hospital_name: "", subdomain_slug: "", branch_code: "", admin_email: "", custom_fee_inr: "" })); }} className="w-full py-2 rounded-lg bg-slate-950 hover:bg-slate-900 border border-slate-700 text-slate-300 font-bold text-center transition">+ Provision Another Hospital</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* RIGHT PANEL: LIVE SPATIAL & QUOTE PREVIEW */}
        <div className="lg:col-span-5 space-y-4">
          <div className="theme-panel rounded-2xl p-5 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider theme-accent-text">Visual Blueprint Preview</span>
              <span className="text-xs font-mono font-bold">{activeBlueprintObj.beds || 0} Seeded Beds</span>
            </div>
            {activeBlueprintObj.beds === 0 ? (
              <div className="theme-elevated border border-slate-500/20 rounded-xl p-6 text-center space-y-2">
                <Layers className="w-7 h-7 mx-auto theme-accent-text" />
                <div className="text-xs font-bold">Zero-Bed Blank Canvas Selected</div>
                <p className="text-[11px] theme-text-muted">Hospital Admin will create Floors, Wards, and Beds via EMR settings.</p>
              </div>
            ) : (
              <div className="theme-elevated border border-slate-500/20 rounded-xl p-4 space-y-2">
                <div className="text-[11px] font-mono theme-text-muted">{activeBlueprintObj.floors_count || 0} Floor(s) &bull; {activeBlueprintObj.theatres_count || 0} OT(s)</div>
                <div className="grid grid-cols-6 gap-1.5 pt-1">
                  {Array.from({ length: activeBlueprintObj.beds || 0 }).map((_, i) => (
                    <div key={i} className="p-1.5 rounded-lg theme-panel text-center font-mono text-[10px] font-bold border border-emerald-500/40 text-emerald-500">B-{String(i + 1).padStart(2, "0")}</div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="theme-panel rounded-2xl p-5 space-y-3 font-mono text-xs">
            <div className="text-xs font-bold uppercase tracking-wider theme-accent-text">Real-Time Commercial Quote</div>
            <div className="flex justify-between py-1.5 border-b border-slate-500/20">
              <span className="theme-text-muted">&Agrave; La Carte Value:</span><span>{fmtInr(quote.listAnnual)}/yr</span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-slate-500/20 text-emerald-500">
              <span>Bundle Discount:</span><span>-{fmtInr(quote.saved)}/yr</span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-slate-500/20 text-base font-black theme-accent-text">
              <span>Invoice:</span><span>{INR}{formatRupees(quote.finalFeeInr)} {quote.suffix}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}