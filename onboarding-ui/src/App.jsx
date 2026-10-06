import React, { useState, useEffect, useMemo, useCallback } from "react";
import {
  Activity,
  AlertTriangle,
  Archive,
  ArrowUpRight,
  BarChart3,
  Building2,
  CheckCircle2,
  Code2,
  Copy,
  Cpu,
  Database,
  Download,
  ExternalLink,
  FileSpreadsheet,
  GitBranch,
  Globe,
  KeyRound,
  Layers,
  Loader2,
  Lock,
  Network,
  Play,
  PlusCircle,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  Unlock,
  Users,
  Wrench,
  X,
  Zap,
} from "lucide-react";

const INR = "\u20B9";
const fmtInr = (paise) =>
  `${INR}${Math.round((paise || 0) / 100).toLocaleString("en-IN")}`;

const DEFAULT_MODULES = [
  { code: "opd", name: "OPD & Queue TV", base_annual_price_paise: 2500000 },
  { code: "ipd_adt", name: "IPD & Spatial ADT", base_annual_price_paise: 4000000 },
  { code: "pharmacy", name: "Pharmacy & FEFO", base_annual_price_paise: 3000000 },
  { code: "diagnostics", name: "LIS Laboratory", base_annual_price_paise: 3500000 },
  { code: "pacs_radiology", name: "Radiology PACS/DICOM", base_annual_price_paise: 4500000 },
  { code: "billing", name: "POS & Invoicing", base_annual_price_paise: 2500000 },
  { code: "insurance_tpa", name: "TPA & Cashless Claims", base_annual_price_paise: 4000000 },
  { code: "ot_surgery", name: "Modular OT & Surgery", base_annual_price_paise: 4500000 },
  { code: "blood_bank", name: "Blood Bank Safety", base_annual_price_paise: 3500000 },
  { code: "er_mlc", name: "ER Triage & MLC", base_annual_price_paise: 3000000 },
  { code: "executive_bi", name: "CFO Executive BI", base_annual_price_paise: 5000000 },
];

const BUNDLES = [
  {
    code: "BUNDLE_TOTAL_HOSPITAL",
    name: "Total Hospital Flagship",
    priceInr: 295000,
    desc: "All 11 clinical, surgical & BI modules",
    modules: DEFAULT_MODULES.map((m) => m.code),
  },
  {
    code: "BUNDLE_SURGICAL_SUITE",
    name: "Surgical & Inpatient Pack",
    priceInr: 145000,
    desc: "IPD, Modular OT, Pharmacy, Billing & TPA",
    modules: ["ipd_adt", "ot_surgery", "pharmacy", "billing", "insurance_tpa"],
  },
  {
    code: "BUNDLE_DIAGNOSTICS_HUB",
    name: "Diagnostics & PACS Hub",
    priceInr: 110000,
    desc: "LIS Lab, PACS DICOM, Pharmacy & Billing",
    modules: ["diagnostics", "pacs_radiology", "billing", "pharmacy"],
  },
  {
    code: "BUNDLE_OPD_DAYCARE",
    name: "OPD & Daycare Bundle",
    priceInr: 65000,
    desc: "OPD Queue, Pharmacy & POS Billing",
    modules: ["opd", "pharmacy", "billing"],
  },
  {
    code: "CUSTOM_A_LA_CARTE",
    name: "Custom \u00C0 La Carte",
    priceInr: null,
    desc: "Hand-pick individual hospital modules",
    modules: [],
  },
];

const DEFAULT_BLUEPRINTS = [
  {
    key: "TIER_1_QUATERNARY_500_BED",
    name: "500-Bed Quaternary Flagship",
    description: "2 Floors: ER Trauma Bay (10 beds), Medical ICU (8 beds), Surgical Ward (4 beds) + 2 Modular OTs.",
    badge: "22 Beds + 2 OTs",
  },
  {
    key: "BOUTIQUE_MATERNITY_NICU",
    name: "Maternity, Fertility & NICU",
    description: "LDR Deluxe Suite (1 bed) + Level III NICU (6 Pediatric beds) + Dedicated OB-GYN OT.",
    badge: "7 Beds + 1 OT",
  },
  {
    key: "TRAUMA_EMERGENCY_HUB",
    name: "Acute Trauma & ER Response",
    description: "6 Red Resuscitation ICU Bays + 24/7 Ortho-Trauma Surgical Theatre.",
    badge: "6 ICU Beds + 1 OT",
  },
  {
    key: "POLYCLINIC_DAYCARE_OPD",
    name: "Polyclinic & Daycare Center",
    description: "High-throughput Outpatient Consulting + 4 Daycare Observation Beds.",
    badge: "4 Daycare Beds",
  },
  {
    key: "BLANK_CANVAS",
    name: "Blank Canvas (Zero Pre-Built Beds / 100% Custom Admin Setup)",
    description: "Seeds RBAC roles, departments & 10 print templates with 0 dummy floors so Hospital Admin builds their exact layout.",
    badge: "0 Pre-Built Beds",
  },
];

function SkeletonCard() {
  return (
    <div className="rounded-2xl bg-slate-900/70 border border-slate-800/80 p-5 animate-pulse space-y-4">
      <div className="flex justify-between">
        <div className="h-4 w-40 bg-slate-800 rounded" />
        <div className="h-4 w-16 bg-slate-800 rounded-full" />
      </div>
      <div className="grid grid-cols-2 gap-2 pt-2">
        <div className="h-10 bg-slate-800/70 rounded-xl" />
        <div className="h-10 bg-slate-800/70 rounded-xl" />
      </div>
      <div className="h-8 bg-slate-800/50 rounded-xl" />
    </div>
  );
}

