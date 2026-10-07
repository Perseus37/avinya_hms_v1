import React, { useState, useEffect, useMemo, useCallback } from "react";
import MigrationStudio from "./MigrationStudio";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart4,
  Building2,
  CheckCircle2,
  ChevronRight,
  Copy,
  Database,
  Download,
  FileSpreadsheet,
  GitBranch,
  KeyRound,
  Layers,
  LayoutGrid,
  List,
  Loader2,
  Lock,
  Network,
  Palette,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Shield,
  ShieldAlert,
  Sparkles,
  Terminal,
  Unlock,
  Zap,
  X,
} from "lucide-react";

const INR = "\u20B9";
const fmtInr = (paise) =>
  `${INR}${Math.round((paise || 0) / 100).toLocaleString("en-IN")}`;

const THEMES = [
  { id: "obsidian-gold", name: "Obsidian Gold", dot: "#f59e0b" },
  { id: "clinical-daylight", name: "Clinical Blue (Light)", dot: "#2563eb" },
  { id: "cyber-violet", name: "Cyber Violet", dot: "#8b5cf6" },
  { id: "bio-emerald", name: "Bio Emerald", dot: "#10b981" },
];

const MODULE_LIST = [
  { code: "opd", name: "OPD & Queue TV", priceInr: 25000, group: "Clinical" },
  { code: "ipd_adt", name: "IPD & Spatial ADT", priceInr: 40000, group: "Clinical" },
  { code: "er_mlc", name: "ER Triage & MLC", priceInr: 30000, group: "Clinical" },
  { code: "ot_surgery", name: "Modular OT & Surgery", priceInr: 45000, group: "Surgical" },
  { code: "diagnostics", name: "LIS Laboratory", priceInr: 35000, group: "Diagnostics" },
  { code: "pacs_radiology", name: "Radiology PACS/DICOM", priceInr: 45000, group: "Diagnostics" },
  { code: "blood_bank", name: "Blood Bank Safety", priceInr: 35000, group: "Diagnostics" },
  { code: "pharmacy", name: "Pharmacy & FEFO", priceInr: 30000, group: "Operations" },
  { code: "billing", name: "POS & Invoicing", priceInr: 25000, group: "Finance" },
  { code: "insurance_tpa", name: "TPA & Cashless Claims", priceInr: 40000, group: "Finance" },
  { code: "executive_bi", name: "CFO Executive BI", priceInr: 50000, group: "Finance" },
];

const BUNDLES = [
  {
    code: "BUNDLE_TOTAL_HOSPITAL",
    name: "Flagship Enterprise",
    priceInr: 295000,
    tag: "All 11 Modules",
    modules: MODULE_LIST.map((m) => m.code),
  },
  {
    code: "BUNDLE_SURGICAL_SUITE",
    name: "Surgical & IPD Suite",
    priceInr: 145000,
    tag: "5 Core Modules",
    modules: ["ipd_adt", "ot_surgery", "pharmacy", "billing", "insurance_tpa"],
  },
  {
    code: "BUNDLE_DIAGNOSTICS_HUB",
    name: "Diagnostics & PACS Hub",
    priceInr: 110000,
    tag: "4 Lab/Imaging Mods",
    modules: ["diagnostics", "pacs_radiology", "billing", "pharmacy"],
  },
  {
    code: "BUNDLE_OPD_DAYCARE",
    name: "Polyclinic Daycare",
    priceInr: 65000,
    tag: "3 Outpatient Mods",
    modules: ["opd", "pharmacy", "billing"],
  },
  {
    code: "CUSTOM_A_LA_CARTE",
    name: "Custom \u00C0 La Carte",
    priceInr: null,
    tag: "Modular Pick",
    modules: [],
  },
];

const BLUEPRINTS = [
  {
    key: "TIER_1_QUATERNARY_500_BED",
    name: "Quaternary Flagship Center",
    floors: 2,
    wards: 3,
    beds: 22,
    ots: 2,
    desc: "ER Trauma Bay (10 beds), Medical ICU (8 beds), Surgical Ward (4 beds) + 2 Modular OTs",
  },
  {
    key: "BOUTIQUE_MATERNITY_NICU",
    name: "Maternity, Fertility & NICU",
    floors: 1,
    wards: 2,
    beds: 7,
    ots: 1,
    desc: "LDR Deluxe Suite (1 bed) + Level III NICU (6 Pediatric beds) + Dedicated OB-GYN OT",
  },
  {
    key: "TRAUMA_EMERGENCY_HUB",
    name: "Acute Trauma & ER Hub",
    floors: 1,
    wards: 1,
    beds: 6,
    ots: 1,
    desc: "6 Red Resuscitation ICU Bays + 24/7 Ortho-Trauma Surgical Theatre",
  },
  {
    key: "POLYCLINIC_DAYCARE_OPD",
    name: "Polyclinic & Daycare OPD",
    floors: 1,
    wards: 1,
    beds: 4,
    ots: 0,
    desc: "High-throughput Outpatient Consulting + 4 Daycare Observation Recliners",
  },
  {
    key: "BLANK_CANVAS",
    name: "Zero-Bed Blank Canvas",
    floors: 0,
    wards: 0,
    beds: 0,
    ots: 0,
    desc: "Seeds RBAC roles, departments & 10 print templates with zero pre-built beds",
  },
];

// Helper that computes exact package/module price in INR for any state
function calculateDynamicPrice(pricingModel, enabledCodesList, billingCycle, maxBeds, customOverrideEnabled, customFeeInr) {
  const rateMap = Object.fromEntries(MODULE_LIST.map((m) => [m.code, m.priceInr]));
  const listAnnual = enabledCodesList.reduce((s, c) => s + (rateMap[c] || 0), 0);
  const bundle = BUNDLES.find((b) => b.code === pricingModel);
  const baseAnnual = bundle?.priceInr || listAnnual;
  const saved = Math.max(0, listAnnual - baseAnnual);
  const hasIpd = enabledCodesList.includes("ipd_adt");
  const beds = hasIpd ? Math.max(0, Number(maxBeds) || 0) : 0;

  let autoCycleFee = baseAnnual;
  let suffix = "/ year";

  if (billingCycle === "MONTHLY_SUBSCRIPTION") {
    autoCycleFee = Math.round(baseAnnual / 12 / 100) * 100;
    suffix = "/ month";
  } else if (billingCycle === "PER_ACTIVE_BED_MONTHLY") {
    autoCycleFee = beds > 0 ? Math.max(10, beds) * 1500 : Math.round(baseAnnual / 12 / 100) * 100;
    suffix = `/ mo (${beds} beds)`;
  } else if (billingCycle === "ONE_TIME_PERPETUAL") {
    autoCycleFee = baseAnnual * 3;
    suffix = "one-time buyout";
  }

  const finalFeeInr =
    customOverrideEnabled && Number(customFeeInr) > 0
      ? Number(customFeeInr)
      : autoCycleFee;

  const normArr =
    billingCycle === "MONTHLY_SUBSCRIPTION" || billingCycle === "PER_ACTIVE_BED_MONTHLY"
      ? finalFeeInr * 12
      : billingCycle === "ONE_TIME_PERPETUAL"
      ? Math.round(finalFeeInr * 0.2)
      : finalFeeInr;

  return {
    listAnnual,
    baseAnnual,
    saved,
    autoCycleFee,
    finalFeeInr,
    normArr,
    suffix,
    beds,
  };
}

function RadialGauge({ percentage = 0, label, sublabel, color = "#f59e0b" }) {
  const radius = 36;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.min(100, Math.max(0, percentage));
  const strokeDashoffset = circumference - (clamped / 100) * circumference;

  return (
    <div className="flex items-center gap-4">
      <div className="relative w-20 h-20 flex items-center justify-center shrink-0">
        <svg className="w-20 h-20 -rotate-90" viewBox="0 0 88 88">
          <circle
            cx="44"
            cy="44"
            r={radius}
            stroke="currentColor"
            strokeWidth="8"
            fill="transparent"
            className="opacity-15"
          />
          <circle
            cx="44"
            cy="44"
            r={radius}
            stroke={color}
            strokeWidth="8"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            fill="transparent"
            className="transition-all duration-700 ease-out"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center font-mono text-xs font-black">
          {clamped}%
        </div>
      </div>
      <div>
        <div className="text-[11px] font-bold uppercase tracking-wider theme-text-muted">
          {label}
        </div>
        <div className="text-lg font-black font-mono mt-0.5">{sublabel}</div>
      </div>
    </div>
  );
}