export default function App() {
  // Authentication & Global State
  const [masterKey, setMasterKey] = useState(
    () => localStorage.getItem("avinya_master_key") || "master_vendor_setup_key_2026_secure"
  );
  const [cpSecret, setCpSecret] = useState(
    () => localStorage.getItem("avinya_cp_secret") || ""
  );
  const [activeTab, setActiveTab] = useState("fleet");
  const [isLoadingFleet, setIsLoadingFleet] = useState(false);
  const [busyAction, setBusyAction] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [toast, setToast] = useState(null);

  // Data Caches
  const [overview, setOverview] = useState(null);
  const [tenants, setTenants] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [schemaParity, setSchemaParity] = useState(null);
  const [topology, setTopology] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [blueprints, setBlueprints] = useState(DEFAULT_BLUEPRINTS);
  const [moduleCatalog, setModuleCatalog] = useState(DEFAULT_MODULES);

  // API Inspector State (tracks every API request/response for developer transparency)
  const [apiLogs, setApiLogs] = useState([]);
  const [showInspector, setShowInspector] = useState(false);

  // Fleet Filtering
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  // Command Palette (Ctrl+K)
  const [cmdOpen, setCmdOpen] = useState(false);
  const [cmdQuery, setCmdQuery] = useState("");

  // Slide-Over Tenant Drawer State
  const [drawerTenant, setDrawerTenant] = useState(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerOutput, setDrawerOutput] = useState(null);
  const [drwForm, setDrwForm] = useState({
    pricing_model: "BUNDLE_TOTAL_HOSPITAL",
    billing_cycle: "ANNUAL_LICENSE",
    custom_fee_inr: "",
    max_beds: 500,
    max_users: 200,
    maintenance_mode: false,
    maintenance_notice: "",
    enabled_modules: {},
    sandbox_slug: "",
  });

  // Tab 2: Onboarding Studio Form State
  const [provForm, setProvForm] = useState({
    hospital_name: "",
    subdomain_slug: "",
    branch_code: "",
    admin_email: "",
    blueprint_archetype: "TIER_1_QUATERNARY_500_BED",
    pricing_model: "BUNDLE_TOTAL_HOSPITAL",
    billing_cycle: "ANNUAL_LICENSE",
    custom_fee_inr: "",
    quota_max_beds: 500,
    quota_max_users: 200,
    federation_sharing_mode: "FULL_MESH",
    brand_primary_color: "#06b6d4",
    seed_icd10: true,
    seed_allergies: true,
    seed_complaints: true,
    selected_modules: DEFAULT_MODULES.map((m) => m.code),
  });
  const [provLogs, setProvLogs] = useState([
    { ts: "READY", msg: "Zero-touch provisioning engine online. Select a blueprint and commercial package.", type: "info" },
  ]);
  const [provResult, setProvResult] = useState(null);

  // Tab 4: Live MPI Pointer Diagnostic
  const [mpiQuery, setMpiQuery] = useState("");
  const [mpiResult, setMpiResult] = useState(null);

  const notify = useCallback((message, type = "success") => {
    setToast({ message, type, id: Date.now() });
  }, []);

  // Unified API Caller with Latency & Payload Telemetry
  const apiCall = useCallback(
    async (path, options = {}, actionKey = null) => {
      if (actionKey) setBusyAction(actionKey);
      const t0 = performance.now();
      const headers = {
        "X-Master-Setup-Key": masterKey.trim(),
        ...(cpSecret.trim() ? { "X-Control-Plane-Secret": cpSecret.trim() } : {}),
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      };

      try {
        const res = await fetch(path, { ...options, headers });
        const elapsedMs = Math.round(performance.now() - t0);
        const serverMs = res.headers.get("X-Process-Time-Ms");
        let data = null;
        try {
          data = await res.json();
        } catch {
          data = { raw: "Non-JSON response" };
        }

        const logEntry = {
          id: Date.now() + Math.random(),
          time: new Date().toLocaleTimeString(),
          method: options.method || "GET",
          path,
          status: res.status,
          ok: res.ok,
          latencyMs: serverMs ? `${serverMs}ms` : `${elapsedMs}ms`,
          requestBody: options.body ? JSON.parse(options.body) : null,
          responseBody: data,
        };
        setApiLogs((prev) => [logEntry, ...prev.slice(0, 29)]);

        if (!res.ok) {
          throw new Error(data?.detail || data?.message || `HTTP ${res.status} Error`);
        }
        return data;
      } finally {
        if (actionKey) setBusyAction(null);
      }
    },
    [masterKey, cpSecret]
  );

  // Sync Fleet, Overview & Analytics
  const syncFleetAll = useCallback(
    async (silent = false) => {
      if (!masterKey.trim()) {
        notify("Please enter your Master Vendor Key in the top command bar.", "error");
        return;
      }
      localStorage.setItem("avinya_master_key", masterKey.trim());
      localStorage.setItem("avinya_cp_secret", cpSecret.trim());
      if (!silent) setIsLoadingFleet(true);

      try {
        const [tenantsRes, overviewRes, analyticsRes] = await Promise.all([
          apiCall("/api/tenants"),
          apiCall("/api/fleet/overview"),
          apiCall("/api/fleet/analytics"),
        ]);
        setTenants(tenantsRes.items || []);
        setOverview(overviewRes);
        setAnalytics(analyticsRes);
        if (!silent) {
          notify(`Synced ${tenantsRes.items?.length || 0} hospital tenants & commercial telemetry.`);
        }
      } catch (err) {
        if (!silent) notify(err.message, "error");
      } finally {
        if (!silent) setIsLoadingFleet(false);
      }
    },
    [apiCall, masterKey, cpSecret, notify]
  );

  // Load initial public catalogs & fleet
  useEffect(() => {
    (async () => {
      try {
        const [bpRes, prcRes] = await Promise.all([
          fetch("/api/blueprints").then((r) => (r.ok ? r.json() : null)),
          fetch("/api/pricing/catalog").then((r) => (r.ok ? r.json() : null)),
        ]);
        if (bpRes?.blueprints?.length) {
          setBlueprints(
            bpRes.blueprints.map((b) => ({
              ...b,
              badge: `${b.floors_count} Floor(s) \u2022 ${b.theatres_count} OT(s)`,
            }))
          );
        }
        if (prcRes?.modules?.length) {
          setModuleCatalog(prcRes.modules);
        }
      } catch {
        // Fallback to built-in catalog constants
      }
      if (masterKey.trim()) {
        syncFleetAll(true);
      }
    })();
  }, []);

  // Auto-refresh interval
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => syncFleetAll(true), 15000);
    return () => clearInterval(id);
  }, [autoRefresh, syncFleetAll]);

  // Keyboard shortcut Ctrl+K / Cmd+K
  useEffect(() => {
    const onKeyDown = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
      } else if (e.key === "Escape") {
        setCmdOpen(false);
        setDrawerTenant(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Tab-specific loaders
  const loadSchemaParity = useCallback(async () => {
    try {
      const data = await apiCall("/api/fleet/schema-status", {}, "schema-load");
      setSchemaParity(data);
    } catch (err) {
      notify(err.message, "error");
    }
  }, [apiCall, notify]);

  const loadTopology = useCallback(async () => {
    try {
      const data = await apiCall("/api/federation/topology", {}, "topo-load");
      setTopology(data.nodes || []);
    } catch (err) {
      notify(err.message, "error");
    }
  }, [apiCall, notify]);

  const loadAuditTrail = useCallback(async () => {
    try {
      const data = await apiCall("/api/fleet/audit-logs?limit=100", {}, "audit-load");
      setAuditLogs(data.items || []);
    } catch (err) {
      notify(err.message, "error");
    }
  }, [apiCall, notify]);

  useEffect(() => {
    if (activeTab === "schema") loadSchemaParity();
    if (activeTab === "topology") loadTopology();
    if (activeTab === "audit") loadAuditTrail();
  }, [activeTab, loadSchemaParity, loadTopology, loadAuditTrail]);

  // Live Commercial Quote Computation (Tab 2)
  const liveQuote = useMemo(() => {
    const rateMap = Object.fromEntries(
      moduleCatalog.map((m) => [m.code, (m.base_annual_price_paise || 3000000) / 100])
    );
    const listAnnualInr = provForm.selected_modules.reduce(
      (sum, code) => sum + (rateMap[code] || 0),
      0
    );
    const bundleObj = BUNDLES.find((b) => b.code === provForm.pricing_model);
    const bundleAnnualInr = bundleObj?.priceInr || listAnnualInr;
    const savingsInr = Math.max(0, listAnnualInr - bundleAnnualInr);

    const hasIpd = provForm.selected_modules.includes("ipd_adt");
    const effectiveBeds = hasIpd ? Math.max(0, Number(provForm.quota_max_beds) || 0) : 0;
    const customInr = Number(provForm.custom_fee_inr) || 0;
    const cycle = provForm.billing_cycle;

    let periodFeeInr = bundleAnnualInr;
    let arrInr = bundleAnnualInr;
    let suffix = "/ yr";

    if (customInr > 0) {
      periodFeeInr = customInr;
      arrInr =
        cycle === "MONTHLY_SUBSCRIPTION" || cycle === "PER_ACTIVE_BED_MONTHLY"
          ? customInr * 12
          : cycle === "ONE_TIME_PERPETUAL"
          ? Math.round(customInr * 0.2)
          : customInr;
      suffix =
        cycle === "MONTHLY_SUBSCRIPTION" || cycle === "PER_ACTIVE_BED_MONTHLY"
          ? "/ mo (Custom)"
          : " (Custom)";
    } else if (cycle === "MONTHLY_SUBSCRIPTION") {
      periodFeeInr = Math.round(bundleAnnualInr / 12 / 100) * 100;
      arrInr = periodFeeInr * 12;
      suffix = "/ mo";
    } else if (cycle === "PER_ACTIVE_BED_MONTHLY") {
      periodFeeInr =
        effectiveBeds > 0
          ? Math.max(10, effectiveBeds) * 1500
          : Math.round(bundleAnnualInr / 12 / 100) * 100;
      arrInr = periodFeeInr * 12;
      suffix = `/ mo (${effectiveBeds} beds @ ${INR}1.5k)`;
    } else if (cycle === "ONE_TIME_PERPETUAL") {
      periodFeeInr = bundleAnnualInr * 3;
      arrInr = Math.round(bundleAnnualInr * 0.2);
      suffix = "one-time (+20% AMC)";
    }

    return {
      listAnnualInr,
      bundleAnnualInr,
      savingsInr,
      periodFeeInr,
      arrInr,
      suffix,
      effectiveBeds,
    };
  }, [provForm, moduleCatalog]);

  // Open Tenant Governance Drawer
  const openTenantDrawer = async (slug) => {
    setDrawerLoading(true);
    setDrawerOutput(null);
    try {
      const d = await apiCall(`/api/tenants/${slug}/details`, {}, `open-${slug}`);
      setDrawerTenant(d);
      setDrwForm({
        pricing_model: d.pricing_model || "BUNDLE_TOTAL_HOSPITAL",
        billing_cycle: d.billing_cycle || "ANNUAL_LICENSE",
        custom_fee_inr: Math.round((d.base_license_fee_paise || 0) / 100),
        max_beds: d.quota_limits?.max_beds ?? 500,
        max_users: d.quota_limits?.max_users ?? 200,
        maintenance_mode: !!d.maintenance_mode,
        maintenance_notice: d.maintenance_notice || "",
        enabled_modules: d.enabled_modules || {},
        sandbox_slug: `${d.subdomain_slug}-uat`,
      });
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setDrawerLoading(false);
    }
  };

  // Save Entitlements in Drawer
  const handleSaveEntitlements = async () => {
    if (!drawerTenant) return;
    try {
      const feeInr = Number(drwForm.custom_fee_inr) || 0;
      await apiCall(
        `/api/tenants/${drawerTenant.subdomain_slug}/entitlements`,
        {
          method: "PATCH",
          body: JSON.stringify({
            master_setup_key: masterKey.trim(),
            pricing_model: drwForm.pricing_model,
            billing_cycle: drwForm.billing_cycle,
            base_license_fee_paise: feeInr > 0 ? feeInr * 100 : null,
            enabled_modules: drwForm.enabled_modules,
            quota_limits: {
              max_beds: Number(drwForm.max_beds) || 0,
              max_users: Number(drwForm.max_users) || 200,
            },
            maintenance_mode: drwForm.maintenance_mode,
            maintenance_notice: drwForm.maintenance_notice || null,
          }),
        },
        "save-entitlements"
      );
      notify(`Updated contract & module entitlements for '${drawerTenant.subdomain_slug}'.`);
      await openTenantDrawer(drawerTenant.subdomain_slug);
      await syncFleetAll(true);
    } catch (err) {
      notify(err.message, "error");
    }
  };

  // Provisioning Submit Handler
  const handleProvisionSubmit = async (e) => {
    e.preventDefault();
    setProvResult(null);
    const slug = provForm.subdomain_slug.trim().toLowerCase();
    const hasIpd = provForm.selected_modules.includes("ipd_adt");
    const customInr = Number(provForm.custom_fee_inr) || 0;

    setProvLogs([
      { ts: new Date().toLocaleTimeString(), msg: `STAGE 1/5: Validating Master Vendor Key & checking '${slug}' availability...`, type: "cyan" },
      { ts: new Date().toLocaleTimeString(), msg: `STAGE 2/5: Creating isolated PostgreSQL database 'hms_${slug}'...`, type: "default" },
      { ts: new Date().toLocaleTimeString(), msg: `STAGE 3/5: Running 36-table Alembic baseline & seeding 9 RBAC roles + 10 Print Templates...`, type: "default" },
      { ts: new Date().toLocaleTimeString(), msg: `STAGE 4/5: Applying spatial blueprint '${provForm.blueprint_archetype}' & licensing ${provForm.selected_modules.length} modules...`, type: "default" },
    ]);

    try {
      const data = await apiCall(
        "/api/provision",
        {
          method: "POST",
          body: JSON.stringify({
            master_setup_key: masterKey.trim(),
            hospital_name: provForm.hospital_name.trim(),
            subdomain_slug: slug,
            branch_code: provForm.branch_code.trim().toUpperCase(),
            admin_email: provForm.admin_email.trim().toLowerCase(),
            pricing_model: provForm.pricing_model,
            billing_cycle: provForm.billing_cycle,
            selected_modules: provForm.selected_modules,
            base_license_fee_paise: customInr > 0 ? customInr * 100 : null,
            quota_max_beds: hasIpd ? Number(provForm.quota_max_beds) || 0 : 0,
            quota_max_users: Number(provForm.quota_max_users) || 200,
            blueprint_archetype: provForm.blueprint_archetype,
            federation_sharing_mode: provForm.federation_sharing_mode,
            brand_primary_color: provForm.brand_primary_color,
            seed_icd10: provForm.seed_icd10,
            seed_allergies: provForm.seed_allergies,
            seed_complaints: provForm.seed_complaints,
          }),
        },
        "provision-submit"
      );

      setProvLogs((prev) => [
        ...prev,
        {
          ts: new Date().toLocaleTimeString(),
          msg: `STAGE 5/5: Provisioned '${data.database_name}' & sealed keys via AES-256-GCM!`,
          type: "emerald",
        },
      ]);
      setProvResult(data);
      notify(`Hospital '${data.hospital_name}' provisioned at ${data.subdomain_url}!`);
      await syncFleetAll(true);
    } catch (err) {
      setProvLogs((prev) => [
        ...prev,
        { ts: new Date().toLocaleTimeString(), msg: `FATAL ERROR: ${err.message}`, type: "rose" },
      ]);
      notify(err.message, "error");
    }
  };

  const downloadEnvKit = (r) => {
    if (!r) return;
    const lines = [
      `# =====================================================================`,
      `# AVINYA ENTERPRISE HMS // TENANT CREDENTIAL HANDOVER KIT`,
      `# Hospital: ${r.hospital_name} (${r.branch_code})`,
      `# =====================================================================`,
      `HOSPITAL_NAME="${r.hospital_name}"`,
      `SUBDOMAIN_SLUG="${r.subdomain_slug}"`,
      `BRANCH_CODE="${r.branch_code}"`,
      `HOSPITAL_API_URL="${r.subdomain_url}"`,
      `DATABASE_NAME="${r.database_name}"`,
      `PRICING_MODEL="${r.pricing_model}"`,
      `BILLING_CYCLE="${r.billing_cycle}"`,
      `CONTRACT_FEE_INR="${Math.round((r.base_license_fee_paise || 0) / 100)}"`,
      `ADMIN_EMAIL="${r.admin_email}"`,
      `ADMIN_INITIAL_PASSWORD="${r.admin_password}"`,
      `TENANT_JWT_SECRET="${r.jwt_secret_key}"`,
      `FEDERATION_API_KEY="${r.federation_api_key}"`,
      `BACKUP_RECOVERY_KEY="${r.backup_encryption_key}"`,
    ].join("\n");
    const blob = new Blob([lines], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${r.subdomain_slug}_credentials.env`;
    a.click();
    notify(`Downloaded ${r.subdomain_slug}_credentials.env`);
  };

  // Filtered Fleet List
  const filteredTenants = useMemo(() => {
    const q = searchQuery.toLowerCase();
    return tenants.filter((t) => {
      const matchesText =
        t.hospital_name.toLowerCase().includes(q) ||
        t.subdomain_slug.toLowerCase().includes(q) ||
        t.branch_code.toLowerCase().includes(q) ||
        (t.pricing_model || "").toLowerCase().includes(q);
      if (!matchesText) return false;
      if (statusFilter === "ACTIVE") return t.is_active && !t.maintenance_mode;
      if (statusFilter === "MAINTENANCE") return t.is_active && t.maintenance_mode;
      if (statusFilter === "SUSPENDED") return !t.is_active;
      return true;
    });
  }, [tenants, searchQuery, statusFilter]);

  return (
    <div className="min-h-screen grid-surface p-4 md:p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* TOP COMMAND HEADER */}
        <header className="rounded-2xl bg-slate-900/80 backdrop-blur-xl border border-cyan-500/25 p-5 shadow-2xl flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-cyan-500/25 to-indigo-500/25 border border-cyan-400/40 flex items-center justify-center text-cyan-300 shadow-inner">
              <Cpu className="w-6 h-6" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2.5">
                <h1 className="text-lg md:text-xl font-black tracking-wide text-white">
                  AVINYA NEXUS // CLOUD CONTROL PLANE
                </h1>
                <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                  REACT v4.5 SPA
                </span>
                <button
                  onClick={() => setCmdOpen(true)}
                  className="px-2.5 py-1 rounded-lg text-[11px] font-mono bg-slate-950 hover:bg-slate-800 text-slate-300 border border-slate-700 flex items-center gap-1.5 transition"
                >
                  <Search className="w-3 h-3 text-cyan-400" />
                  <span>Ctrl+K Command</span>
                </button>
                <button
                  onClick={() => setShowInspector((v) => !v)}
                  className={`px-2.5 py-1 rounded-lg text-[11px] font-mono border flex items-center gap-1.5 transition ${
                    showInspector
                      ? "bg-indigo-500/25 text-indigo-200 border-indigo-400/50"
                      : "bg-slate-950 text-slate-400 border-slate-800 hover:text-white"
                  }`}
                >
                  <Code2 className="w-3.5 h-3.5" />
                  <span>API Inspector ({apiLogs.length})</span>
                </button>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                Zero-DB-Touch Tenant Provisioning, Commercial Bundle Quoting, Staged Canary Migrations &amp; Zero-Knowledge MPI Mesh
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <div className="relative">
              <KeyRound className="w-3.5 h-3.5 text-cyan-400 absolute left-3 top-2.5" />
              <input
                type="password"
                value={masterKey}
                onChange={(e) => setMasterKey(e.target.value)}
                placeholder="Master Vendor Key..."
                className="bg-slate-950/90 border border-slate-700 rounded-xl pl-8 pr-3 py-2 text-xs font-mono text-cyan-200 focus:border-cyan-400 outline-none w-52"
              />
            </div>
            <label className="flex items-center gap-1.5 text-xs text-slate-300 bg-slate-950/80 border border-slate-800 px-3 py-2 rounded-xl cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              <span className="font-mono text-[11px]">Live 15s</span>
            </label>
            <button
              onClick={() => syncFleetAll(false)}
              disabled={isLoadingFleet}
              className="bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 text-slate-950 font-black px-4 py-2 rounded-xl text-xs uppercase tracking-wider flex items-center gap-1.5 transition shadow-lg"
            >
              {isLoadingFleet ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <RefreshCw className="w-3.5 h-3.5" />
              )}
              <span>Sync Fleet</span>
            </button>
          </div>
        </header>

        {/* TOAST BANNER */}
        {toast && (
          <div
            className={`rounded-xl p-3.5 text-xs font-semibold flex items-center justify-between border transition ${
              toast.type === "error"
                ? "bg-rose-500/15 border-rose-500/40 text-rose-200"
                : "bg-emerald-500/15 border-emerald-500/40 text-emerald-200"
            }`}
          >
            <div className="flex items-center gap-2">
              {toast.type === "error" ? (
                <AlertTriangle className="w-4 h-4 text-rose-400" />
              ) : (
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              )}
              <span>{toast.message}</span>
            </div>
            <button onClick={() => setToast(null)} className="px-2 hover:text-white">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* LIVE API RESPONSE & LATENCY INSPECTOR DRAWER */}
        {showInspector && (
          <section className="rounded-2xl bg-slate-950/95 border border-indigo-500/40 p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <div className="flex items-center gap-2 text-xs font-bold text-indigo-300 font-mono">
                <Terminal className="w-4 h-4" />
                <span>REAL-TIME API TELEMETRY &amp; RESPONSE PAYLOAD INSPECTOR</span>
              </div>
              <button
                onClick={() => setApiLogs([])}
                className="text-[11px] text-slate-400 hover:text-white font-mono"
              >
                Clear Logs
              </button>
            </div>
            <div className="max-h-60 overflow-y-auto space-y-2 font-mono text-[11px]">
              {apiLogs.length === 0 ? (
                <div className="text-slate-500 py-4 text-center">
                  No API calls recorded yet. Click Sync Fleet or any action to inspect live responses.
                </div>
              ) : (
                apiLogs.map((log) => (
                  <details
                    key={log.id}
                    className="bg-slate-900/90 border border-slate-800 rounded-xl p-2.5"
                  >
                    <summary className="cursor-pointer flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            log.ok
                              ? "bg-emerald-500/20 text-emerald-300"
                              : "bg-rose-500/20 text-rose-300"
                          }`}
                        >
                          {log.method} {log.status}
                        </span>
                        <span className="text-white font-bold">{log.path}</span>
                      </div>
                      <span className="text-cyan-300">{log.latencyMs} &bull; {log.time}</span>
                    </summary>
                    <pre className="mt-2 p-2.5 rounded-lg bg-slate-950 text-slate-300 overflow-x-auto text-[10px]">
                      {JSON.stringify(log.responseBody, null, 2)}
                    </pre>
                  </details>
                ))
              )}
            </div>
          </section>
        )}

        {/* GLOBAL KPI PULSE BAR (WITH SKELETON STATES) */}
        <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            {
              label: "Active / Total Hospitals",
              val: overview ? `${overview.active_hospitals} / ${overview.total_hospitals}` : "-- / --",
              sub: "Isolated Cloud DBs",
              color: "text-cyan-300",
            },
            {
              label: "Fleet Bed Occupancy",
              val: overview ? `${overview.fleet_bed_occupancy_pct}%` : "--%",
              sub: overview ? `${overview.occupied_beds} / ${overview.total_beds} Beds` : "0 / 0 Beds",
              color: "text-emerald-400",
            },
            {
              label: "Normalized Annual ARR",
              val: overview ? fmtInr(overview.vendor_annual_license_arr_paise) : `${INR}--`,
              sub: "Recurring SaaS Book",
              color: "text-cyan-200",
            },
            {
              label: "Global MPI Pointers",
              val: overview ? overview.global_patient_pointers_indexed.toLocaleString("en-IN") : "--",
              sub: "SHA-256 Zero-Knowledge",
              color: "text-indigo-400",
            },
            {
              label: "Hospital GMV Volume",
              val: overview ? fmtInr(overview.fleet_gross_collected_revenue_paise) : `${INR}--`,
              sub: "Settled Patient Receipts",
              color: "text-amber-300",
            },
            {
              label: "Encrypted Snapshots",
              val: overview ? overview.encrypted_disaster_snapshots_count : "--",
              sub: "AES-256 Vault Ready",
              color: "text-rose-400",
            },
          ].map((kpi, idx) => (
            <div
              key={idx}
              className="rounded-xl bg-slate-900/75 border border-slate-800/90 p-4 flex flex-col justify-between"
            >
              <div className="text-[10px] font-bold uppercase text-slate-400">{kpi.label}</div>
              {isLoadingFleet ? (
                <div className="h-7 w-24 bg-slate-800 animate-pulse rounded mt-1.5" />
              ) : (
                <div className={`text-2xl font-black font-mono mt-1 ${kpi.color}`}>{kpi.val}</div>
              )}
              <div className="text-[10px] text-slate-500 font-mono mt-0.5">{kpi.sub}</div>
            </div>
          ))}
        </section>

        {/* 6-TAB NAVIGATION BAR */}
        <nav className="flex flex-wrap gap-2 border-b border-slate-800 pb-3">
          {[
            { id: "fleet", label: "1. Active Fleet & Inspector", icon: Building2 },
            { id: "provision", label: "2. Onboarding Studio", icon: PlusCircle },
            { id: "analytics", label: "3. Fleet Analytics & ARR BI", icon: BarChart3 },
            { id: "schema", label: "4. Staged Canary & Schema Sync", icon: GitBranch },
            { id: "topology", label: "5. Federation Mesh & MPI", icon: Network },
            { id: "audit", label: "6. Forensic Audit Trail", icon: ShieldCheck },
          ].map((t) => {
            const Icon = t.icon;
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`px-4 py-2 rounded-xl text-xs font-bold flex items-center gap-2 border transition ${
                  active
                    ? "bg-cyan-500/20 text-cyan-300 border-cyan-400/40 shadow-md"
                    : "bg-slate-900/90 text-slate-400 hover:text-white border-slate-800"
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{t.label}</span>
              </button>
            );
          })}
        </nav>

        {/* ================================================================= */}
        {/* TAB 1: ACTIVE HOSPITAL FLEET MATRIX                               */}
        {/* ================================================================= */}
        {activeTab === "fleet" && (
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2.5">
                <div className="relative">
                  <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3.5 top-2.5" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Filter hospitals by name, slug, branch code, or bundle..."
                    className="bg-slate-950 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs w-80 outline-none focus:border-cyan-400"
                  />
                </div>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-400"
                >
                  <option value="ALL">All Statuses ({tenants.length})</option>
                  <option value="ACTIVE">Active Only</option>
                  <option value="MAINTENANCE">In Maintenance</option>
                  <option value="SUSPENDED">Suspended</option>
                </select>
              </div>
              <button
                onClick={() => setActiveTab("provision")}
                className="px-3.5 py-2 rounded-xl bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 text-xs font-bold flex items-center gap-1.5"
              >
                <PlusCircle className="w-3.5 h-3.5" />
                <span>Onboard New Hospital</span>
              </button>
            </div>

            {isLoadingFleet ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {[1, 2, 3, 4, 5, 6].map((n) => (
                  <SkeletonCard key={n} />
                ))}
              </div>
            ) : filteredTenants.length === 0 ? (
              <div className="rounded-2xl bg-slate-900/70 border border-slate-800 p-12 text-center space-y-3">
                <Database className="w-8 h-8 text-cyan-400 mx-auto" />
                <div className="text-sm font-bold text-white">No Matching Hospital Tenants Found</div>
                <p className="text-xs text-slate-400">
                  Verify your Master Vendor Key in the top bar and click Sync Fleet, or provision a new hospital.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {filteredTenants.map((t) => {
                  const isMonthly =
                    t.billing_cycle === "MONTHLY_SUBSCRIPTION" ||
                    t.billing_cycle === "PER_ACTIVE_BED_MONTHLY";
                  return (
                    <div
                      key={t.id}
                      className={`rounded-2xl bg-slate-900/75 backdrop-blur-md p-5 flex flex-col justify-between space-y-4 border transition ${
                        !t.is_active
                          ? "border-rose-500/40"
                          : t.maintenance_mode
                          ? "border-amber-500/40"
                          : "border-slate-800 hover:border-cyan-500/40"
                      }`}
                    >
                      <div>
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <h3 className="font-bold text-sm text-white flex items-center gap-1.5">
                              <span>{t.hospital_name}</span>
                              {t.subdomain_slug === "default" && (
                                <span className="px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 text-[9px] font-mono">
                                  CANARY
                                </span>
                              )}
                            </h3>
                            <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                              {t.subdomain_slug} &bull; {t.branch_code}
                            </div>
                          </div>
                          <span
                            className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold font-mono ${
                              !t.is_active
                                ? "bg-rose-500/20 text-rose-300"
                                : t.maintenance_mode
                                ? "bg-amber-500/20 text-amber-300"
                                : "bg-emerald-500/20 text-emerald-300"
                            }`}
                          >
                            {!t.is_active ? "SUSPENDED" : t.maintenance_mode ? "MAINTENANCE" : "ACTIVE"}
                          </span>
                        </div>

                        <div className="grid grid-cols-2 gap-2 mt-4 bg-slate-950/80 p-3 rounded-xl text-[11px] font-mono border border-slate-800/80">
                          <div>
                            <span className="text-slate-500">DB:</span>{" "}
                            <span className="text-slate-200">{t.db_name}</span>
                          </div>
                          <div>
                            <span className="text-slate-500">Modules:</span>{" "}
                            <span className="text-emerald-300">{t.active_modules_count} / 11</span>
                          </div>
                          <div className="col-span-2 truncate">
                            <span className="text-slate-500">Tier:</span>{" "}
                            <span className="text-cyan-300">{t.pricing_model}</span>
                          </div>
                          <div className="col-span-2">
                            <span className="text-slate-500">Contract:</span>{" "}
                            <span className="text-amber-300 font-bold">
                              {fmtInr(t.base_license_fee_paise)}
                              {isMonthly ? "/mo" : "/yr"}
                            </span>{" "}
                            <span className="text-slate-500 text-[10px]">({t.billing_cycle})</span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center justify-between pt-2 border-t border-slate-800/80 text-xs">
                        <span className="text-[10px] text-slate-400 font-mono">
                          MPI Pointers: <b className="text-indigo-300">{t.global_patient_pointers_count}</b>
                        </span>
                        <div className="flex items-center gap-1.5">
                          <a
                            href={`http://${t.subdomain_slug}.localhost:8000/docs`}
                            target="_blank"
                            rel="noreferrer"
                            className="px-2.5 py-1.5 rounded-xl bg-slate-950 hover:bg-slate-800 text-slate-300 border border-slate-800 font-bold text-[11px] flex items-center gap-1"
                          >
                            <span>API</span>
                            <ArrowUpRight className="w-3 h-3" />
                          </a>
                          <button
                            onClick={() => openTenantDrawer(t.subdomain_slug)}
                            disabled={busyAction === `open-${t.subdomain_slug}`}
                            className="px-3 py-1.5 rounded-xl bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 font-bold text-xs flex items-center gap-1.5 transition"
                          >
                            {busyAction === `open-${t.subdomain_slug}` && (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            )}
                            <span>Manage &amp; Inspect</span>
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        )}

        {/* ================================================================= */}
        {/* TAB 2: MODULAR & BUNDLE HOSPITAL ONBOARDING STUDIO                */}
        {/* ================================================================= */}
        {activeTab === "provision" && (
          <section className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-7 rounded-2xl bg-slate-900/80 border border-slate-800 p-6 space-y-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <div>
                  <h2 className="text-base font-bold text-white">
                    Zero-Touch Hospital Cloud Provisioning Studio
                  </h2>
                  <p className="text-xs text-slate-400">
                    Provisions isolated PostgreSQL DB, runs 36-table Alembic baseline, seeds RBAC &amp; spatial layout.
                  </p>
                </div>
                <Sparkles className="w-5 h-5 text-cyan-400" />
              </div>

              <form onSubmit={handleProvisionSubmit} className="space-y-4 text-xs">
                {/* 1. Spatial Blueprint Selector */}
                <div>
                  <label className="block text-[11px] font-bold uppercase text-slate-400 mb-2">
                    1. Select Starter Spatial Blueprint (Hospital Admin Can Edit / Move / Delete Anytime)
                  </label>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    {blueprints.map((bp) => {
                      const active = provForm.blueprint_archetype === bp.key;
                      return (
                        <div
                          key={bp.key}
                          onClick={() =>
                            setProvForm((prev) => ({ ...prev, blueprint_archetype: bp.key }))
                          }
                          className={`p-3 rounded-xl cursor-pointer border transition ${
                            bp.key === "BLANK_CANVAS" ? "sm:col-span-2" : ""
                          } ${
                            active
                              ? "bg-cyan-500/15 border-cyan-400/50 text-white"
                              : "bg-slate-950/70 border-slate-800 text-slate-300 hover:border-slate-700"
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-bold text-xs">{bp.name}</span>
                            <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-900 text-cyan-300">
                              {bp.badge}
                            </span>
                          </div>
                          <p className="text-[10px] text-slate-400 mt-1">{bp.description}</p>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* 2. Commercial Bundle & Module Matrix */}
                <div className="space-y-3 pt-2 border-t border-slate-800">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <label className="block text-[11px] font-bold uppercase text-slate-400">
                      2. Select Commercial Bundle or &Agrave; La Carte Modules
                    </label>
                    <div className="flex items-center gap-2">
                      {liveQuote.savingsInr > 0 && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-bold font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                          Saves {INR}
                          {liveQuote.savingsInr.toLocaleString("en-IN")}/yr
                        </span>
                      )}
                      <span className="text-xs font-black font-mono text-cyan-300">
                        {INR}
                        {liveQuote.periodFeeInr.toLocaleString("en-IN")} {liveQuote.suffix}
                      </span>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                    {BUNDLES.map((b) => {
                      const active = provForm.pricing_model === b.code;
                      return (
                        <button
                          type="button"
                          key={b.code}
                          onClick={() =>
                            setProvForm((prev) => ({
                              ...prev,
                              pricing_model: b.code,
                              selected_modules:
                                b.code === "CUSTOM_A_LA_CARTE" ? prev.selected_modules : b.modules,
                            }))
                          }
                          className={`p-2.5 rounded-xl border text-left transition ${
                            active
                              ? "bg-cyan-500/20 border-cyan-400/50 text-cyan-200"
                              : "bg-slate-950 border-slate-800 text-slate-300 hover:border-slate-700"
                          }`}
                        >
                          <div className="font-bold text-[11px]">{b.name}</div>
                          <div className="text-[10px] text-slate-400 mt-0.5">
                            {b.priceInr ? `${INR}${(b.priceInr / 1000).toFixed(0)}k/yr` : "Custom"}
                          </div>
                        </button>
                      );
                    })}
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-slate-950 p-3.5 rounded-xl border border-slate-800 text-[11px]">
                    {moduleCatalog.map((m) => {
                      const checked = provForm.selected_modules.includes(m.code);
                      return (
                        <label key={m.code} className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              const next = e.target.checked
                                ? [...provForm.selected_modules, m.code]
                                : provForm.selected_modules.filter((x) => x !== m.code);
                              setProvForm((prev) => ({
                                ...prev,
                                pricing_model: "CUSTOM_A_LA_CARTE",
                                selected_modules: next,
                              }));
                            }}
                          />
                          <span>
                            {m.name} ({INR}
                            {Math.round((m.base_annual_price_paise || 3000000) / 100000)}k)
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>

                {/* 3. Hospital Identity & Auto-Slug */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-800">
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Hospital Legal Name
                    </label>
                    <input
                      type="text"
                      required
                      value={provForm.hospital_name}
                      onChange={(e) => {
                        const val = e.target.value;
                        const cleanSlug = val
                          .toLowerCase()
                          .replace(/[^a-z0-9]+/g, "-")
                          .replace(/^-|-$/g, "")
                          .slice(0, 24);
                        const prefix = (val.replace(/[^A-Za-z]/g, "").toUpperCase() + "HMS").slice(
                          0,
                          6
                        );
                        setProvForm((prev) => ({
                          ...prev,
                          hospital_name: val,
                          subdomain_slug: cleanSlug,
                          branch_code: val ? `${prefix}-HYD-01` : "",
                        }));
                      }}
                      placeholder="e.g. KIMS Icon Hospital"
                      className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 outline-none focus:border-cyan-400"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Subdomain Slug
                    </label>
                    <input
                      type="text"
                      required
                      value={provForm.subdomain_slug}
                      onChange={(e) =>
                        setProvForm((prev) => ({ ...prev, subdomain_slug: e.target.value }))
                      }
                      placeholder="e.g. kims-icon"
                      className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 font-mono lowercase outline-none focus:border-cyan-400"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Branch Code
                    </label>
                    <input
                      type="text"
                      required
                      value={provForm.branch_code}
                      onChange={(e) =>
                        setProvForm((prev) => ({ ...prev, branch_code: e.target.value }))
                      }
                      placeholder="e.g. KIMS-HYD-01"
                      className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 font-mono uppercase outline-none focus:border-cyan-400"
                    />
                  </div>
                </div>

                {/* 4. Billing Cycle, Custom Fee & Quotas */}
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Superuser Admin Email
                    </label>
                    <input
                      type="email"
                      required
                      value={provForm.admin_email}
                      onChange={(e) =>
                        setProvForm((prev) => ({ ...prev, admin_email: e.target.value }))
                      }
                      placeholder="admin@hospital.com"
                      className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 outline-none focus:border-cyan-400"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Billing Cycle
                    </label>
                    <select
                      value={provForm.billing_cycle}
                      onChange={(e) =>
                        setProvForm((prev) => ({ ...prev, billing_cycle: e.target.value }))
                      }
                      className="w-full bg-slate-950 border border-slate-700 rounded-xl px-2.5 py-2 outline-none focus:border-cyan-400"
                    >
                      <option value="ANNUAL_LICENSE">Annual License</option>
                      <option value="MONTHLY_SUBSCRIPTION">Monthly Installment</option>
                      <option value="PER_ACTIVE_BED_MONTHLY">Per-Bed Monthly ({INR}1.5k/bed)</option>
                      <option value="ONE_TIME_PERPETUAL">Perpetual Buyout (+20% AMC)</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Custom Fee Override ({INR})
                    </label>
                    <input
                      type="number"
                      value={provForm.custom_fee_inr}
                      onChange={(e) =>
                        setProvForm((prev) => ({ ...prev, custom_fee_inr: e.target.value }))
                      }
                      placeholder="Auto from Bundle"
                      className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Bed / User Quotas
                    </label>
                    <div className="flex gap-1.5">
                      <input
                        type="number"
                        value={
                          provForm.selected_modules.includes("ipd_adt")
                            ? provForm.quota_max_beds
                            : 0
                        }
                        disabled={!provForm.selected_modules.includes("ipd_adt")}
                        onChange={(e) =>
                          setProvForm((prev) => ({ ...prev, quota_max_beds: e.target.value }))
                        }
                        className="w-1/2 bg-slate-950 border border-slate-700 rounded-xl px-2 py-2 font-mono disabled:opacity-40"
                      />
                      <input
                        type="number"
                        value={provForm.quota_max_users}
                        onChange={(e) =>
                          setProvForm((prev) => ({ ...prev, quota_max_users: e.target.value }))
                        }
                        className="w-1/2 bg-slate-950 border border-slate-700 rounded-xl px-2 py-2 font-mono"
                      />
                    </div>
                  </div>
                </div>

                {/* 5. Day-0 Privacy Sovereignty & Clinical Dictionaries */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2 border-t border-slate-800">
                  <div>
                    <label className="block text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Day-0 Federation Privacy Sovereignty
                    </label>
                    <select
                      value={provForm.federation_sharing_mode}
                      onChange={(e) =>
                        setProvForm((prev) => ({
                          ...prev,
                          federation_sharing_mode: e.target.value,
                        }))
                      }
                      className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 outline-none focus:border-cyan-400"
                    >
                      <option value="FULL_MESH">FULL_MESH (Share Demographics &amp; Allergies)</option>
                      <option value="CORPORATE_GROUP_ONLY">CORPORATE_GROUP_ONLY (Sister Branches Only)</option>
                      <option value="EMERGENCY_ALLERGIES_ONLY">EMERGENCY_ALLERGIES_ONLY (Redacts Address)</option>
                      <option value="ISOLATED_SILO">ISOLATED_SILO (100% Private / Zero Outbound)</option>
                    </select>
                  </div>
                  <div className="bg-slate-950/90 border border-slate-800 rounded-xl p-2.5 flex flex-col justify-center">
                    <span className="text-[10px] font-bold uppercase text-slate-400 mb-1">
                      Pre-Seed Clinical Dictionaries:
                    </span>
                    <div className="flex flex-wrap gap-3 text-[11px]">
                      <label className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={provForm.seed_icd10}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, seed_icd10: e.target.checked }))
                          }
                        />
                        <span>ICD-10</span>
                      </label>
                      <label className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={provForm.seed_allergies}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, seed_allergies: e.target.checked }))
                          }
                        />
                        <span>Allergies</span>
                      </label>
                      <label className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={provForm.seed_complaints}
                          onChange={(e) =>
                            setProvForm((p) => ({ ...p, seed_complaints: e.target.checked }))
                          }
                        />
                        <span>Complaints &amp; Exams</span>
                      </label>
                    </div>
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={busyAction === "provision-submit"}
                  className="w-full bg-gradient-to-r from-cyan-500 to-indigo-500 hover:from-cyan-400 hover:to-indigo-400 disabled:opacity-50 text-slate-950 font-black py-3 rounded-xl uppercase tracking-wider flex items-center justify-center gap-2 transition shadow-lg"
                >
                  {busyAction === "provision-submit" ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Provisioning Isolated Database &amp; Seeding Blueprint...</span>
                    </>
                  ) : (
                    <>
                      <Zap className="w-4 h-4" />
                      <span>Execute Zero-Touch Tenant Provisioning</span>
                    </>
                  )}
                </button>
              </form>
            </div>

            {/* RIGHT COLUMN: LIVE QUOTE BREAKDOWN, TERMINAL & CREDENTIAL VAULT */}
            <div className="lg:col-span-5 space-y-4">
              <div className="rounded-2xl bg-slate-900/80 border border-cyan-500/30 p-5 space-y-3 text-xs">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                  <span className="font-bold uppercase tracking-wider text-cyan-300 font-mono">
                    Live Commercial Contract Quote
                  </span>
                  <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-200 font-mono text-[10px] font-bold">
                    {provForm.pricing_model}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2.5 font-mono text-[11px]">
                  <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                    <div className="text-[10px] text-slate-400">&Agrave; La Carte List Value</div>
                    <div className="text-sm font-bold text-slate-200 mt-0.5">
                      {INR}
                      {liveQuote.listAnnualInr.toLocaleString("en-IN")} / yr
                    </div>
                  </div>
                  <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                    <div className="text-[10px] text-slate-400">Bundle Discount Saved</div>
                    <div className="text-sm font-bold text-emerald-400 mt-0.5">
                      {INR}
                      {liveQuote.savingsInr.toLocaleString("en-IN")} / yr
                    </div>
                  </div>
                  <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                    <div className="text-[10px] text-slate-400">Billed Per Cycle</div>
                    <div className="text-sm font-black text-cyan-300 mt-0.5">
                      {INR}
                      {liveQuote.periodFeeInr.toLocaleString("en-IN")} {liveQuote.suffix}
                    </div>
                  </div>
                  <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                    <div className="text-[10px] text-slate-400">Normalized Annual ARR</div>
                    <div className="text-sm font-black text-amber-300 mt-0.5">
                      {INR}
                      {liveQuote.arrInr.toLocaleString("en-IN")} / yr
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-2xl bg-slate-900/80 border border-slate-800 p-5 space-y-3">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
                  <span className="text-xs font-bold uppercase tracking-wider text-cyan-300 font-mono">
                    Provisioning Execution Telemetry
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300">
                    {busyAction === "provision-submit" ? "EXECUTING" : provResult ? "COMPLETED" : "STANDBY"}
                  </span>
                </div>
                <div className="bg-slate-950 border border-slate-800 rounded-xl p-3.5 h-48 overflow-y-auto font-mono text-[11px] space-y-1.5">
                  {provLogs.map((l, idx) => (
                    <div
                      key={idx}
                      className={
                        l.type === "cyan"
                          ? "text-cyan-300"
                          : l.type === "emerald"
                          ? "text-emerald-400 font-bold"
                          : l.type === "rose"
                          ? "text-rose-400 font-bold"
                          : "text-slate-300"
                      }
                    >
                      [{l.ts}] {l.msg}
                    </div>
                  ))}
                </div>

                {provResult && (
                  <div className="bg-emerald-950/30 border border-emerald-500/40 rounded-xl p-4 space-y-2.5 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-bold text-emerald-300 uppercase">
                        Provisioned Security Envelope
                      </span>
                      <div className="flex gap-1.5">
                        <button
                          type="button"
                          onClick={() => downloadEnvKit(provResult)}
                          className="px-2.5 py-1 rounded bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-200 border border-cyan-500/30 text-[10px] font-bold flex items-center gap-1"
                        >
                          <Download className="w-3 h-3" />
                          <span>.env Kit</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            navigator.clipboard.writeText(JSON.stringify(provResult, null, 2));
                            notify("Copied provisioning JSON to clipboard.");
                          }}
                          className="px-2.5 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-200 border border-emerald-500/30 text-[10px] font-bold flex items-center gap-1"
                        >
                          <Copy className="w-3 h-3" />
                          <span>Copy JSON</span>
                        </button>
                      </div>
                    </div>
                    <div className="font-mono text-[11px] space-y-1 text-slate-200 break-all">
                      <div>
                        <span className="text-slate-400">URL:</span>{" "}
                        <span className="text-emerald-300 font-bold">{provResult.subdomain_url}</span>
                      </div>
                      <div>
                        <span className="text-slate-400">Database:</span> {provResult.database_name} (
                        {fmtInr(provResult.base_license_fee_paise)})
                      </div>
                      <div>
                        <span className="text-slate-400">Spatial Seeded:</span>{" "}
                        {provResult.spatial_summary?.floors} Floors, {provResult.spatial_summary?.wards} Wards,{" "}
                        {provResult.spatial_summary?.beds} Beds, {provResult.spatial_summary?.theatres} OTs
                      </div>
                      <div>
                        <span className="text-slate-400">Admin Email:</span> {provResult.admin_email}
                      </div>
                      <div>
                        <span className="text-slate-400">One-Time Password:</span>{" "}
                        <span className="text-amber-300 font-bold">{provResult.admin_password}</span>
                      </div>
                      <div>
                        <span className="text-slate-400">Federation Key:</span>{" "}
                        {provResult.federation_api_key}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </section>
        )}

        {/* ================================================================= */}
        {/* TAB 3: FLEET ANALYTICS & COMMERCIAL INTELLIGENCE                  */}
        {/* ================================================================= */}
        {activeTab === "analytics" && (
          <section className="space-y-6">
            {!analytics ? (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <SkeletonCard />
                <SkeletonCard />
                <SkeletonCard />
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                  {/* ARR by Bundle Breakdown */}
                  <div className="rounded-2xl bg-slate-900/80 border border-slate-800 p-5 space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-cyan-300">
                      Normalized ARR by Commercial Package
                    </h3>
                    <div className="space-y-2.5 text-xs">
                      {Object.entries(analytics.arr_by_pricing_model_paise || {}).map(
                        ([model, arrPaise]) => (
                          <div key={model} className="space-y-1">
                            <div className="flex justify-between font-mono text-[11px]">
                              <span className="text-slate-300">{model}</span>
                              <span className="text-cyan-300 font-bold">{fmtInr(arrPaise)}</span>
                            </div>
                            <div className="w-full bg-slate-950 h-2 rounded-full overflow-hidden">
                              <div
                                className="bg-gradient-to-r from-cyan-400 to-indigo-500 h-full"
                                style={{
                                  width: `${Math.min(
                                    100,
                                    Math.round(
                                      (arrPaise /
                                        Math.max(1, overview?.vendor_annual_license_arr_paise || 1)) *
                                        100
                                    )
                                  )}%`,
                                }}
                              />
                            </div>
                          </div>
                        )
                      )}
                    </div>
                  </div>

                  {/* Billing Cycle Mix */}
                  <div className="rounded-2xl bg-slate-900/80 border border-slate-800 p-5 space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-indigo-300">
                      Contract Billing Cycle Distribution
                    </h3>
                    <div className="space-y-2.5 text-xs">
                      {Object.entries(analytics.tenants_by_billing_cycle || {}).map(
                        ([cycle, count]) => (
                          <div
                            key={cycle}
                            className="flex items-center justify-between bg-slate-950 p-3 rounded-xl border border-slate-800 font-mono"
                          >
                            <span className="text-slate-300">{cycle}</span>
                            <span className="px-2.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-bold">
                              {count} Hospital(s)
                            </span>
                          </div>
                        )
                      )}
                    </div>
                  </div>

                  {/* Module Adoption Penetration */}
                  <div className="rounded-2xl bg-slate-900/80 border border-slate-800 p-5 space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-300">
                      Module Adoption Across Fleet ({analytics.total_tenants} Tenants)
                    </h3>
                    <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
                      {Object.entries(analytics.module_adoption_counts || {}).map(([mod, cnt]) => (
                        <div
                          key={mod}
                          className="bg-slate-950 px-3 py-2 rounded-xl border border-slate-800 flex items-center justify-between"
                        >
                          <span className="text-slate-300">{mod}</span>
                          <span className="text-emerald-400 font-bold">
                            {cnt}/{analytics.total_tenants}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Hospital Operational & GMV Leaderboard */}
                <div className="rounded-2xl bg-slate-900/80 border border-slate-800 p-6 space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-sm font-bold text-white">
                        Hospital Fleet Operational &amp; GMV Leaderboard
                      </h3>
                      <p className="text-xs text-slate-400">
                        Live cross-database comparison of patient volume, bed occupancy, and collected revenue.
                      </p>
                    </div>
                  </div>
                  <div className="overflow-x-auto text-xs">
                    <table className="w-full text-left font-mono">
                      <thead>
                        <tr className="text-slate-400 border-b border-slate-800 text-[11px] uppercase">
                          <th className="py-2.5 px-3">Hospital</th>
                          <th className="py-2.5 px-3">Package</th>
                          <th className="py-2.5 px-3">Normalized ARR</th>
                          <th className="py-2.5 px-3">MPI Patients</th>
                          <th className="py-2.5 px-3">Bed Occupancy</th>
                          <th className="py-2.5 px-3">Collected GMV</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(analytics.hospital_leaderboard || []).map((h) => (
                          <tr key={h.subdomain_slug} className="border-b border-slate-800/70">
                            <td className="py-2.5 px-3 font-bold text-white">
                              {h.hospital_name}{" "}
                              <span className="text-slate-500">({h.subdomain_slug})</span>
                            </td>
                            <td className="py-2.5 px-3 text-cyan-300">{h.pricing_model}</td>
                            <td className="py-2.5 px-3 text-emerald-300">
                              {fmtInr(h.normalized_arr_paise)}
                            </td>
                            <td className="py-2.5 px-3 text-slate-200">{h.patients_count}</td>
                            <td className="py-2.5 px-3">
                              <span className="text-amber-300">
                                {h.occupied_beds}/{h.total_beds} ({h.occupancy_pct}%)
                              </span>
                            </td>
                            <td className="py-2.5 px-3 font-bold text-amber-300">
                              {fmtInr(h.collected_gmv_paise)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </section>
        )}

        {/* ================================================================= */}
        {/* TAB 4: STAGED CANARY & FLEET SCHEMA SYNC                          */}
        {/* ================================================================= */}
        {activeTab === "schema" && (
          <section className="rounded-2xl bg-slate-900/80 border border-slate-800 p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
              <div>
                <h2 className="text-base font-bold text-white">
                  Staged Canary Migration &amp; Fleet Template Synchronizer
                </h2>
                <p className="text-xs text-slate-400">
                  Stage 1: Upgrade &amp; verify <span className="font-mono text-cyan-300">default</span>{" "}
                  (Canary DB) first. Stage 2: Push verified Alembic{" "}
                  <span className="font-mono">{schemaParity?.head_revision || "head"}</span> to all remaining hospitals.
                </p>
              </div>
              <div className="flex gap-2.5">
                <button
                  onClick={loadSchemaParity}
                  disabled={busyAction === "schema-load"}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-bold flex items-center gap-1.5"
                >
                  {busyAction === "schema-load" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>Refresh Parity</span>
                </button>
                <button
                  onClick={async () => {
                    try {
                      const d = await apiCall(
                        "/api/tenants/sync-fleet",
                        {
                          method: "POST",
                          body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                        },
                        "sync-fleet"
                      );
                      notify(
                        `Stage 2 Complete: Migrated & synced ${d.synced_tenants_count} cloud hospitals.`
                      );
                      await loadSchemaParity();
                    } catch (err) {
                      notify(err.message, "error");
                    }
                  }}
                  disabled={busyAction === "sync-fleet"}
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold flex items-center gap-1.5"
                >
                  {busyAction === "sync-fleet" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>Stage 2: Push Head &amp; Templates to Entire Fleet</span>
                </button>
              </div>
            </div>

            <div className="overflow-x-auto text-xs">
              <table className="w-full text-left font-mono">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-800 text-[11px] uppercase">
                    <th className="py-2.5 px-3">Tenant Slug</th>
                    <th className="py-2.5 px-3">Database</th>
                    <th className="py-2.5 px-3">Current DB Revision</th>
                    <th className="py-2.5 px-3">Target Head</th>
                    <th className="py-2.5 px-3">Parity Status</th>
                    <th className="py-2.5 px-3 text-right">Stage 1 / Single Action</th>
                  </tr>
                </thead>
                <tbody>
                  {(schemaParity?.parity || []).map((p) => (
                    <tr key={p.subdomain_slug} className="border-b border-slate-800/80">
                      <td className="py-2.5 px-3 font-bold text-white">
                        {p.subdomain_slug}{" "}
                        {p.subdomain_slug === "default" && (
                          <span className="ml-1 px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 text-[9px]">
                            CANARY
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-slate-300">{p.db_name}</td>
                      <td className="py-2.5 px-3 text-cyan-300">{p.schema_version}</td>
                      <td className="py-2.5 px-3 text-slate-400">{p.head_revision}</td>
                      <td className="py-2.5 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            p.status === "UP_TO_DATE"
                              ? "bg-emerald-500/20 text-emerald-300"
                              : "bg-amber-500/20 text-amber-300"
                          }`}
                        >
                          {p.status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          onClick={async () => {
                            try {
                              const res = await apiCall(
                                `/api/tenants/${p.subdomain_slug}/sync`,
                                {
                                  method: "POST",
                                  body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                                },
                                `sync-${p.subdomain_slug}`
                              );
                              notify(res.message);
                              await loadSchemaParity();
                            } catch (err) {
                              notify(err.message, "error");
                            }
                          }}
                          disabled={busyAction === `sync-${p.subdomain_slug}`}
                          className="px-3 py-1 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 font-bold text-[11px] inline-flex items-center gap-1"
                        >
                          {busyAction === `sync-${p.subdomain_slug}` && (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          )}
                          <span>Upgrade &amp; Sync Tenant</span>
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* ================================================================= */}
        {/* TAB 5: ZERO-KNOWLEDGE FEDERATION MESH & LIVE MPI LOOKUP           */}
        {/* ================================================================= */}
        {activeTab === "topology" && (
          <section className="space-y-6">
            <div className="rounded-2xl bg-slate-900/80 border border-slate-800 p-6 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
                <div>
                  <h2 className="text-base font-bold text-white">
                    Zero-Knowledge Global MPI Pointer Verifier
                  </h2>
                  <p className="text-xs text-slate-400">
                    Test phone numbers (hashed via SHA-256), ABHA IDs, or MRNs to verify which hospital branches hold records without exposing clinical PII.
                  </p>
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={mpiQuery}
                    onChange={(e) => setMpiQuery(e.target.value)}
                    placeholder="Enter Phone, ABHA-..., or MRN-..."
                    className="bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2 text-xs font-mono w-64 outline-none focus:border-cyan-400"
                  />
                  <button
                    onClick={async () => {
                      if (!mpiQuery.trim()) return;
                      try {
                        const d = await apiCall(
                          `/api/mpi/lookup?query=${encodeURIComponent(mpiQuery.trim())}`,
                          {},
                          "mpi-lookup"
                        );
                        setMpiResult(d);
                      } catch (err) {
                        notify(err.message, "error");
                      }
                    }}
                    disabled={busyAction === "mpi-lookup"}
                    className="px-4 py-2 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-black text-xs flex items-center gap-1.5"
                  >
                    {busyAction === "mpi-lookup" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    <span>Verify Pointer Mesh</span>
                  </button>
                </div>
              </div>

              {mpiResult && (
                <div className="text-xs font-mono space-y-2">
                  <div className="text-slate-300">
                    Query: <b className="text-white">{mpiResult.query}</b> &bull; Computed SHA-256 Prefix:{" "}
                    <span className="text-cyan-300">{mpiResult.computed_phone_sha256 || "N/A"}</span> &bull; Matches:{" "}
                    <b className="text-emerald-400">{mpiResult.matches_count}</b>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    {(mpiResult.pointers || []).map((p) => (
                      <div
                        key={p.id}
                        className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex items-center justify-between"
                      >
                        <div>
                          <div className="font-bold text-white">
                            {p.branch_code} ({p.subdomain_slug}) &bull; MRN:{" "}
                            <span className="text-cyan-300">{p.source_mrn}</span>
                          </div>
                          <div className="text-[10px] text-slate-400 mt-0.5">
                            ABHA: {p.abha_number || "N/A"} | Hash: {p.phone_hash_prefix}
                          </div>
                        </div>
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            p.has_critical_allergies
                              ? "bg-rose-500/20 text-rose-300"
                              : "bg-slate-800 text-slate-300"
                          }`}
                        >
                          {p.has_critical_allergies ? "CRITICAL ALLERGY" : "STANDARD"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-2xl bg-slate-900/80 border border-slate-800 p-6 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <div>
                  <h2 className="text-base font-bold text-white">
                    Connected Hospital Nodes &amp; Sovereignty Matrix
                  </h2>
                  <p className="text-xs text-slate-400">
                    Real-time view of every hospital branch key and its outbound sharing sovereignty policy.
                  </p>
                </div>
                <button
                  onClick={loadTopology}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-bold"
                >
                  Refresh Mesh
                </button>
              </div>
              <div className="overflow-x-auto text-xs font-mono">
                <table className="w-full text-left">
                  <thead>
                    <tr className="text-slate-400 border-b border-slate-800 text-[11px] uppercase">
                      <th className="py-2.5 px-3">Branch Code</th>
                      <th className="py-2.5 px-3">Hospital Node</th>
                      <th className="py-2.5 px-3">Fed Key Prefix</th>
                      <th className="py-2.5 px-3">Sovereignty Mode</th>
                      <th className="py-2.5 px-3">Indexed Pointers</th>
                      <th className="py-2.5 px-3">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topology.map((n) => (
                      <tr key={n.branch_code} className="border-b border-slate-800/80">
                        <td className="py-2.5 px-3 font-bold text-white">{n.branch_code}</td>
                        <td className="py-2.5 px-3 text-slate-200">
                          {n.hospital_name} ({n.subdomain_slug})
                        </td>
                        <td className="py-2.5 px-3 text-slate-400">{n.api_key_prefix}...</td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              n.federation_sharing_mode === "ISOLATED_SILO"
                                ? "bg-rose-500/20 text-rose-300"
                                : n.federation_sharing_mode === "CORPORATE_GROUP_ONLY"
                                ? "bg-amber-500/20 text-amber-300"
                                : "bg-cyan-500/20 text-cyan-300"
                            }`}
                          >
                            {n.federation_sharing_mode}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-indigo-300 font-bold">
                          {n.indexed_pointers_count}
                        </td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              n.is_active
                                ? "bg-emerald-500/20 text-emerald-300"
                                : "bg-rose-500/20 text-rose-300"
                            }`}
                          >
                            {n.is_active ? "ONLINE" : "REVOKED"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        {/* ================================================================= */}
        {/* TAB 6: IMMUTABLE VENDOR AUDIT TRAIL                               */}
        {/* ================================================================= */}
        {activeTab === "audit" && (
          <section className="rounded-2xl bg-slate-900/80 border border-slate-800 p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
              <div>
                <h2 className="text-base font-bold text-white">
                  Immutable Control Plane Forensic Audit Ledger
                </h2>
                <p className="text-xs text-slate-400">
                  Tamper-evident log of all hospital provisionings, contract updates, key rotations, and snapshot restores.
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    if (!auditLogs.length) return;
                    const hdr = "id,action,target_slug,actor,details,created_at\n";
                    const rows = auditLogs
                      .map(
                        (l) =>
                          `${l.id},"${l.action}","${l.target_slug}","${l.actor}","${(
                            l.details || ""
                          ).replace(/"/g, '""')}","${l.created_at}"`
                      )
                      .join("\n");
                    const blob = new Blob([hdr + rows], { type: "text/csv;charset=utf-8" });
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(blob);
                    a.download = `avinya_audit_ledger_${new Date().toISOString().slice(0, 10)}.csv`;
                    a.click();
                  }}
                  className="px-3.5 py-2 rounded-xl bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/30 text-xs font-bold flex items-center gap-1.5"
                >
                  <FileSpreadsheet className="w-3.5 h-3.5" />
                  <span>Export CSV</span>
                </button>
                <button
                  onClick={loadAuditTrail}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-bold"
                >
                  Refresh Ledger
                </button>
              </div>
            </div>
            <div className="overflow-x-auto text-xs font-mono">
              <table className="w-full text-left">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-800 text-[11px] uppercase">
                    <th className="py-2 px-3">ID</th>
                    <th className="py-2 px-3">Action</th>
                    <th className="py-2 px-3">Target Tenant</th>
                    <th className="py-2 px-3">Forensic Details</th>
                    <th className="py-2 px-3">Timestamp (UTC)</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLogs.map((l) => (
                    <tr key={l.id} className="border-b border-slate-800/80">
                      <td className="py-2 px-3 text-slate-400">#{l.id}</td>
                      <td className="py-2 px-3 font-bold text-cyan-300">{l.action}</td>
                      <td className="py-2 px-3 font-bold text-white">{l.target_slug}</td>
                      <td className="py-2 px-3 text-slate-300 font-sans">{l.details}</td>
                      <td className="py-2 px-3 text-slate-400">{l.created_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>

      {/* =================================================================== */}
      {/* CTRL+K GLOBAL COMMAND PALETTE MODAL                                 */}
      {/* =================================================================== */}
      {cmdOpen && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-start justify-center pt-24 p-4">
          <div className="w-full max-w-xl rounded-2xl bg-slate-900 border border-cyan-500/40 p-4 space-y-3 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
              <input
                type="text"
                autoFocus
                value={cmdQuery}
                onChange={(e) => setCmdQuery(e.target.value)}
                placeholder="Type a hospital name or command (e.g. 'apollo', 'analytics', 'sync')..."
                className="w-full bg-transparent text-sm text-white outline-none font-mono"
              />
              <button
                onClick={() => setCmdOpen(false)}
                className="text-xs font-mono text-slate-400 hover:text-white px-2 py-0.5 rounded bg-slate-800"
              >
                ESC
              </button>
            </div>
            <div className="max-h-72 overflow-y-auto space-y-1.5 text-xs">
              {[
                { label: "Go to Tab 1: Active Hospital Fleet", act: () => setActiveTab("fleet") },
                { label: "Go to Tab 2: Launch New Hospital Onboarding Studio", act: () => setActiveTab("provision") },
                { label: "Go to Tab 3: Fleet Analytics & ARR BI", act: () => setActiveTab("analytics") },
                { label: "Go to Tab 4: Staged Canary & Fleet Schema Sync", act: () => setActiveTab("schema") },
                { label: "Go to Tab 5: Federation Mesh & Live MPI Lookup", act: () => setActiveTab("topology") },
                { label: "Go to Tab 6: Forensic Audit Ledger", act: () => setActiveTab("audit") },
                { label: "Toggle Live API Response Inspector", act: () => setShowInspector((v) => !v) },
                ...tenants.map((t) => ({
                  label: `Inspect & Manage Hospital: ${t.hospital_name} (${t.subdomain_slug} / ${t.branch_code})`,
                  act: () => openTenantDrawer(t.subdomain_slug),
                })),
              ]
                .filter((i) => i.label.toLowerCase().includes(cmdQuery.toLowerCase()))
                .map((item, idx) => (
                  <button
                    key={idx}
                    onClick={() => {
                      setCmdOpen(false);
                      item.act();
                    }}
                    className="w-full text-left px-3.5 py-2.5 rounded-xl bg-slate-950/90 hover:bg-cyan-500/20 hover:text-cyan-200 border border-slate-800 transition flex items-center justify-between"
                  >
                    <span>{item.label}</span>
                    <span className="text-[10px] font-mono text-slate-400">&crarr;</span>
                  </button>
                ))}
            </div>
          </div>
        </div>
      )}

      {/* =================================================================== */}
      {/* SLIDE-OVER TENANT GOVERNANCE & DISASTER RECOVERY DRAWER             */}
      {/* =================================================================== */}
      {drawerTenant && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex justify-end">
          <div className="w-full max-w-3xl bg-slate-950 border-l border-slate-800 h-full overflow-y-auto p-6 space-y-6">
            <div className="flex items-start justify-between border-b border-slate-800 pb-4">
              <div>
                <div className="flex flex-wrap items-center gap-2.5">
                  <h2 className="text-xl font-black text-white">{drawerTenant.hospital_name}</h2>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold font-mono ${
                      !drawerTenant.is_active
                        ? "bg-rose-500/20 text-rose-300"
                        : drawerTenant.maintenance_mode
                        ? "bg-amber-500/20 text-amber-300"
                        : "bg-emerald-500/20 text-emerald-300"
                    }`}
                  >
                    {!drawerTenant.is_active
                      ? "SUSPENDED"
                      : drawerTenant.maintenance_mode
                      ? "MAINTENANCE"
                      : "ACTIVE"}
                  </span>
                  <span className="px-2.5 py-0.5 rounded-full text-[10px] font-bold font-mono bg-cyan-500/20 text-cyan-300">
                    MPI: {drawerTenant.hospital_policy?.federation_sharing_mode || "FULL_MESH"}
                  </span>
                </div>
                <p className="text-xs text-slate-400 font-mono mt-1">
                  Subdomain: {drawerTenant.subdomain_slug} | Branch: {drawerTenant.branch_code} | DB:{" "}
                  {drawerTenant.db_name} | Admin: {drawerTenant.admin_email}
                </p>
              </div>
              <button
                onClick={() => setDrawerTenant(null)}
                className="text-slate-400 hover:text-white p-1.5 rounded-lg hover:bg-slate-900"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 1. Live Data-Plane Telemetry & Quota Utilization */}
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-cyan-300 mb-2.5">
                1. Live Data-Plane Telemetry &amp; Quota Utilization
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
                <div className="bg-slate-900 p-3.5 rounded-xl border border-slate-800">
                  <div className="text-[10px] text-slate-400 uppercase">Staff Users Quota</div>
                  <div className="text-lg font-black text-white mt-0.5">
                    {drawerTenant.metrics?.users_count} / {drwForm.max_users}
                  </div>
                </div>
                <div className="bg-slate-900 p-3.5 rounded-xl border border-slate-800">
                  <div className="text-[10px] text-slate-400 uppercase">Bed Quota &amp; Census</div>
                  <div className="text-lg font-black text-cyan-300 mt-0.5">
                    {drawerTenant.metrics?.occupied_beds_count} occ /{" "}
                    {drawerTenant.metrics?.total_beds_count} ({drwForm.max_beds} cap)
                  </div>
                </div>
                <div className="bg-slate-900 p-3.5 rounded-xl border border-slate-800">
                  <div className="text-[10px] text-slate-400 uppercase">MPI Patients</div>
                  <div className="text-lg font-black text-white mt-0.5">
                    {drawerTenant.metrics?.patients_count}
                  </div>
                </div>
                <div className="bg-slate-900 p-3.5 rounded-xl border border-slate-800">
                  <div className="text-[10px] text-slate-400 uppercase">Active Encounters</div>
                  <div className="text-lg font-black text-emerald-400 mt-0.5">
                    {drawerTenant.metrics?.active_encounters_count}
                  </div>
                </div>
                <div className="bg-slate-900 p-3.5 rounded-xl border border-slate-800 col-span-2">
                  <div className="text-[10px] text-slate-400 uppercase">Gross Billed Revenue</div>
                  <div className="text-lg font-black text-white mt-0.5">
                    {fmtInr(drawerTenant.metrics?.gross_revenue_paise)}
                  </div>
                </div>
                <div className="bg-slate-900 p-3.5 rounded-xl border border-slate-800 col-span-2">
                  <div className="text-[10px] text-slate-400 uppercase">Collected Revenue (Paid)</div>
                  <div className="text-lg font-black text-amber-300 mt-0.5">
                    {fmtInr(drawerTenant.metrics?.collected_revenue_paise)}
                  </div>
                </div>
              </div>
            </div>

            {/* 2. Commercial Contract, Modules & Maintenance Freeze */}
            <div className="bg-slate-900/90 border border-slate-800 p-5 rounded-2xl space-y-4 text-xs">
              <div className="flex items-center justify-between">
                <h3 className="font-bold uppercase tracking-wider text-indigo-300">
                  2. Commercial Package, Module Entitlements &amp; Quotas
                </h3>
                <button
                  onClick={handleSaveEntitlements}
                  disabled={busyAction === "save-entitlements"}
                  className="px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-bold flex items-center gap-1.5"
                >
                  {busyAction === "save-entitlements" && (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  )}
                  <span>Save Contract &amp; Quotas</span>
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-5 gap-2.5">
                <div>
                  <label className="block text-[10px] uppercase text-slate-400 mb-1">
                    Pricing Model
                  </label>
                  <select
                    value={drwForm.pricing_model}
                    onChange={(e) =>
                      setDrwForm((p) => ({ ...p, pricing_model: e.target.value }))
                    }
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2"
                  >
                    {BUNDLES.map((b) => (
                      <option key={b.code} value={b.code}>
                        {b.code}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] uppercase text-slate-400 mb-1">
                    Billing Cycle
                  </label>
                  <select
                    value={drwForm.billing_cycle}
                    onChange={(e) =>
                      setDrwForm((p) => ({ ...p, billing_cycle: e.target.value }))
                    }
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2"
                  >
                    <option value="ANNUAL_LICENSE">ANNUAL_LICENSE</option>
                    <option value="MONTHLY_SUBSCRIPTION">MONTHLY_SUBSCRIPTION</option>
                    <option value="PER_ACTIVE_BED_MONTHLY">PER_ACTIVE_BED_MONTHLY</option>
                    <option value="ONE_TIME_PERPETUAL">ONE_TIME_PERPETUAL</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] uppercase text-slate-400 mb-1">
                    Contract Fee ({INR})
                  </label>
                  <input
                    type="number"
                    value={drwForm.custom_fee_inr}
                    onChange={(e) =>
                      setDrwForm((p) => ({ ...p, custom_fee_inr: e.target.value }))
                    }
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase text-slate-400 mb-1">
                    Max Bed Quota
                  </label>
                  <input
                    type="number"
                    value={drwForm.max_beds}
                    onChange={(e) => setDrwForm((p) => ({ ...p, max_beds: e.target.value }))}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase text-slate-400 mb-1">
                    Max Users Quota
                  </label>
                  <input
                    type="number"
                    value={drwForm.max_users}
                    onChange={(e) => setDrwForm((p) => ({ ...p, max_users: e.target.value }))}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {moduleCatalog.map((m) => (
                  <label
                    key={m.code}
                    className="flex items-center gap-1.5 bg-slate-950 p-2 rounded-lg border border-slate-800 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={drwForm.enabled_modules[m.code] !== false}
                      onChange={(e) =>
                        setDrwForm((p) => ({
                          ...p,
                          enabled_modules: {
                            ...p.enabled_modules,
                            [m.code]: e.target.checked,
                          },
                        }))
                      }
                    />
                    <span className="text-[11px]">{m.name}</span>
                  </label>
                ))}
              </div>

              <div className="pt-2 border-t border-slate-800 flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 font-bold text-amber-300 cursor-pointer">
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
                  placeholder="Custom maintenance banner message..."
                  className="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-1.5"
                />
              </div>
            </div>

            {/* 3. AES-256 Catastrophic Backup Vault & 1-Click Restore */}
            <div className="bg-slate-900/90 border border-slate-800 p-5 rounded-2xl space-y-3 text-xs">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-bold uppercase tracking-wider text-emerald-300">
                    3. AES-256 Encrypted Backup Vault &amp; 1-Click Restore
                  </h3>
                  <p className="text-[11px] text-slate-400">
                    Trigger an immediate snapshot or restore any historical .dump.enc file into PostgreSQL.
                  </p>
                </div>
                <button
                  onClick={async () => {
                    try {
                      const d = await apiCall(
                        `/api/tenants/${drawerTenant.subdomain_slug}/backups/trigger`,
                        {
                          method: "POST",
                          body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                        },
                        "trigger-backup"
                      );
                      notify(d.message);
                      await openTenantDrawer(drawerTenant.subdomain_slug);
                      await syncFleetAll(true);
                    } catch (err) {
                      notify(err.message, "error");
                    }
                  }}
                  disabled={busyAction === "trigger-backup"}
                  className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold flex items-center gap-1.5"
                >
                  {busyAction === "trigger-backup" && (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  )}
                  <span>+ Take Snapshot Now</span>
                </button>
              </div>

              <div className="space-y-2 max-h-44 overflow-y-auto font-mono">
                {(drawerTenant.snapshots || []).length === 0 ? (
                  <div className="text-slate-500 text-center py-3">
                    No encrypted .dump.enc snapshots found for this hospital yet.
                  </div>
                ) : (
                  drawerTenant.snapshots.map((s) => (
                    <div
                      key={s.filename}
                      className="flex items-center justify-between bg-slate-950 px-3.5 py-2 rounded-xl border border-slate-800"
                    >
                      <div>
                        <div className="text-[11px] text-white">{s.filename}</div>
                        <div className="text-[10px] text-slate-400">
                          {(s.size_bytes / 1024).toFixed(1)} KB &bull;{" "}
                          {new Date(s.created_at * 1000).toLocaleString()}
                        </div>
                      </div>
                      <button
                        onClick={async () => {
                          if (
                            !window.confirm(
                              `Confirm Point-in-Time Restore: Overwrite '${drawerTenant.db_name}' with '${s.filename}'?`
                            )
                          )
                            return;
                          try {
                            const d = await apiCall(
                              `/api/tenants/${drawerTenant.subdomain_slug}/backups/${encodeURIComponent(
                                s.filename
                              )}/restore`,
                              {
                                method: "POST",
                                body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                              },
                              `restore-${s.filename}`
                            );
                            notify(d.message);
                            await openTenantDrawer(drawerTenant.subdomain_slug);
                          } catch (err) {
                            notify(err.message, "error");
                          }
                        }}
                        disabled={busyAction === `restore-${s.filename}`}
                        className="px-3 py-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 text-[10px] font-bold flex items-center gap-1"
                      >
                        {busyAction === `restore-${s.filename}` && (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        )}
                        <span>Restore into DB</span>
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* 4. Zero-Trust Security Operations & UAT Sandbox Cloning */}
            <div className="bg-slate-900/90 border border-slate-800 p-5 rounded-2xl space-y-4 text-xs">
              <h3 className="font-bold uppercase tracking-wider text-rose-300">
                4. Zero-Trust Security Operations &amp; UAT Sandbox Cloning
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <button
                  onClick={async () => {
                    if (
                      !window.confirm(
                        `Rotate JWT, Backup & Federation keys for '${drawerTenant.subdomain_slug}'?`
                      )
                    )
                      return;
                    try {
                      const d = await apiCall(
                        `/api/tenants/${drawerTenant.subdomain_slug}/rotate-keys`,
                        {
                          method: "POST",
                          body: JSON.stringify({
                            master_setup_key: masterKey.trim(),
                            rotate_jwt_secret: true,
                            rotate_backup_key: true,
                            rotate_federation_key: true,
                          }),
                        },
                        "rotate-keys"
                      );
                      setDrawerOutput(d);
                      notify("Cryptographic keys rotated and sealed.");
                    } catch (err) {
                      notify(err.message, "error");
                    }
                  }}
                  className="p-3 rounded-xl bg-slate-950 hover:bg-slate-900 border border-slate-800 text-left"
                >
                  <div className="font-bold text-white">Rotate Cryptographic Keys</div>
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    Mints new JWT Secret, AES-256 Backup Key &amp; Federation Key.
                  </div>
                </button>

                <button
                  onClick={async () => {
                    try {
                      const d = await apiCall(
                        `/api/tenants/${drawerTenant.subdomain_slug}/reset-admin-password`,
                        {
                          method: "POST",
                          body: JSON.stringify({ master_setup_key: masterKey.trim() }),
                        },
                        "reset-pwd"
                      );
                      setDrawerOutput(d);
                      notify("Superuser account unlocked with new one-time password.");
                    } catch (err) {
                      notify(err.message, "error");
                    }
                  }}
                  className="p-3 rounded-xl bg-slate-950 hover:bg-slate-900 border border-slate-800 text-left"
                >
                  <div className="font-bold text-amber-300">Break-Glass Admin Password Reset</div>
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    Unlocks superuser account and generates a 1-time password.
                  </div>
                </button>

                <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex items-center gap-2">
                  <input
                    type="text"
                    value={drwForm.sandbox_slug}
                    onChange={(e) =>
                      setDrwForm((p) => ({ ...p, sandbox_slug: e.target.value }))
                    }
                    className="bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 font-mono text-xs flex-1"
                  />
                  <button
                    onClick={async () => {
                      try {
                        const d = await apiCall(
                          `/api/tenants/${drawerTenant.subdomain_slug}/clone-sandbox`,
                          {
                            method: "POST",
                            body: JSON.stringify({
                              master_setup_key: masterKey.trim(),
                              sandbox_subdomain_slug: drwForm.sandbox_slug.trim().toLowerCase(),
                            }),
                          },
                          "clone-uat"
                        );
                        setDrawerOutput(d);
                        notify(`Cloned UAT sandbox '${d.sandbox_subdomain_slug}'.`);
                        await syncFleetAll(true);
                      } catch (err) {
                        notify(err.message, "error");
                      }
                    }}
                    className="px-3 py-1.5 rounded bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold"
                  >
                    Clone UAT
                  </button>
                </div>

                <button
                  onClick={async () => {
                    try {
                      const d = await apiCall(
                        `/api/tenants/${drawerTenant.subdomain_slug}/status`,
                        {
                          method: "PATCH",
                          body: JSON.stringify({
                            master_setup_key: masterKey.trim(),
                            is_active: !drawerTenant.is_active,
                          }),
                        },
                        "toggle-status"
                      );
                      notify(d.message);
                      await openTenantDrawer(drawerTenant.subdomain_slug);
                      await syncFleetAll(true);
                    } catch (err) {
                      notify(err.message, "error");
                    }
                  }}
                  className={`p-3 rounded-xl border text-left font-bold ${
                    drawerTenant.is_active
                      ? "bg-rose-950/40 border-rose-500/40 text-rose-300"
                      : "bg-emerald-950/40 border-emerald-500/40 text-emerald-300"
                  }`}
                >
                  <div>
                    {drawerTenant.is_active
                      ? "Suspend Tenant (Circuit-Breaker)"
                      : "Reactivate Hospital Tenant"}
                  </div>
                  <div className="text-[10px] opacity-75 mt-0.5">
                    {drawerTenant.is_active
                      ? "Immediately blocks all API & Federation traffic with HTTP 403."
                      : "Restores normal API & Federation access immediately."}
                  </div>
                </button>
              </div>

              {drawerOutput && (
                <pre className="bg-slate-950 border border-cyan-500/40 rounded-xl p-3.5 font-mono text-[11px] text-cyan-200 overflow-x-auto">
                  {JSON.stringify(drawerOutput, null, 2)}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}