// Key-Locked Custom Fee Override Widget (Used in both Provisioning Studio & Drawer)
function KeyGatedFeeOverride({
  autoFeeInr,
  suffix,
  isUnlocked,
  onUnlockChange,
  customFeeInr,
  onCustomFeeChange,
  masterKey,
  notify,
}) {
  const [promptOpen, setPromptOpen] = useState(false);
  const [enteredKey, setEnteredKey] = useState("");

  const handleVerifyKey = () => {
    if (!enteredKey.trim() || enteredKey.trim() !== masterKey.trim()) {
      notify("Invalid Override Authorization Key. Must match Master Vendor Key.", "err");
      return;
    }
    onUnlockChange(true);
    setPromptOpen(false);
    setEnteredKey("");
    if (!customFeeInr) onCustomFeeChange(autoFeeInr);
    notify("Custom fee override unlocked with Vendor Key.");
  };

  return (
    <div className="theme-elevated p-3.5 rounded-xl space-y-2.5 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-[10px] font-bold uppercase theme-text-muted">
            Contract Fee Calculation Mode
          </div>
          {!isUnlocked ? (
            <div className="font-mono font-black text-sm mt-0.5 flex items-center gap-2">
              <span>
                Auto Tier Price: {INR}
                {autoFeeInr.toLocaleString("en-IN")} {suffix}
              </span>
              <span className="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-500 font-bold">
                AUTO-PRICED
              </span>
            </div>
          ) : (
            <div className="font-mono font-bold text-amber-500 text-xs mt-0.5">
              Custom Negotiated Fee Override Active
            </div>
          )}
        </div>

        {!isUnlocked ? (
          <button
            type="button"
            onClick={() => setPromptOpen((v) => !v)}
            className="px-3 py-1.5 rounded-lg theme-panel font-bold text-[11px] flex items-center gap-1.5 hover:opacity-90"
          >
            <Lock className="w-3.5 h-3.5 theme-accent-text" />
            <span>Override Fee with Key</span>
          </button>
        ) : (
          <button
            type="button"
            onClick={() => {
              onUnlockChange(false);
              onCustomFeeChange("");
              notify("Reverted to automatic package pricing.");
            }}
            className="px-3 py-1.5 rounded-lg bg-emerald-500/20 text-emerald-500 font-bold text-[11px] flex items-center gap-1.5"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset to Auto Price</span>
          </button>
        )}
      </div>

      {promptOpen && !isUnlocked && (
        <div className="pt-2 border-t border-slate-500/20 flex items-center gap-2">
          <KeyRound className="w-4 h-4 theme-accent-text shrink-0" />
          <input
            type="password"
            value={enteredKey}
            onChange={(e) => setEnteredKey(e.target.value)}
            placeholder="Enter Vendor Authorization Key to unlock fee override..."
            className="flex-1 theme-input rounded-lg px-3 py-1.5 text-xs font-mono"
          />
          <button
            type="button"
            onClick={handleVerifyKey}
            className="theme-accent-bg px-3 py-1.5 rounded-lg font-bold text-xs"
          >
            Unlock
          </button>
          <button
            type="button"
            onClick={() => setPromptOpen(false)}
            className="px-2 py-1.5 theme-text-muted"
          >
            Cancel
          </button>
        </div>
      )}

      {isUnlocked && (
        <div className="pt-2 border-t border-slate-500/20 flex items-center gap-3">
          <Unlock className="w-4 h-4 text-amber-500 shrink-0" />
          <div className="flex-1">
            <label className="block text-[10px] uppercase theme-text-muted mb-1">
              Custom Negotiated Contract Fee ({INR})
            </label>
            <input
              type="number"
              value={customFeeInr}
              onChange={(e) => onCustomFeeChange(e.target.value)}
              placeholder={String(autoFeeInr)}
              className="w-full theme-input rounded-lg px-3 py-1.5 font-mono font-bold"
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem("avinya_cp_theme") || "obsidian-gold"
  );
  const [masterKey, setMasterKey] = useState(
    () => localStorage.getItem("avinya_master_key") || "master_vendor_setup_key_2026_secure"
  );
  const [activeView, setActiveView] = useState("command-center");
  const [fleetLayout, setFleetLayout] = useState("grid");
  const [wizardStep, setWizardStep] = useState(1);

  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState(null);
  const [toast, setToast] = useState(null);

  // Data Stores
  const [overview, setOverview] = useState(null);
  const [tenants, setTenants] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [schemaParity, setSchemaParity] = useState(null);
  const [topology, setTopology] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [apiLogs, setApiLogs] = useState([]);
  const [devtoolsOpen, setDevtoolsOpen] = useState(false);

  // Fleet Filters & Slide-Over Drawer State
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [selectedTenant, setSelectedTenant] = useState(null);
  const [drawerOutput, setDrawerOutput] = useState(null);

  // Drawer Form State (Notice customFeeUnlocked defaults to FALSE so changing package auto-prices!)
  const [drwForm, setDrwForm] = useState({
    pricing_model: "BUNDLE_TOTAL_HOSPITAL",
    billing_cycle: "ANNUAL_LICENSE",
    customFeeUnlocked: false,
    custom_fee_inr: "",
    max_beds: 500,
    max_users: 200,
    deployment_mode: "CLOUD",
    maintenance_mode: false,
    maintenance_notice: "",
    enabled_modules: {},
    sandbox_slug: "",
    rot_jwt: true,
    rot_backup: true,
    rot_fed: true,
  });

  // Provisioning Studio State
  const [provForm, setProvForm] = useState({
    hospital_name: "",
    subdomain_slug: "",
    branch_code: "",
    admin_email: "",
    blueprint_archetype: "TIER_1_QUATERNARY_500_BED",
    pricing_model: "BUNDLE_TOTAL_HOSPITAL",
    billing_cycle: "ANNUAL_LICENSE",
    customFeeUnlocked: false,
    custom_fee_inr: "",
    quota_max_beds: 500,
    quota_max_users: 200,
    federation_sharing_mode: "FULL_MESH",
    seed_icd10: true,
    seed_allergies: true,
    seed_complaints: true,
    selected_modules: MODULE_LIST.map((m) => m.code),
  });
  const [provResult, setProvResult] = useState(null);

  // MPI Live Verifier
  const [mpiQuery, setMpiQuery] = useState("");
  const [mpiResult, setMpiResult] = useState(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("avinya_cp_theme", theme);
  }, [theme]);

  const notify = useCallback((msg, type = "ok") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 5000);
  }, []);

  const request = useCallback(
    async (path, options = {}, tag = null) => {
      if (tag) setBusyKey(tag);
      const t0 = performance.now();
      const headers = {
        "X-Master-Setup-Key": masterKey.trim(),
        ...(options.body ? { "Content-Type": "application/json" } : {}),
      };
      try {
        const res = await fetch(path, { ...options, headers });
        const ms = Math.round(performance.now() - t0);
        let body = null;
        try {
          body = await res.json();
        } catch {
          body = {};
        }
        setApiLogs((prev) => [
          {
            id: Date.now() + Math.random(),
            ts: new Date().toLocaleTimeString(),
            method: options.method || "GET",
            path,
            status: res.status,
            ok: res.ok,
            ms,
            body,
          },
          ...prev.slice(0, 24),
        ]);
        if (!res.ok) throw new Error(body?.detail || body?.message || `HTTP ${res.status}`);
        return body;
      } finally {
        if (tag) setBusyKey(null);
      }
    },
    [masterKey]
  );

  const syncAll = useCallback(
    async (silent = false) => {
      if (!masterKey.trim()) return;
      localStorage.setItem("avinya_master_key", masterKey.trim());
      if (!silent) setLoading(true);
      try {
        const [tData, oData, aData, sData, topData, audData] = await Promise.all([
          request("/api/tenants"),
          request("/api/fleet/overview"),
          request("/api/fleet/analytics").catch(() => null),
          request("/api/fleet/schema-status").catch(() => null),
          request("/api/federation/topology").catch(() => null),
          request("/api/fleet/audit-logs?limit=80").catch(() => null),
        ]);
        setTenants(tData.items || []);
        setOverview(oData);
        if (aData) setAnalytics(aData);
        if (sData) setSchemaParity(sData);
        if (topData) setTopology(topData.nodes || []);
        if (audData) setAuditLogs(audData.items || []);
        if (!silent) notify(`Synced ${tData.items?.length || 0} hospital environments.`);
      } catch (err) {
        if (!silent) notify(err.message, "err");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [masterKey, request, notify]
  );

  useEffect(() => {
    syncAll(true);
  }, []);

  // Provisioning Studio Live Quote
  const quote = useMemo(
    () =>
      calculateDynamicPrice(
        provForm.pricing_model,
        provForm.selected_modules,
        provForm.billing_cycle,
        provForm.quota_max_beds,
        provForm.customFeeUnlocked,
        provForm.custom_fee_inr
      ),
    [provForm]
  );

  // Drawer Live Quote (Automatically updates when changing package or modules in the Drawer!)
  const drawerQuote = useMemo(() => {
    const enabledList = Object.entries(drwForm.enabled_modules)
      .filter(([, v]) => v !== false)
      .map(([k]) => k);
    return calculateDynamicPrice(
      drwForm.pricing_model,
      enabledList,
      drwForm.billing_cycle,
      drwForm.max_beds,
      drwForm.customFeeUnlocked,
      drwForm.custom_fee_inr
    );
  }, [drwForm]);

  const activeBlueprintObj = useMemo(
    () =>
      BLUEPRINTS.find((b) => b.key === provForm.blueprint_archetype) || BLUEPRINTS[0],
    [provForm.blueprint_archetype]
  );

  const inspectHospital = async (slug) => {
    setDrawerOutput(null);
    try {
      const d = await request(`/api/tenants/${slug}/details`, {}, `inspect-${slug}`);
      setSelectedTenant(d);
      setDrwForm({
        pricing_model: d.pricing_model || "BUNDLE_TOTAL_HOSPITAL",
        billing_cycle: d.billing_cycle || "ANNUAL_LICENSE",
        customFeeUnlocked: false, // Locked by default so package changes auto-calculate!
        custom_fee_inr: Math.round((d.base_license_fee_paise || 0) / 100),
        max_beds: d.quota_limits?.max_beds ?? 500,
        max_users: d.quota_limits?.max_users ?? 200,
        deployment_mode: d.deployment_mode || "CLOUD",
        maintenance_mode: !!d.maintenance_mode,
        maintenance_notice: d.maintenance_notice || "",
        enabled_modules: d.enabled_modules || {},
        sandbox_slug: `${d.subdomain_slug}-uat`,
        rot_jwt: true,
        rot_backup: true,
        rot_fed: true,
      });
    } catch (err) {
      notify(err.message, "err");
    }
  };

  // When user changes the Pricing Model dropdown inside the Drawer, auto-sync the modules!
  const handleDrawerPackageChange = (newModel) => {
    const bundleObj = BUNDLES.find((b) => b.code === newModel);
    let nextMods = { ...drwForm.enabled_modules };
    if (bundleObj && newModel !== "CUSTOM_A_LA_CARTE") {
      nextMods = Object.fromEntries(
        MODULE_LIST.map((m) => [m.code, bundleObj.modules.includes(m.code)])
      );
    }
    setDrwForm((p) => ({
      ...p,
      pricing_model: newModel,
      enabled_modules: nextMods,
      customFeeUnlocked: false,
    }));
  };

  const filteredTenants = useMemo(() => {
    const q = search.toLowerCase();
    return tenants.filter((t) => {
      const match =
        t.hospital_name.toLowerCase().includes(q) ||
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
    <div className="h-screen overflow-hidden flex flex-col md:flex-row">
      {/* ================================================================= */}
      {/* LEFT COMMAND SIDEBAR RAIL (FIXED HEIGHT, ZERO VISIBLE SCROLLBAR)  */}
      {/* ================================================================= */}
      <aside className="w-full md:w-72 md:h-screen md:sticky md:top-0 theme-panel border-r flex flex-col justify-between p-4 shrink-0 overflow-hidden select-none">
        <div className="space-y-4 overflow-y-auto no-scrollbar">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl theme-accent-bg flex items-center justify-center font-black text-lg shadow-lg">
                A
              </div>
              <div>
                <div className="font-black tracking-tight text-sm">AVINYA NEXUS</div>
                <div className="text-[10px] font-mono theme-text-muted">
                  CLOUD CONTROL OS v5.1
                </div>
              </div>
            </div>
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-ping" />
          </div>

          {/* 4-Theme Switcher */}
          <div className="theme-elevated p-2.5 rounded-xl space-y-2">
            <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-wider theme-text-muted">
              <span className="flex items-center gap-1">
                <Palette className="w-3 h-3" /> UI Theme Engine
              </span>
              <span>High-Contrast</span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {THEMES.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTheme(t.id)}
                  className={`px-2.5 py-1.5 rounded-lg text-[11px] font-semibold flex items-center gap-2 transition border ${
                    theme === t.id
                      ? "theme-panel font-bold border-current"
                      : "opacity-70 hover:opacity-100 border-transparent"
                  }`}
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: t.dot }}
                  />
                  <span className="truncate">{t.name}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Navigation */}
          <nav className="space-y-1.5">
            <div className="text-[10px] font-bold uppercase tracking-wider theme-text-muted px-2 mb-2">
              Control Plane Modules
            </div>
            {[
              {
                id: "command-center",
                label: "Executive Telemetry & BI",
                sub: "ARR, GMV & Radial Gauges",
                icon: BarChart4,
              },
              {
                id: "fleet-matrix",
                label: "Hospital Fleet Explorer",
                sub: `${tenants.length} Provisioned Tenants`,
                icon: Building2,
              },
              {
                id: "provision-studio",
                label: "4-Step Onboarding Studio",
                sub: "Visual Blueprint & Quote",
                icon: Sparkles,
              },
              {
                id: "canary-migrations",
                label: "Canary & Schema Rollout",
                sub: "2-Stage Alembic Pipeline",
                icon: GitBranch,
              },
              {
                id: "federation-mesh",
                label: "Zero-Knowledge MPI Mesh",
                sub: "Sovereignty & Hash Verifier",
                icon: Network,
              },
              {
                id: "audit-vault",
                label: "Forensic Audit Ledger",
                sub: `${auditLogs.length} Immutable Events`,
                icon: Shield,
              },
            ].map((item) => {
              const Icon = item.icon;
              const active = activeView === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveView(item.id)}
                  className={`w-full text-left px-3.5 py-3 rounded-xl transition flex items-center justify-between ${
                    active ? "theme-accent-bg shadow-lg" : "hover:opacity-85 theme-elevated"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <Icon className="w-4 h-4 shrink-0" />
                    <div>
                      <div className="text-xs font-bold leading-none">{item.label}</div>
                      <div
                        className={`text-[10px] mt-1 font-mono ${
                          active ? "opacity-90" : "theme-text-muted"
                        }`}
                      >
                        {item.sub}
                      </div>
                    </div>
                  </div>
                  <ChevronRight className="w-3.5 h-3.5 opacity-60" />
                </button>
              );
            })}
          </nav>
        </div>

        {/* Bottom Master Key Vault */}
        <div className="space-y-3 pt-4 border-t border-slate-500/20">
          <div className="space-y-1.5">
            <label className="text-[10px] font-bold uppercase font-mono theme-text-muted flex items-center gap-1">
              <KeyRound className="w-3 h-3" /> Master Vendor Key
            </label>
            <input
              type="password"
              value={masterKey}
              onChange={(e) => setMasterKey(e.target.value)}
              className="w-full theme-input rounded-xl px-3 py-2 text-xs font-mono"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => syncAll(false)}
              disabled={loading}
              className="flex-1 theme-accent-bg font-bold py-2 rounded-xl text-xs flex items-center justify-center gap-1.5 shadow-md"
            >
              {loading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <RefreshCw className="w-3.5 h-3.5" />
              )}
              <span>Sync Cluster</span>
            </button>
            <button
              onClick={() => setDevtoolsOpen((v) => !v)}
              title="Toggle API Latency & Payload Console"
              className="px-3 py-2 rounded-xl theme-elevated text-xs font-mono flex items-center gap-1"
            >
              <Terminal className="w-3.5 h-3.5" />
              <span>{apiLogs.length}</span>
            </button>
          </div>
        </div>
      </aside>

      {/* ================================================================= */}
      {/* MAIN WORKSPACE CANVAS (INDEPENDENTLY SCROLLABLE)                  */}
      {/* ================================================================= */}
      <main className="flex-1 h-full p-6 md:p-8 overflow-y-auto space-y-6">
        {toast && (
          <div
            className={`p-4 rounded-2xl border text-xs font-bold flex items-center justify-between shadow-xl ${
              toast.type === "err"
                ? "bg-rose-500/20 border-rose-500/50 text-rose-500"
                : "bg-emerald-500/20 border-emerald-500/50 text-emerald-500"
            }`}
          >
            <span>{toast.msg}</span>
            <button onClick={() => setToast(null)}>
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* =============================================================== */}
        {/* VIEW 1: EXECUTIVE TELEMETRY & COMMERCIAL BI                     */}
        {/* =============================================================== */}
        {activeView === "command-center" && (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h1 className="text-2xl font-black tracking-tight">
                  Executive Cluster Telemetry &amp; Commercial Intelligence
                </h1>
                <p className="text-xs theme-text-muted mt-0.5">
                  Real-time SaaS ARR book, hospital GMV waterfall, radial capacity gauges, and module penetration.
                </p>
              </div>
              <button
                onClick={() => setActiveView("provision-studio")}
                className="theme-accent-bg px-4 py-2.5 rounded-xl text-xs font-black uppercase tracking-wider flex items-center gap-2 shadow-lg"
              >
                <Plus className="w-4 h-4" />
                <span>Provision Hospital Tenant</span>
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="theme-panel p-5 rounded-2xl flex items-center justify-between">
                <RadialGauge
                  percentage={
                    overview?.total_hospitals
                      ? Math.round((overview.active_hospitals / overview.total_hospitals) * 100)
                      : 100
                  }
                  label="Active Cloud Fleet"
                  sublabel={`${overview?.active_hospitals ?? 0} / ${
                    overview?.total_hospitals ?? 0
                  } Nodes`}
                  color="#10b981"
                />
              </div>

              <div className="theme-panel p-5 rounded-2xl flex items-center justify-between">
                <RadialGauge
                  percentage={overview?.fleet_bed_occupancy_pct || 0}
                  label="Cluster Bed Census"
                  sublabel={`${overview?.occupied_beds ?? 0} / ${overview?.total_beds ?? 0} Beds`}
                  color="#f59e0b"
                />
              </div>

              <div className="theme-panel p-5 rounded-2xl flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold uppercase theme-text-muted">
                    Normalized Annual ARR
                  </span>
                  <Sparkles className="w-4 h-4 theme-accent-text" />
                </div>
                <div className="text-3xl font-black font-mono theme-accent-text mt-2">
                  {overview ? fmtInr(overview.vendor_annual_license_arr_paise) : `${INR}0`}
                </div>
                <div className="text-[11px] font-mono theme-text-muted mt-1">
                  Annualized across all active contracts
                </div>
              </div>

              <div className="theme-panel p-5 rounded-2xl flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold uppercase theme-text-muted">
                    Hospital Patient GMV
                  </span>
                  <Activity className="w-4 h-4 text-emerald-500" />
                </div>
                <div className="text-3xl font-black font-mono text-emerald-500 mt-2">
                  {overview ? fmtInr(overview.fleet_gross_collected_revenue_paise) : `${INR}0`}
                </div>
                <div className="text-[11px] font-mono theme-text-muted mt-1">
                  {overview?.global_patient_pointers_indexed ?? 0} MPI Pointers &bull;{" "}
                  {overview?.encrypted_disaster_snapshots_count ?? 0} Backups
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-5 theme-panel rounded-2xl p-6 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold uppercase tracking-wider theme-accent-text">
                    SaaS ARR Share by Commercial Tier
                  </h3>
                  <span className="text-[11px] font-mono theme-text-muted">Live Book</span>
                </div>
                <div className="space-y-3">
                  {Object.entries(analytics?.arr_by_pricing_model_paise || {}).map(
                    ([tier, paise]) => {
                      const pct = Math.min(
                        100,
                        Math.round(
                          (paise / Math.max(1, overview?.vendor_annual_license_arr_paise || 1)) *
                            100
                        )
                      );
                      return (
                        <div key={tier} className="space-y-1.5">
                          <div className="flex justify-between text-xs font-mono">
                            <span className="font-bold">{tier}</span>
                            <span>
                              {fmtInr(paise)} ({pct}%)
                            </span>
                          </div>
                          <div className="w-full h-2.5 rounded-full theme-elevated overflow-hidden">
                            <div
                              className="h-full theme-accent-bg transition-all duration-700"
                              style={{ width: `${Math.max(4, pct)}%` }}
                            />
                          </div>
                        </div>
                      );
                    }
                  )}
                </div>

                <div className="pt-4 border-t border-slate-500/20 space-y-2">
                  <div className="text-xs font-bold uppercase tracking-wider theme-text-muted">
                    Billing Cycle Mix
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(analytics?.tenants_by_billing_cycle || {}).map(
                      ([cycle, cnt]) => (
                        <div
                          key={cycle}
                          className="theme-elevated p-2.5 rounded-xl flex items-center justify-between text-xs font-mono"
                        >
                          <span className="truncate text-[10px]">{cycle}</span>
                          <span className="font-black theme-accent-text">{cnt}</span>
                        </div>
                      )
                    )}
                  </div>
                </div>
              </div>

              <div className="lg:col-span-7 theme-panel rounded-2xl p-6 space-y-4">
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider theme-accent-text">
                    11-Module Licensing Penetration Matrix
                  </h3>
                  <p className="text-[11px] theme-text-muted">
                    Active module entitlements across {analytics?.total_tenants || tenants.length} registered hospitals
                  </p>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  {MODULE_LIST.map((m) => {
                    const count = analytics?.module_adoption_counts?.[m.code] ?? tenants.length;
                    const total = Math.max(1, analytics?.total_tenants || tenants.length);
                    const pct = Math.round((count / total) * 100);
                    return (
                      <div key={m.code} className="theme-elevated p-3 rounded-xl space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded theme-panel">
                            {m.group}
                          </span>
                          <span className="text-xs font-mono font-black theme-accent-text">
                            {count}/{total}
                          </span>
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
                <h3 className="text-sm font-bold">
                  Multi-Tenant Performance &amp; Revenue Leaderboard
                </h3>
                <p className="text-xs theme-text-muted">
                  Click any hospital row to open its full command drawer (Package, Quotas, Key Rotation, Snapshots &amp; Circuit-Breaker).
                </p>
              </div>
              <div className="overflow-x-auto text-xs font-mono">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-slate-500/20 text-[11px] uppercase theme-text-muted">
                      <th className="py-3 px-3">Hospital Tenant</th>
                      <th className="py-3 px-3">Commercial Tier</th>
                      <th className="py-3 px-3">Annualized ARR</th>
                      <th className="py-3 px-3">MPI Patients</th>
                      <th className="py-3 px-3">Bed Census</th>
                      <th className="py-3 px-3">Settled GMV</th>
                      <th className="py-3 px-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(analytics?.hospital_leaderboard || tenants).map((h) => (
                      <tr
                        key={h.subdomain_slug}
                        onClick={() => inspectHospital(h.subdomain_slug)}
                        className="border-b border-slate-500/10 theme-row-hover cursor-pointer transition"
                      >
                        <td className="py-3 px-3 font-bold">
                          {h.hospital_name}{" "}
                          <span className="theme-text-muted">({h.subdomain_slug})</span>
                        </td>
                        <td className="py-3 px-3 theme-accent-text">{h.pricing_model}</td>
                        <td className="py-3 px-3 text-emerald-500 font-bold">
                          {fmtInr(h.normalized_arr_paise ?? h.base_license_fee_paise)}
                        </td>
                        <td className="py-3 px-3">{h.patients_count ?? "--"}</td>
                        <td className="py-3 px-3">
                          {h.occupied_beds ?? 0}/{h.total_beds ?? 0} ({h.occupancy_pct ?? 0}%)
                        </td>
                        <td className="py-3 px-3 font-bold">
                          {fmtInr(h.collected_gmv_paise ?? 0)}
                        </td>
                        <td className="py-3 px-3 text-right">
                          <span className="px-2.5 py-1 rounded-lg theme-elevated text-[11px] font-bold">
                            Manage &rarr;
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* =============================================================== */}
        {/* VIEW 2: HOSPITAL FLEET EXPLORER                                 */}
        {/* =============================================================== */}
        {activeView === "fleet-matrix" && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h1 className="text-2xl font-black tracking-tight">Hospital Tenant Explorer</h1>
                <p className="text-xs theme-text-muted">
                  Click Command Drawer on any hospital to switch packages, rotate keys, clone sandboxes, or restore snapshots.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search hospital, slug, branch..."
                  className="theme-input rounded-xl px-3.5 py-2 text-xs w-64"
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
                    className={`p-1.5 rounded-lg ${
                      fleetLayout === "grid" ? "theme-accent-bg" : ""
                    }`}
                  >
                    <LayoutGrid className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setFleetLayout("list")}
                    className={`p-1.5 rounded-lg ${
                      fleetLayout === "list" ? "theme-accent-bg" : ""
                    }`}
                  >
                    <List className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>

            {fleetLayout === "grid" ? (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {filteredTenants.map((t) => (
                  <div
                    key={t.id}
                    className="theme-panel rounded-2xl p-5 flex flex-col justify-between space-y-4 hover:scale-[1.01] transition"
                  >
                    <div>
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="font-black text-base flex items-center gap-2">
                            <span>{t.hospital_name}</span>
                            {t.subdomain_slug === "default" && (
                              <span className="px-2 py-0.5 rounded text-[9px] font-mono theme-accent-bg">
                                CANARY
                              </span>
                            )}
                          </div>
                          <div className="text-xs font-mono theme-text-muted mt-0.5">
                            {t.subdomain_slug} &bull; {t.branch_code}
                          </div>
                        </div>
                        <span
                          className={`px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                            !t.is_active
                              ? "bg-rose-500/20 text-rose-500"
                              : t.maintenance_mode
                              ? "bg-amber-500/20 text-amber-500"
                              : "bg-emerald-500/20 text-emerald-500"
                          }`}
                        >
                          {!t.is_active
                            ? "SUSPENDED"
                            : t.maintenance_mode
                            ? "MAINTENANCE"
                            : "ONLINE"}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-2.5 mt-4 theme-elevated p-3.5 rounded-xl text-xs font-mono">
                        <div>
                          <div className="text-[10px] theme-text-muted">DATABASE</div>
                          <div className="font-bold truncate">{t.db_name}</div>
                        </div>
                        <div>
                          <div className="text-[10px] theme-text-muted">MODULES</div>
                          <div className="font-bold text-emerald-500">
                            {t.active_modules_count} / 11 Active
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] theme-text-muted">PACKAGE</div>
                          <div className="font-bold theme-accent-text truncate">
                            {t.pricing_model}
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] theme-text-muted">CONTRACT FEE</div>
                          <div className="font-bold">{fmtInr(t.base_license_fee_paise)}</div>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center justify-between pt-2">
                      <a
                        href={`http://${t.subdomain_slug}.localhost:8000/docs`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs font-mono theme-text-muted hover:underline flex items-center gap-1"
                      >
                        <span>Open Swagger</span>
                        <ArrowUpRight className="w-3.5 h-3.5" />
                      </a>
                      <button
                        onClick={() => inspectHospital(t.subdomain_slug)}
                        className="theme-accent-bg px-4 py-2 rounded-xl text-xs font-bold flex items-center gap-1.5 shadow"
                      >
                        {busyKey === `inspect-${t.subdomain_slug}` && (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        )}
                        <span>Command Drawer &rarr;</span>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="theme-panel rounded-2xl overflow-hidden text-xs font-mono">
                <table className="w-full text-left">
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
                    {filteredTenants.map((t) => (
                      <tr key={t.id} className="border-b border-slate-500/10 theme-row-hover">
                        <td className="py-3 px-4 font-bold">{t.hospital_name}</td>
                        <td className="py-3 px-4">
                          {t.subdomain_slug} ({t.db_name})
                        </td>
                        <td className="py-3 px-4 theme-accent-text">{t.pricing_model}</td>
                        <td className="py-3 px-4">{t.active_modules_count}/11</td>
                        <td className="py-3 px-4 font-bold">{fmtInr(t.base_license_fee_paise)}</td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => inspectHospital(t.subdomain_slug)}
                            className="theme-accent-bg px-3 py-1 rounded-lg font-bold"
                          >
                            Manage
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* =============================================================== */}
        {/* VIEW 3: 4-STEP INTERACTIVE ONBOARDING WIZARD                    */}
        {/* =============================================================== */}
        {activeView === "provision-studio" && (
          <div className="space-y-6">
            <div>
              <h1 className="text-2xl font-black tracking-tight">
                Interactive Hospital Provisioning Studio
              </h1>
              <p className="text-xs theme-text-muted">
                Step-by-step cloud database creation, visual spatial blueprint preview, auto-pricing, and key-gated fee overrides.
              </p>
            </div>

            <div className="grid grid-cols-4 gap-3">
              {[
                { step: 1, title: "1. Hospital Identity & Sovereignty" },
                { step: 2, title: "2. Visual Spatial Blueprint" },
                { step: 3, title: "3. Commercial Bundle & Quotas" },
                { step: 4, title: "4. Launch & Security Vault" },
              ].map((s) => (
                <button
                  key={s.step}
                  onClick={() => setWizardStep(s.step)}
                  className={`p-3.5 rounded-2xl text-left font-bold text-xs border transition ${
                    wizardStep === s.step
                      ? "theme-accent-bg shadow-lg"
                      : "theme-panel opacity-75 hover:opacity-100"
                  }`}
                >
                  {s.title}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-7 theme-panel rounded-2xl p-6 space-y-5">
                {wizardStep === 1 && (
                  <div className="space-y-4">
                    <h3 className="text-base font-black">
                      Step 1: Hospital Legal Identity &amp; Federation Privacy Sovereignty
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
                      <div className="sm:col-span-2">
                        <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">
                          Hospital Legal Name
                        </label>
                        <input
                          type="text"
                          value={provForm.hospital_name}
                          onChange={(e) => {
                            const val = e.target.value;
                            const slug = val
                              .toLowerCase()
                              .replace(/[^a-z0-9]+/g, "-")
                              .replace(/^-|-$/g, "")
                              .slice(0, 24);
                            const pref = (
                              val.replace(/[^A-Za-z]/g, "").toUpperCase() + "HMS"
                            ).slice(0, 6);
                            setProvForm((p) => ({
                              ...p,
                              hospital_name: val,
                              subdomain_slug: slug,
                              branch_code: val ? `${pref}-HYD-01` : "",
                            }));
                          }}
                          placeholder="e.g. AIG Hospitals Gachibowli"
                          className="w-full theme-input rounded-xl px-4 py-3 text-sm"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">
                          Tenant Subdomain Slug
                        </label>
                        <input
                          type="text"
                          value={provForm.subdomain_slug}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, subdomain_slug: e.target.value }))
                          }
                          placeholder="aig-gachibowli"
                          className="w-full theme-input rounded-xl px-3.5 py-2.5 font-mono"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">
                          Branch Code
                        </label>
                        <input
                          type="text"
                          value={provForm.branch_code}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, branch_code: e.target.value }))
                          }
                          placeholder="AIGHMS-HYD-01"
                          className="w-full theme-input rounded-xl px-3.5 py-2.5 font-mono uppercase"
                        />
                      </div>
                      <div className="sm:col-span-2">
                        <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">
                          Superuser Admin Email
                        </label>
                        <input
                          type="email"
                          value={provForm.admin_email}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, admin_email: e.target.value }))
                          }
                          placeholder="cto@aighospitals.com"
                          className="w-full theme-input rounded-xl px-3.5 py-2.5"
                        />
                      </div>
                      <div className="sm:col-span-2">
                        <label className="block text-[11px] font-bold uppercase theme-text-muted mb-1">
                          Day-0 Federation Data-Sharing Sovereignty Mode
                        </label>
                        <select
                          value={provForm.federation_sharing_mode}
                          onChange={(e) =>
                            setProvForm((p) => ({
                              ...p,
                              federation_sharing_mode: e.target.value,
                            }))
                          }
                          className="w-full theme-input rounded-xl px-3.5 py-2.5"
                        >
                          <option value="FULL_MESH">
                            FULL_MESH (Share Demographics &amp; Allergies Across Network)
                          </option>
                          <option value="CORPORATE_GROUP_ONLY">
                            CORPORATE_GROUP_ONLY (Share Only With Sister Branches)
                          </option>
                          <option value="EMERGENCY_ALLERGIES_ONLY">
                            EMERGENCY_ALLERGIES_ONLY (Redacts Residential Address)
                          </option>
                          <option value="ISOLATED_SILO">
                            ISOLATED_SILO (100% Private / Zero Outbound Sharing)
                          </option>
                        </select>
                      </div>
                    </div>
                    <div className="flex justify-end pt-3">
                      <button
                        onClick={() => {
                          if (!provForm.hospital_name || !provForm.subdomain_slug || !provForm.branch_code || !provForm.admin_email) {
                            return notify("Please fill in all Hospital Identity fields before continuing.", "err");
                          }
                          if (!/\S+@\S+\.\S+/.test(provForm.admin_email)) {
                            return notify("Please enter a valid Admin Email address.", "err");
                          }
                          setWizardStep(2);
                        }}
                        className="theme-accent-bg px-5 py-2.5 rounded-xl text-xs font-black shadow-lg"
                      >
                        Next: Select Spatial Blueprint &rarr;
                      </button>
                    </div>
                  </div>
                )}

                {wizardStep === 2 && (
                  <div className="space-y-4">
                    <h3 className="text-base font-black">
                      Step 2: Choose Starter Spatial Topology (Or Zero-Bed Blank Canvas)
                    </h3>
                    <div className="grid grid-cols-1 gap-3">
                      {BLUEPRINTS.map((bp) => {
                        const active = provForm.blueprint_archetype === bp.key;
                        return (
                          <div
                            key={bp.key}
                            onClick={() =>
                              setProvForm((p) => ({ ...p, blueprint_archetype: bp.key }))
                            }
                            className={`p-4 rounded-2xl cursor-pointer transition flex items-center justify-between ${
                              active ? "theme-accent-bg shadow-lg" : "theme-elevated"
                            }`}
                          >
                            <div>
                              <div className="font-black text-sm">{bp.name}</div>
                              <div className="text-xs opacity-85 mt-0.5">{bp.desc}</div>
                            </div>
                            <div className="text-right font-mono text-xs shrink-0 ml-4">
                              <div className="font-black">{bp.beds} Beds</div>
                              <div className="text-[10px] opacity-80">
                                {bp.floors} Flr &bull; {bp.ots} OT
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex justify-between pt-3">
                      <button
                        onClick={() => setWizardStep(1)}
                        className="theme-elevated px-4 py-2 rounded-xl text-xs font-bold"
                      >
                        &larr; Back
                      </button>
                      <button
                        onClick={() => setWizardStep(3)}
                        className="theme-accent-bg px-5 py-2.5 rounded-xl text-xs font-black"
                      >
                        Next: Commercial Package &rarr;
                      </button>
                    </div>
                  </div>
                )}

                {wizardStep === 3 && (
                  <div className="space-y-4 text-xs">
                    <h3 className="text-base font-black">
                      Step 3: Commercial Bundle, Auto-Pricing &amp; Resource Quotas
                    </h3>
                    <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                      {BUNDLES.map((b) => (
                        <button
                          type="button"
                          key={b.code}
                          onClick={() =>
                            setProvForm((p) => ({
                              ...p,
                              pricing_model: b.code,
                              selected_modules:
                                b.code === "CUSTOM_A_LA_CARTE" ? p.selected_modules : b.modules,
                              customFeeUnlocked: false,
                            }))
                          }
                          className={`p-3 rounded-xl text-left transition ${
                            provForm.pricing_model === b.code
                              ? "theme-accent-bg shadow-md"
                              : "theme-elevated"
                          }`}
                        >
                          <div className="font-bold">{b.name}</div>
                          <div className="text-[10px] opacity-80 mt-0.5">{b.tag}</div>
                        </button>
                      ))}
                    </div>

                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 theme-elevated p-3.5 rounded-2xl">
                      {MODULE_LIST.map((m) => {
                        const checked = provForm.selected_modules.includes(m.code);
                        return (
                          <label key={m.code} className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => {
                                const next = e.target.checked
                                  ? [...provForm.selected_modules, m.code]
                                  : provForm.selected_modules.filter((x) => x !== m.code);
                                setProvForm((p) => ({
                                  ...p,
                                  pricing_model: "CUSTOM_A_LA_CARTE",
                                  selected_modules: next,
                                  customFeeUnlocked: false,
                                }));
                              }}
                            />
                            <span>
                              {m.name} ({INR}
                              {m.priceInr / 1000}k)
                            </span>
                          </label>
                        );
                      })}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div>
                        <label className="block text-[10px] font-bold uppercase theme-text-muted mb-1">
                          Billing Cycle
                        </label>
                        <select
                          value={provForm.billing_cycle}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, billing_cycle: e.target.value }))
                          }
                          className="w-full theme-input rounded-xl p-2.5"
                        >
                          <option value="ANNUAL_LICENSE">Annual License</option>
                          <option value="MONTHLY_SUBSCRIPTION">Monthly Installment</option>
                          <option value="PER_ACTIVE_BED_MONTHLY">Per-Bed Monthly</option>
                          <option value="ONE_TIME_PERPETUAL">Perpetual Buyout</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-[10px] font-bold uppercase theme-text-muted mb-1">
                          Max Bed Quota
                        </label>
                        <input
                          type="number"
                          value={provForm.quota_max_beds}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, quota_max_beds: e.target.value }))
                          }
                          className="w-full theme-input rounded-xl p-2.5 font-mono"
                        />
                      </div>
                      <div>
                        <label className="block text-[10px] font-bold uppercase theme-text-muted mb-1">
                          Max Staff Users
                        </label>
                        <input
                          type="number"
                          value={provForm.quota_max_users}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, quota_max_users: e.target.value }))
                          }
                          className="w-full theme-input rounded-xl p-2.5 font-mono"
                        />
                      </div>
                    </div>

                    {/* Key-Gated Custom Fee Override */}
                    <KeyGatedFeeOverride
                      autoFeeInr={quote.autoCycleFee}
                      suffix={quote.suffix}
                      isUnlocked={provForm.customFeeUnlocked}
                      onUnlockChange={(v) =>
                        setProvForm((p) => ({ ...p, customFeeUnlocked: v }))
                      }
                      customFeeInr={provForm.custom_fee_inr}
                      onCustomFeeChange={(val) =>
                        setProvForm((p) => ({ ...p, custom_fee_inr: val }))
                      }
                      masterKey={masterKey}
                      notify={notify}
                    />

                    <div className="flex justify-between pt-3">
                      <button
                        onClick={() => setWizardStep(2)}
                        className="theme-elevated px-4 py-2 rounded-xl text-xs font-bold"
                      >
                        &larr; Back
                      </button>
                      <button
                        onClick={() => setWizardStep(4)}
                        className="theme-accent-bg px-5 py-2.5 rounded-xl text-xs font-black"
                      >
                        Next: Review &amp; Execute &rarr;
                      </button>
                    </div>
                  </div>
                )}

                {wizardStep === 4 && (
                  <div className="space-y-4 text-xs">
                    <h3 className="text-base font-black">
                      Step 4: Confirm Zero-Touch Cloud Provisioning
                    </h3>
                    <div className="theme-elevated p-4 rounded-2xl grid grid-cols-2 gap-3 font-mono">
                      <div>
                        <span className="theme-text-muted">Hospital:</span>{" "}
                        <b>{provForm.hospital_name || "Unset"}</b>
                      </div>
                      <div>
                        <span className="theme-text-muted">Target DB:</span>{" "}
                        <b className="theme-accent-text">
                          hms_{provForm.subdomain_slug || "tenant"}
                        </b>
                      </div>
                      <div>
                        <span className="theme-text-muted">Package:</span>{" "}
                        <b>{provForm.pricing_model}</b>
                      </div>
                      <div>
                        <span className="theme-text-muted">Contract Fee:</span>{" "}
                        <b className="text-emerald-500">
                          {INR}
                          {quote.finalFeeInr.toLocaleString("en-IN")} {quote.suffix}
                        </b>
                      </div>
                    </div>

                    <button
                      onClick={async () => {
                        try {
                          const customInr =
                            provForm.customFeeUnlocked && Number(provForm.custom_fee_inr) > 0
                              ? Number(provForm.custom_fee_inr)
                              : 0;
                          const data = await request(
                            "/api/provision",
                            {
                              method: "POST",
                              body: JSON.stringify({
                                master_setup_key: masterKey.trim(),
                                hospital_name: provForm.hospital_name.trim(),
                                subdomain_slug: provForm.subdomain_slug.trim().toLowerCase(),
                                branch_code: provForm.branch_code.trim().toUpperCase(),
                                admin_email: provForm.admin_email.trim().toLowerCase(),
                                pricing_model: provForm.pricing_model,
                                billing_cycle: provForm.billing_cycle,
                                selected_modules: provForm.selected_modules,
                                base_license_fee_paise: customInr > 0 ? customInr * 100 : null,
                                quota_max_beds: Number(provForm.quota_max_beds) || 0,
                                quota_max_users: Number(provForm.quota_max_users) || 200,
                                blueprint_archetype: provForm.blueprint_archetype,
                                federation_sharing_mode: provForm.federation_sharing_mode,
                                seed_icd10: provForm.seed_icd10,
                                seed_allergies: provForm.seed_allergies,
                                seed_complaints: provForm.seed_complaints,
                              }),
                            },
                            "provision-exec"
                          );
                          setProvResult(data);
                          notify(`Provisioned ${data.hospital_name} (${data.database_name})!`);
                          await syncAll(true);
                        } catch (err) {
                          notify(err.message, "err");
                        }
                      }}
                      disabled={busyKey === "provision-exec"}
                      className="w-full theme-accent-bg py-3.5 rounded-2xl font-black uppercase tracking-wider flex items-center justify-center gap-2 shadow-xl"
                    >
                      {busyKey === "provision-exec" ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span>Creating PostgreSQL DB &amp; Applying 36-Table Schema...</span>
                        </>
                      ) : (
                        <>
                          <Zap className="w-4 h-4" />
                          <span>Execute Zero-Touch Provisioning Now</span>
                        </>
                      )}
                    </button>

                    {provResult && (
                      <div className="p-4 rounded-2xl bg-emerald-500/15 border border-emerald-500/40 space-y-3 font-mono text-[11px]">
                        <div className="flex items-center justify-between border-b border-emerald-500/20 pb-2">
                          <span className="font-bold text-emerald-500 text-xs">
                            PROVISIONED CREDENTIAL VAULT
                          </span>
                          <button
                            onClick={() => {
                              navigator.clipboard.writeText(JSON.stringify(provResult, null, 2));
                              notify("Copied JSON credentials.");
                            }}
                            className="px-2.5 py-1 rounded bg-emerald-500/20 text-emerald-500 font-bold"
                          >
                            Copy JSON
                          </button>
                        </div>
                        <div><span className="opacity-70">URL:</span> <a href={provResult.subdomain_url} target="_blank" className="text-emerald-400 font-bold hover:underline">{provResult.subdomain_url}</a></div>
                        <div><span className="opacity-70">Database:</span> {provResult.database_name}</div>
                        <div><span className="opacity-70">Admin Email:</span> {provResult.admin_email}</div>
                        <div>
                          <span className="opacity-70">One-Time Password:</span>{" "}
                          <b className="theme-accent-text">{provResult.admin_password}</b>
                        </div>
                        <div><span className="opacity-70">Federation Key:</span> {provResult.federation_api_key}</div>
                        
                        <div className="pt-3">
                          <button
                            onClick={() => {
                              setProvResult(null);
                              setWizardStep(1);
                              setProvForm(prev => ({
                                ...prev,
                                hospital_name: "", subdomain_slug: "", branch_code: "", admin_email: "", custom_fee_inr: ""
                              }));
                            }}
                            className="w-full py-2 rounded-lg bg-slate-950 hover:bg-slate-900 border border-slate-700 text-slate-300 font-bold text-center transition"
                          >
                            + Provision Another Hospital
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Right 5 Cols: Spatial Visualizer + Live Commercial Quote */}
              <div className="lg:col-span-5 space-y-4">
                <div className="theme-panel rounded-2xl p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wider theme-accent-text">
                      Visual Spatial Blueprint Preview
                    </span>
                    <span className="text-xs font-mono font-bold">
                      {activeBlueprintObj.beds} Seeded Beds
                    </span>
                  </div>
                  {activeBlueprintObj.beds === 0 ? (
                    <div className="theme-elevated rounded-xl p-6 text-center space-y-2">
                      <Layers className="w-7 h-7 mx-auto theme-accent-text" />
                      <div className="text-xs font-bold">Zero-Bed Blank Canvas Selected</div>
                      <p className="text-[11px] theme-text-muted">
                        Hospital Admin will create Floors, Wards, Rooms, and up to {quote.beds} licensed beds via /v1/api/ip/...
                      </p>
                    </div>
                  ) : (
                    <div className="theme-elevated rounded-xl p-4 space-y-2">
                      <div className="text-[11px] font-mono theme-text-muted">
                        {activeBlueprintObj.floors} Floor(s) &bull; {activeBlueprintObj.wards} Ward(s) &bull;{" "}
                        {activeBlueprintObj.ots} Modular OT(s)
                      </div>
                      <div className="grid grid-cols-6 gap-1.5 pt-1">
                        {Array.from({ length: activeBlueprintObj.beds }).map((_, i) => (
                          <div
                            key={i}
                            className="p-1.5 rounded-lg theme-panel text-center font-mono text-[10px] font-bold border border-emerald-500/40 text-emerald-500"
                          >
                            B-{String(i + 1).padStart(2, "0")}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="theme-panel rounded-2xl p-5 space-y-3 font-mono text-xs">
                  <div className="text-xs font-bold uppercase tracking-wider theme-accent-text">
                    Real-Time Commercial Contract Quote
                  </div>
                  <div className="flex justify-between py-1.5 border-b border-slate-500/20">
                    <span className="theme-text-muted">&Agrave; La Carte List Value:</span>
                    <span>
                      {INR}
                      {quote.listAnnual.toLocaleString("en-IN")}/yr
                    </span>
                  </div>
                  <div className="flex justify-between py-1.5 border-b border-slate-500/20 text-emerald-500">
                    <span>Bundle Discount Saved:</span>
                    <span>
                      -{INR}
                      {quote.saved.toLocaleString("en-IN")}/yr
                    </span>
                  </div>
                  <div className="flex justify-between py-1.5 border-b border-slate-500/20 text-base font-black theme-accent-text">
                    <span>Invoice Per Cycle:</span>
                    <span>
                      {INR}
                      {quote.finalFeeInr.toLocaleString("en-IN")} {quote.suffix}
                    </span>
                  </div>
                  <div className="flex justify-between pt-1 text-[11px] theme-text-muted">
                    <span>Normalized Annual ARR Booked:</span>
                    <span className="font-bold">
                      {INR}
                      {quote.normArr.toLocaleString("en-IN")}/yr
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* =============================================================== */}
        {/* VIEW 4: STAGED CANARY & ALEMBIC SCHEMA ROLLOUT                  */}
        {/* =============================================================== */}
        {activeView === "canary-migrations" && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h1 className="text-2xl font-black tracking-tight">
                  2-Stage Canary &amp; Fleet Schema Pipeline
                </h1>
                <p className="text-xs theme-text-muted">
                  Verify migrations on <span className="font-mono font-bold">hms_default</span> first, then push verified Alembic head across the hospital fleet.
                </p>
              </div>
              <button
                onClick={async () => {
                  try {
                    const d = await request(
                      "/api/tenants/sync-fleet",
                      {
                        method: "POST",
                        body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                      },
                      "sync-all-fleet"
                    );
                    notify(`Stage 2 Complete: Synced ${d.synced_tenants_count} hospitals.`);
                    await syncAll(true);
                  } catch (err) {
                    notify(err.message, "err");
                  }
                }}
                disabled={busyKey === "sync-all-fleet"}
                className="theme-accent-bg px-4 py-2.5 rounded-xl text-xs font-black flex items-center gap-2 shadow-lg"
              >
                {busyKey === "sync-all-fleet" && <Loader2 className="w-4 h-4 animate-spin" />}
                <span>Stage 2: Push Verified Head to Entire Fleet</span>
              </button>
            </div>

            <div className="theme-panel rounded-2xl overflow-hidden text-xs font-mono">
              <table className="w-full text-left">
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
                  {(schemaParity?.parity || []).map((p) => (
                    <tr key={p.subdomain_slug} className="border-b border-slate-500/10 theme-row-hover">
                      <td className="py-3 px-4 font-bold">
                        {p.subdomain_slug}{" "}
                        {p.subdomain_slug === "default" && (
                          <span className="ml-1.5 px-2 py-0.5 rounded text-[9px] theme-accent-bg">
                            CANARY
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4">{p.db_name}</td>
                      <td className="py-3 px-4 theme-accent-text">{p.schema_version}</td>
                      <td className="py-3 px-4 theme-text-muted">{p.head_revision}</td>
                      <td className="py-3 px-4">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            p.status === "UP_TO_DATE"
                              ? "bg-emerald-500/20 text-emerald-500"
                              : "bg-amber-500/20 text-amber-500"
                          }`}
                        >
                          {p.status}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          onClick={async () => {
                            try {
                              const d = await request(
                                `/api/tenants/${p.subdomain_slug}/sync`,
                                {
                                  method: "POST",
                                  body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                                },
                                `sync-${p.subdomain_slug}`
                              );
                              notify(d.message);
                              await syncAll(true);
                            } catch (err) {
                              notify(err.message, "err");
                            }
                          }}
                          className="theme-elevated hover:opacity-80 px-3 py-1.5 rounded-lg font-bold"
                        >
                          Upgrade &amp; Sync Tenant
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* =============================================================== */}
        {/* VIEW 5: ZERO-KNOWLEDGE FEDERATION MESH & MPI VERIFIER           */}
        {/* =============================================================== */}
        {activeView === "federation-mesh" && (
          <div className="space-y-6">
            <div>
              <h1 className="text-2xl font-black tracking-tight">
                Zero-Knowledge Federation Mesh &amp; Sovereignty
              </h1>
              <p className="text-xs theme-text-muted">
                Verify SHA-256 patient routing pointers across hospitals without exposing clinical PII.
              </p>
            </div>

            <div className="theme-panel rounded-2xl p-6 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="text-xs font-bold uppercase tracking-wider theme-accent-text">
                  Live SHA-256 MPI Pointer Locator
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={mpiQuery}
                    onChange={(e) => setMpiQuery(e.target.value)}
                    placeholder="Phone, ABHA-..., or MRN-..."
                    className="theme-input rounded-xl px-3.5 py-2 text-xs font-mono w-64"
                  />
                  <button
                    onClick={async () => {
                      if (!mpiQuery.trim()) return;
                      try {
                        const d = await request(
                          `/api/mpi/lookup?query=${encodeURIComponent(mpiQuery.trim())}`,
                          {},
                          "mpi-verify"
                        );
                        setMpiResult(d);
                      } catch (err) {
                        notify(err.message, "err");
                      }
                    }}
                    className="theme-accent-bg px-4 py-2 rounded-xl text-xs font-black"
                  >
                    Search Pointer Index
                  </button>
                </div>
              </div>
              {mpiResult && (
                <div className="font-mono text-xs space-y-2 pt-2">
                  <div>
                    Matches: <b>{mpiResult.matches_count}</b> &bull; SHA-256 Hash:{" "}
                    <span className="theme-accent-text">
                      {mpiResult.computed_phone_sha256 || "N/A"}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {(mpiResult.pointers || []).map((p) => (
                      <div
                        key={p.id}
                        className="theme-elevated p-3 rounded-xl flex items-center justify-between"
                      >
                        <div>
                          <div className="font-bold">
                            {p.branch_code} ({p.subdomain_slug}) &bull; {p.source_mrn}
                          </div>
                          <div className="text-[10px] theme-text-muted">
                            ABHA: {p.abha_number || "None"}
                          </div>
                        </div>
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-500">
                          {p.has_critical_allergies ? "CRITICAL ALLERGY" : "INDEXED"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {topology.map((n) => (
                <div key={n.branch_code} className="theme-panel rounded-2xl p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-black text-sm">{n.branch_code}</span>
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold theme-elevated theme-accent-text">
                      {n.federation_sharing_mode}
                    </span>
                  </div>
                  <div className="text-xs font-bold">{n.hospital_name}</div>
                  <div className="flex justify-between text-xs font-mono theme-text-muted pt-2 border-t border-slate-500/20">
                    <span>Key: {n.api_key_prefix}...</span>
                    <span className="font-bold text-emerald-500">
                      {n.indexed_pointers_count} Pointers
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* =============================================================== */}
        {/* VIEW 6: FORENSIC AUDIT LEDGER                                   */}
        {/* =============================================================== */}
        {activeView === "audit-vault" && (
          <div className="space-y-4">
            <div>
              <h1 className="text-2xl font-black tracking-tight">
                Immutable Control Plane Forensic Ledger
              </h1>
              <p className="text-xs theme-text-muted">
                Every provisioning, contract modification, key rotation, and snapshot restore.
              </p>
            </div>
            <div className="theme-panel rounded-2xl overflow-hidden text-xs font-mono">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-slate-500/20 text-[11px] uppercase theme-text-muted">
                    <th className="py-3 px-4">ID</th>
                    <th className="py-3 px-4">Action</th>
                    <th className="py-3 px-4">Tenant</th>
                    <th className="py-3 px-4">Details</th>
                    <th className="py-3 px-4">Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLogs.map((l) => (
                    <tr key={l.id} className="border-b border-slate-500/10 theme-row-hover">
                      <td className="py-2.5 px-4 theme-text-muted">#{l.id}</td>
                      <td className="py-2.5 px-4 font-bold theme-accent-text">{l.action}</td>
                      <td className="py-2.5 px-4 font-bold">{l.target_slug}</td>
                      <td className="py-2.5 px-4 font-sans">{l.details}</td>
                      <td className="py-2.5 px-4 theme-text-muted">{l.created_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* DOCKABLE BOTTOM DEVTOOLS API CONSOLE */}
        {devtoolsOpen && (
          <div className="theme-panel rounded-2xl p-4 space-y-2 border-2">
            <div className="flex items-center justify-between text-xs font-mono font-bold">
              <span className="theme-accent-text">
                LIVE CONTROL PLANE API INSPECTOR ({apiLogs.length} calls)
              </span>
              <button onClick={() => setDevtoolsOpen(false)}>Close &times;</button>
            </div>
            <div className="max-h-52 overflow-y-auto space-y-1.5 font-mono text-[11px]">
              {apiLogs.map((l) => (
                <details key={l.id} className="theme-elevated p-2 rounded-lg">
                  <summary className="cursor-pointer flex justify-between">
                    <span>
                      <b>
                        {l.method} {l.status}
                      </b>{" "}
                      {l.path}
                    </span>
                    <span className="theme-accent-text">
                      {l.ms}ms &bull; {l.ts}
                    </span>
                  </summary>
                  <pre className="mt-2 p-2 theme-panel rounded overflow-x-auto text-[10px]">
                    {JSON.stringify(l.body, null, 2)}
                  </pre>
                </details>
              ))}
            </div>
          </div>
        )}
      </main>

      {/* ================================================================= */}
      {/* COMPLETE SLIDE-OVER HOSPITAL COMMAND DRAWER                       */}
      {/* (1. Telemetry, 2. Auto-Priced Contract + Key Override,            */}
      {/*  3. Snapshot Vault, 5. Key Rotation / Break-Glass / Circuit Brk)  */}
      {/* ================================================================= */}
      {selectedTenant && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex justify-end">
          <div className="w-full max-w-3xl theme-panel h-full overflow-y-auto no-scrollbar p-6 space-y-6 border-l">
            {/* Drawer Header */}
            <div className="flex items-start justify-between border-b border-slate-500/20 pb-4">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-xl font-black">{selectedTenant.hospital_name}</h2>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                      !selectedTenant.is_active
                        ? "bg-rose-500/20 text-rose-500"
                        : selectedTenant.maintenance_mode
                        ? "bg-amber-500/20 text-amber-500"
                        : "bg-emerald-500/20 text-emerald-500"
                    }`}
                  >
                    {!selectedTenant.is_active
                      ? "SUSPENDED"
                      : selectedTenant.maintenance_mode
                      ? "MAINTENANCE"
                      : "ACTIVE"}
                  </span>
                  <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold theme-elevated theme-accent-text">
                    MPI: {selectedTenant.hospital_policy?.federation_sharing_mode || "FULL_MESH"}
                  </span>
                </div>
                <p className="text-xs font-mono theme-text-muted mt-1">
                  Subdomain: {selectedTenant.subdomain_slug} &bull; Branch:{" "}
                  {selectedTenant.branch_code} &bull; DB: {selectedTenant.db_name} &bull; Admin:{" "}
                  {selectedTenant.admin_email}
                </p>
              </div>
              <button
                onClick={() => setSelectedTenant(null)}
                className="p-2 rounded-lg theme-elevated"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 1. Live Data-Plane Telemetry & 1-Click Demo Activity Generator */}
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs font-bold uppercase tracking-wider theme-accent-text">
                  1. Live Hospital Data-Plane Telemetry (From {selectedTenant.db_name})
                </div>
                <button
                  onClick={async () => {
                    try {
                      const d = await request(
                        `/api/tenants/${selectedTenant.subdomain_slug}/seed-demo-activity`,
                        {
                          method: "POST",
                          body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                        },
                        "seed-demo"
                      );
                      notify(d.message);
                      await inspectHospital(selectedTenant.subdomain_slug);
                      await syncAll(true);
                    } catch (err) {
                      notify(err.message, "err");
                    }
                  }}
                  disabled={busyKey === "seed-demo"}
                  className="px-3 py-1 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-500 font-bold text-[11px] flex items-center gap-1.5"
                >
                  {busyKey === "seed-demo" && <Loader2 className="w-3 h-3 animate-spin" />}
                  <span>+ Simulate {INR}2,500 Patient Bill &amp; Encounter</span>
                </button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
                <div className="theme-elevated p-3.5 rounded-xl">
                  <div className="text-[10px] theme-text-muted uppercase">Staff Users</div>
                  <div className="text-lg font-black mt-0.5">
                    {selectedTenant.metrics?.users_count} / {drwForm.max_users}
                  </div>
                </div>
                <div className="theme-elevated p-3.5 rounded-xl">
                  <div className="text-[10px] theme-text-muted uppercase">Beds (Occ / Cap)</div>
                  <div className="text-lg font-black theme-accent-text mt-0.5">
                    {selectedTenant.metrics?.occupied_beds_count} /{" "}
                    {selectedTenant.metrics?.total_beds_count} ({drwForm.max_beds})
                  </div>
                </div>
                <div className="theme-elevated p-3.5 rounded-xl">
                  <div className="text-[10px] theme-text-muted uppercase">MPI Patients</div>
                  <div className="text-lg font-black mt-0.5">
                    {selectedTenant.metrics?.patients_count}
                  </div>
                </div>
                <div className="theme-elevated p-3.5 rounded-xl">
                  <div className="text-[10px] theme-text-muted uppercase">Active Encounters</div>
                  <div className="text-lg font-black text-emerald-500 mt-0.5">
                    {selectedTenant.metrics?.active_encounters_count}
                  </div>
                </div>
                <div className="theme-elevated p-3.5 rounded-xl col-span-2">
                  <div className="text-[10px] theme-text-muted uppercase">
                    Hospital Patient Gross Billed
                  </div>
                  <div className="text-lg font-black mt-0.5">
                    {fmtInr(selectedTenant.metrics?.gross_revenue_paise)}
                  </div>
                  <div className="text-[10px] theme-text-muted mt-0.5">
                    Auto-updates when patients are billed in Hospital EMR
                  </div>
                </div>
                <div className="theme-elevated p-3.5 rounded-xl col-span-2">
                  <div className="text-[10px] theme-text-muted uppercase">
                    Hospital Patient Collected (Paid)
                  </div>
                  <div className="text-lg font-black text-emerald-500 mt-0.5">
                    {fmtInr(selectedTenant.metrics?.collected_revenue_paise)}
                  </div>
                  <div className="text-[10px] theme-text-muted mt-0.5">
                    Settled via POS UPI / Card / Cash / Insurance
                  </div>
                </div>
              </div>
            </div>

            {/* 2. Commercial Package, Auto-Pricing, Key-Locked Override & Maintenance Freeze */}
            <div className="theme-elevated p-5 rounded-2xl space-y-4 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <span className="font-bold uppercase tracking-wider theme-accent-text">
                    2. Commercial Package, Module Licensing &amp; Quotas
                  </span>
                  <p className="text-[11px] theme-text-muted">
                    Switching packages automatically selects its modules and recalculates the tier price.
                  </p>
                </div>
                <button
                  onClick={async () => {
                    try {
                      // CRITICAL FIX: Only send base_license_fee_paise if customFeeUnlocked is True!
                      const overridePaise =
                        drwForm.customFeeUnlocked && Number(drwForm.custom_fee_inr) > 0
                          ? Number(drwForm.custom_fee_inr) * 100
                          : null;
                      await request(
                        `/api/tenants/${selectedTenant.subdomain_slug}/entitlements`,
                        {
                          method: "PATCH",
                          body: JSON.stringify({
                            master_setup_key: masterKey.trim(),
                            pricing_model: drwForm.pricing_model,
                            billing_cycle: drwForm.billing_cycle,
                            base_license_fee_paise: overridePaise,
                            enabled_modules: drwForm.enabled_modules,
                            quota_limits: {
                              max_beds: Number(drwForm.max_beds) || 0,
                              max_users: Number(drwForm.max_users) || 200,
                            },
                            maintenance_mode: drwForm.maintenance_mode,
                            maintenance_notice: drwForm.maintenance_notice || null,
                          }),
                        },
                        "save-ent"
                      );
                      notify(
                        `Updated package (${drwForm.pricing_model}) & recalculated contract fee!`
                      );
                      await inspectHospital(selectedTenant.subdomain_slug);
                      await syncAll(true);
                    } catch (err) {
                      notify(err.message, "err");
                    }
                  }}
                  disabled={busyKey === "save-ent"}
                  className="theme-accent-bg px-4 py-2 rounded-xl font-black flex items-center gap-1.5 shadow"
                >
                  {busyKey === "save-ent" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>Save Package &amp; Quotas</span>
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                <div>
                  <label className="block text-[10px] uppercase theme-text-muted mb-1">
                    Pricing Model
                  </label>
                  <select
                    value={drwForm.pricing_model}
                    onChange={(e) => handleDrawerPackageChange(e.target.value)}
                    className="w-full theme-input rounded-lg p-2"
                  >
                    {BUNDLES.map((b) => (
                      <option key={b.code} value={b.code}>
                        {b.code}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] uppercase theme-text-muted mb-1">
                    Billing Cycle
                  </label>
                  <select
                    value={drwForm.billing_cycle}
                    onChange={(e) =>
                      setDrwForm((p) => ({
                        ...p,
                        billing_cycle: e.target.value,
                        customFeeUnlocked: false,
                      }))
                    }
                    className="w-full theme-input rounded-lg p-2"
                  >
                    <option value="ANNUAL_LICENSE">ANNUAL_LICENSE</option>
                    <option value="MONTHLY_SUBSCRIPTION">MONTHLY_SUBSCRIPTION</option>
                    <option value="PER_ACTIVE_BED_MONTHLY">PER_ACTIVE_BED_MONTHLY</option>
                    <option value="ONE_TIME_PERPETUAL">ONE_TIME_PERPETUAL</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] uppercase theme-text-muted mb-1">
                    Max Beds Quota
                  </label>
                  <input
                    type="number"
                    value={drwForm.max_beds}
                    onChange={(e) => setDrwForm((p) => ({ ...p, max_beds: e.target.value }))}
                    className="w-full theme-input rounded-lg p-2 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase theme-text-muted mb-1">
                    Max Users Quota
                  </label>
                  <input
                    type="number"
                    value={drwForm.max_users}
                    onChange={(e) => setDrwForm((p) => ({ ...p, max_users: e.target.value }))}
                    className="w-full theme-input rounded-lg p-2 font-mono"
                  />
                </div>
              </div>

              {/* Key-Locked Custom Fee Override */}
              <KeyGatedFeeOverride
                autoFeeInr={drawerQuote.autoCycleFee}
                suffix={drawerQuote.suffix}
                isUnlocked={drwForm.customFeeUnlocked}
                onUnlockChange={(v) => setDrwForm((p) => ({ ...p, customFeeUnlocked: v }))}
                customFeeInr={drwForm.custom_fee_inr}
                onCustomFeeChange={(val) => setDrwForm((p) => ({ ...p, custom_fee_inr: val }))}
                masterKey={masterKey}
                notify={notify}
              />

              {/* Module Checkboxes */}
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {MODULE_LIST.map((m) => (
                  <label
                    key={m.code}
                    className="flex items-center gap-1.5 theme-panel p-2 rounded-lg cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={drwForm.enabled_modules[m.code] !== false}
                      onChange={(e) =>
                        setDrwForm((p) => ({
                          ...p,
                          pricing_model: "CUSTOM_A_LA_CARTE",
                          customFeeUnlocked: false,
                          enabled_modules: {
                            ...p.enabled_modules,
                            [m.code]: e.target.checked,
                          },
                        }))
                      }
                    />
                    <span className="text-[11px]">
                      {m.name} ({INR}
                      {m.priceInr / 1000}k)
                    </span>
                  </label>
                ))}
              </div>

              {/* Read-Only Maintenance Window Freeze */}
              <div className="pt-3 border-t border-slate-500/20 flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 font-bold text-amber-500 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={drwForm.maintenance_mode}
                    onChange={(e) =>
                      setDrwForm((p) => ({ ...p, maintenance_mode: e.target.checked }))
                    }
                  />
                  <span>Read-Only Maintenance Freeze (Blocks Writes with HTTP 503)</span>
                </label>
                <input
                  type="text"
                  value={drwForm.maintenance_notice}
                  onChange={(e) =>
                    setDrwForm((p) => ({ ...p, maintenance_notice: e.target.value }))
                  }
                  placeholder="Optional maintenance notice banner..."
                  className="flex-1 theme-input rounded-lg px-3 py-1.5"
                />
              </div>
            </div>

            {/* 3. AES-256 Catastrophic Backup Vault */}
            <div className="theme-elevated p-5 rounded-2xl space-y-3 text-xs">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-bold uppercase tracking-wider text-emerald-500">
                    3. AES-256 Snapshot Vault ({selectedTenant.snapshots?.length || 0})
                  </span>
                  <p className="text-[11px] theme-text-muted">
                    Take an immediate encrypted pg_dump snapshot or restore any point-in-time backup.
                  </p>
                </div>
                <button
                  onClick={async () => {
                    try {
                      const d = await request(
                        `/api/tenants/${selectedTenant.subdomain_slug}/backups/trigger`,
                        {
                          method: "POST",
                          body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                        },
                        "snap-trig"
                      );
                      notify(d.message);
                      await inspectHospital(selectedTenant.subdomain_slug);
                      await syncAll(true);
                    } catch (err) {
                      notify(err.message, "err");
                    }
                  }}
                  disabled={busyKey === "snap-trig"}
                  className="bg-emerald-600 hover:bg-emerald-500 text-white px-3.5 py-1.5 rounded-lg font-bold flex items-center gap-1.5"
                >
                  {busyKey === "snap-trig" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>+ Take Snapshot Now</span>
                </button>
              </div>
              <div className="space-y-2 max-h-40 overflow-y-auto font-mono">
                {(selectedTenant.snapshots || []).map((s) => (
                  <div
                    key={s.filename}
                    className="theme-panel p-2.5 rounded-xl flex items-center justify-between"
                  >
                    <div>
                      <div className="text-[11px] font-bold">{s.filename}</div>
                      <div className="text-[10px] theme-text-muted">
                        {(s.size_bytes / 1024).toFixed(1)} KB &bull;{" "}
                        {new Date(s.created_at * 1000).toLocaleString()}
                      </div>
                    </div>
                    <button
                      onClick={async () => {
                        if (!window.confirm(`Restore ${s.filename} into ${selectedTenant.db_name}?`))
                          return;
                        try {
                          const d = await request(
                            `/api/tenants/${selectedTenant.subdomain_slug}/backups/${encodeURIComponent(
                              s.filename
                            )}/restore`,
                            {
                              method: "POST",
                              body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                            },
                            `rest-${s.filename}`
                          );
                          notify(d.message);
                        } catch (err) {
                          notify(err.message, "err");
                        }
                      }}
                      className="px-3 py-1 rounded-lg bg-amber-500/20 text-amber-500 font-bold text-[10px]"
                    >
                      Restore into DB
                    </button>
                  </div>
                ))}
              </div>
            </div>
            {/* 4. VISUAL ETL DATA MIGRATION STUDIO */}
            <div className="theme-elevated p-5 rounded-2xl space-y-3 text-xs">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-bold uppercase tracking-wider text-cyan-400">
                    4. Legacy Data Migration Studio (Visual ETL)
                  </span>
                  <p className="text-[11px] theme-text-muted mt-1">
                    Upload legacy CSV extracts, visually map columns to the Avinya schema, and securely bulk-insert records.
                  </p>
                </div>
              </div>
              <div className="pt-2">
                <MigrationStudio 
                  masterKey={masterKey} 
                  tenantSlug={selectedTenant.subdomain_slug} 
                />
              </div>
            </div>
            
            {/* 5. COMPLETE ZERO-TRUST SECURITY, KEY ROTATION, SANDBOX CLONE & CIRCUIT BREAKER */}
            <div className="theme-elevated p-5 rounded-2xl space-y-4 text-xs">
              <div className="font-bold uppercase tracking-wider text-rose-500">
                5. Zero-Trust Cryptographic Key Rotation, Break-Glass &amp; Circuit-Breaker
              </div>

              {/* Granular Key Rotation Box */}
              <div className="theme-panel p-3.5 rounded-xl space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-bold">Granular Cryptographic Key Rotation</div>
                  <button
                    onClick={async () => {
                      if (
                        !window.confirm(
                          `Rotate selected cryptographic keys for '${selectedTenant.subdomain_slug}'?`
                        )
                      )
                        return;
                      try {
                        const d = await request(
                          `/api/tenants/${selectedTenant.subdomain_slug}/rotate-keys`,
                          {
                            method: "POST",
                            body: JSON.stringify({
                              master_setup_key: masterKey.trim(),
                              rotate_jwt_secret: drwForm.rot_jwt,
                              rotate_backup_key: drwForm.rot_backup,
                              rotate_federation_key: drwForm.rot_fed,
                            }),
                          },
                          "rot-keys"
                        );
                        setDrawerOutput(d);
                        notify("Rotated selected cryptographic keys and sealed via AES-256-GCM.");
                        await syncAll(true);
                      } catch (err) {
                        notify(err.message, "err");
                      }
                    }}
                    disabled={busyKey === "rot-keys"}
                    className="theme-accent-bg px-3.5 py-1.5 rounded-lg font-bold flex items-center gap-1.5"
                  >
                    {busyKey === "rot-keys" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    <span>Rotate Selected Keys</span>
                  </button>
                </div>
                <div className="flex flex-wrap gap-4 text-[11px]">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={drwForm.rot_jwt}
                      onChange={(e) => setDrwForm((p) => ({ ...p, rot_jwt: e.target.checked }))}
                    />
                    <span>JWT Signing Secret</span>
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={drwForm.rot_backup}
                      onChange={(e) => setDrwForm((p) => ({ ...p, rot_backup: e.target.checked }))}
                    />
                    <span>AES-256 Backup Key</span>
                  </label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={drwForm.rot_fed}
                      onChange={(e) => setDrwForm((p) => ({ ...p, rot_fed: e.target.checked }))}
                    />
                    <span>Federation API Key (fed_live_...)</span>
                  </label>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {/* Break-Glass Admin Password Reset */}
                <button
                  onClick={async () => {
                    try {
                      const d = await request(
                        `/api/tenants/${selectedTenant.subdomain_slug}/reset-admin-password`,
                        {
                          method: "POST",
                          body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                        },
                        "reset-pwd"
                      );
                      setDrawerOutput(d);
                      notify("Break-glass superuser unlocked with new 1-time password.");
                    } catch (err) {
                      notify(err.message, "err");
                    }
                  }}
                  className="theme-panel p-3.5 rounded-xl text-left hover:opacity-90"
                >
                  <div className="font-bold text-amber-500">
                    Break-Glass Admin Password Reset
                  </div>
                  <div className="text-[10px] theme-text-muted mt-0.5">
                    Unlocks superuser account and generates a 1-time recovery password.
                  </div>
                </button>

                {/* Deployment Mode Switcher (CLOUD / ON_PREM) */}
                <div className="theme-panel p-3.5 rounded-xl flex items-center justify-between gap-2">
                  <div>
                    <div className="font-bold">Deployment Routing Mode</div>
                    <div className="text-[10px] theme-text-muted">
                      Current: {selectedTenant.deployment_mode}
                    </div>
                  </div>
                  <button
                    onClick={async () => {
                      const nextMode =
                        selectedTenant.deployment_mode === "CLOUD" ? "ON_PREM" : "CLOUD";
                      try {
                        const d = await request(
                          `/api/tenants/${selectedTenant.subdomain_slug}/deployment-mode`,
                          {
                            method: "PATCH",
                            body: JSON.stringify({
                              master_setup_key: masterKey.trim(),
                              deployment_mode: nextMode,
                            }),
                          },
                          "switch-dep"
                        );
                        notify(`Switched deployment mode to ${d.deployment_mode}`);
                        await inspectHospital(selectedTenant.subdomain_slug);
                        await syncAll(true);
                      } catch (err) {
                        notify(err.message, "err");
                      }
                    }}
                    className="theme-elevated px-3 py-1.5 rounded-lg font-mono font-bold text-[11px]"
                  >
                    Switch to {selectedTenant.deployment_mode === "CLOUD" ? "ON_PREM" : "CLOUD"}
                  </button>
                </div>

                {/* UAT Sandbox Cloner */}
                <div className="theme-panel p-3.5 rounded-xl flex items-center gap-2">
                  <input
                    type="text"
                    value={drwForm.sandbox_slug}
                    onChange={(e) => setDrwForm((p) => ({ ...p, sandbox_slug: e.target.value }))}
                    placeholder="sandbox-slug"
                    className="theme-input rounded-lg px-2.5 py-1.5 font-mono text-xs flex-1"
                  />
                  <button
                    onClick={async () => {
                      try {
                        const d = await request(
                          `/api/tenants/${selectedTenant.subdomain_slug}/clone-sandbox`,
                          {
                            method: "POST",
                            body: JSON.stringify({
                              master_setup_key: masterKey.trim(),
                              sandbox_subdomain_slug: drwForm.sandbox_slug.trim().toLowerCase(),
                            }),
                          },
                          "clone-sbx"
                        );
                        setDrawerOutput(d);
                        notify(`Cloned UAT sandbox '${d.sandbox_subdomain_slug}'!`);
                        await syncAll(true);
                      } catch (err) {
                        notify(err.message, "err");
                      }
                    }}
                    className="theme-accent-bg px-3 py-1.5 rounded-lg font-bold"
                  >
                    Clone UAT
                  </button>
                </div>

                {/* Tenant Circuit-Breaker (Suspend / Reactivate) */}
                <button
                  onClick={async () => {
                    try {
                      const d = await request(
                        `/api/tenants/${selectedTenant.subdomain_slug}/status`,
                        {
                          method: "PATCH",
                          body: JSON.stringify({
                            master_setup_key: masterKey.trim(),
                            is_active: !selectedTenant.is_active,
                          }),
                        },
                        "toggle-cb"
                      );
                      notify(d.message);
                      await inspectHospital(selectedTenant.subdomain_slug);
                      await syncAll(true);
                    } catch (err) {
                      notify(err.message, "err");
                    }
                  }}
                  className={`p-3.5 rounded-xl border text-left font-bold ${
                    selectedTenant.is_active
                      ? "bg-rose-500/15 border-rose-500/40 text-rose-500"
                      : "bg-emerald-500/15 border-emerald-500/40 text-emerald-500"
                  }`}
                >
                  <div>
                    {selectedTenant.is_active
                      ? "Suspend Tenant (Circuit-Breaker)"
                      : "Reactivate Hospital Tenant"}
                  </div>
                  <div className="text-[10px] opacity-80 mt-0.5">
                    {selectedTenant.is_active
                      ? "Immediately blocks all API & Federation traffic with HTTP 403."
                      : "Restores normal API & Federation access immediately."}
                  </div>
                </button>
              </div>

              {/* Action Output Vault (Displays Rotated Keys / Break-Glass Password) */}
              {drawerOutput && (
                <div className="theme-panel p-4 rounded-xl border-2 border-emerald-500/40 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-emerald-500 uppercase text-[11px]">
                      Security Operation Output (Copy Now)
                    </span>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(JSON.stringify(drawerOutput, null, 2));
                        notify("Copied security output to clipboard.");
                      }}
                      className="px-2 py-0.5 rounded theme-elevated text-[10px] font-bold"
                    >
                      Copy JSON
                    </button>
                  </div>
                  <pre className="font-mono text-[11px] overflow-x-auto">
                    {JSON.stringify(drawerOutput, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}