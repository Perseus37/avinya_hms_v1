from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from backend.control_plane.services.auth import verify_control_plane_secret

router = APIRouter(tags=["Control Plane UI Console"])

@router.get("/console", response_class=HTMLResponse, dependencies=[Depends(verify_control_plane_secret)])
def render_command_deck():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Avinya Nexus // Enterprise Multi-Tenant Control Plane v4.0</title>
  <link rel="stylesheet" href="/assets/legacy-console.css" />
  <style>
    body { background: radial-gradient(circle at top right, #0f172a, #020617 70%); color: #f1f5f9; font-family: ui-sans-serif, system-ui, sans-serif; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
    .glass { background: rgba(15, 23, 42, 0.78); backdrop-filter: blur(16px); border: 1px solid rgba(148, 163, 184, 0.12); }
    .glass-bright { background: rgba(30, 41, 59, 0.65); backdrop-filter: blur(16px); border: 1px solid rgba(56, 189, 248, 0.28); }
    .glow-cyan { box-shadow: 0 0 25px rgba(6, 182, 212, 0.12); }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  </style>
</head>
<body class="min-h-screen p-4 md:p-6">
  <div class="max-w-7xl mx-auto space-y-6">
    <!-- TOP COMMAND BAR -->
    <header class="glass glow-cyan rounded-2xl p-5 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center gap-4">
        <div class="w-12 h-12 rounded-2xl bg-cyan-500/15 border border-cyan-400/40 flex items-center justify-center text-cyan-300 text-2xl font-black">
          &#11041;
        </div>
        <div>
          <div class="flex items-center gap-2.5">
            <h1 class="text-lg md:text-xl font-black tracking-wide text-white">AVINYA NEXUS // CLOUD CONTROL PLANE</h1>
            <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">v4.0 MODULAR</span>
          </div>
          <p class="text-xs text-slate-400 mt-0.5">Zero-DB-Touch Provisioning, Modular &amp; Bundle Licensing, Staged Canary Migrations &amp; Federation Topology</p>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2.5">
        <input type="password" id="masterKey" placeholder="Enter Master Vendor Key..."
          class="bg-slate-950/90 border border-slate-700 rounded-xl px-3.5 py-2 text-xs mono text-cyan-200 focus:border-cyan-400 outline-none w-56" />
        <input type="password" id="cpSecretKey" placeholder="Control-Plane Secret (Optional)"
          class="bg-slate-950/90 border border-slate-800 rounded-xl px-3 py-2 text-xs mono text-slate-300 focus:border-cyan-400 outline-none w-48" />
        <button onclick="authenticateAndSync()" class="bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-black px-4 py-2 rounded-xl text-xs uppercase tracking-wider transition">
          Connect &amp; Sync Fleet
        </button>
      </div>
    </header>

    <!-- TOAST NOTIFICATION BANNER -->
    <div id="toastBanner" class="hidden rounded-xl p-3.5 text-xs font-semibold flex items-center justify-between border"></div>

    <!-- GLOBAL FLEET PULSE BAR -->
    <section class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <div class="glass p-4 rounded-xl">
        <div class="text-[10px] font-bold uppercase text-slate-400">Active / Total Hospitals</div>
        <div id="kpiHospitals" class="text-2xl font-black text-cyan-300 mt-1 mono">-- / --</div>
      </div>
      <div class="glass p-4 rounded-xl">
        <div class="text-[10px] font-bold uppercase text-slate-400">Fleet Bed Occupancy</div>
        <div id="kpiOccupancy" class="text-2xl font-black text-emerald-400 mt-1 mono">--%</div>
        <div id="kpiBedCounts" class="text-[10px] text-slate-400 mt-0.5 mono">0 / 0 Beds</div>
      </div>
      <div class="glass p-4 rounded-xl">
        <div class="text-[10px] font-bold uppercase text-slate-400">Annual License ARR</div>
        <div id="kpiArr" class="text-2xl font-black text-cyan-200 mt-1 mono">&#8377;--</div>
      </div>
      <div class="glass p-4 rounded-xl">
        <div class="text-[10px] font-bold uppercase text-slate-400">Global MPI Pointers</div>
        <div id="kpiPointers" class="text-2xl font-black text-indigo-400 mt-1 mono">--</div>
      </div>
      <div class="glass p-4 rounded-xl">
        <div class="text-[10px] font-bold uppercase text-slate-400">Hospital GMV Volume</div>
        <div id="kpiRevenue" class="text-2xl font-black text-amber-300 mt-1 mono">&#8377;--</div>
      </div>
      <div class="glass p-4 rounded-xl">
        <div class="text-[10px] font-bold uppercase text-slate-400">Encrypted Snapshots</div>
        <div id="kpiSnapshots" class="text-2xl font-black text-rose-400 mt-1 mono">--</div>
      </div>
    </section>

    <!-- 5-TAB NAVIGATION BAR -->
    <nav class="flex flex-wrap gap-2 border-b border-slate-800 pb-3">
      <button onclick="switchTab('fleet')" id="tabBtn-fleet" class="px-4 py-2 rounded-xl text-xs font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-400/40">
        1. Active Hospital Fleet &amp; Inspector
      </button>
      <button onclick="switchTab('provision')" id="tabBtn-provision" class="px-4 py-2 rounded-xl text-xs font-bold bg-slate-900 text-slate-400 hover:text-white border border-slate-800">
        2. Modular &amp; Bundle Onboarding Studio
      </button>
      <button onclick="switchTab('schema')" id="tabBtn-schema" class="px-4 py-2 rounded-xl text-xs font-bold bg-slate-900 text-slate-400 hover:text-white border border-slate-800">
        3. Staged Canary &amp; Fleet Schema Sync
      </button>
      <button onclick="switchTab('topology')" id="tabBtn-topology" class="px-4 py-2 rounded-xl text-xs font-bold bg-slate-900 text-slate-400 hover:text-white border border-slate-800">
        4. Federation Network &amp; Sovereignty Topology
      </button>
      <button onclick="switchTab('audit')" id="tabBtn-audit" class="px-4 py-2 rounded-xl text-xs font-bold bg-slate-900 text-slate-400 hover:text-white border border-slate-800">
        5. Immutable Vendor Audit Trail
      </button>
    </nav>

    <!-- ================================================================= -->
    <!-- TAB 1: ACTIVE HOSPITAL FLEET MATRIX                               -->
    <!-- ================================================================= -->
    <section id="tab-fleet" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <input type="text" id="fleetSearchInput" oninput="renderFleetCards()" placeholder="Filter hospitals by name, slug, branch code, or bundle..."
          class="bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-xs w-80 outline-none focus:border-cyan-400" />
        <div class="text-xs text-slate-400">Click <span class="text-cyan-300 font-bold">Manage &amp; Inspect</span> on any hospital to modify licensed modules, quotas, or backups.</div>
      </div>
      <div id="fleetCardsGrid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div class="col-span-full glass rounded-2xl p-10 text-center text-sm text-slate-400">
          Enter your <span class="text-cyan-300 font-mono">Master Vendor Key</span> in the top bar and click <b>Connect &amp; Sync Fleet</b>.
        </div>
      </div>
    </section>

    <!-- ================================================================= -->
    <!-- TAB 2: MODULAR & BUNDLE HOSPITAL ONBOARDING STUDIO                -->
    <!-- ================================================================= -->
    <section id="tab-provision" class="hidden grid grid-cols-1 lg:grid-cols-12 gap-6">
      <div class="lg:col-span-7 glass rounded-2xl p-6 space-y-5">
        <div>
          <h2 class="text-base font-bold text-white">Launch Isolated Hospital Cloud Tenant</h2>
          <p class="text-xs text-slate-400">Choose a starter spatial blueprint (or Blank Canvas) and configure either a discounted Commercial Bundle or an &Agrave; La Carte Module package.</p>
        </div>

        <form id="provisionForm" class="space-y-4 text-xs">
          <!-- 1. Spatial Blueprint -->
          <div>
            <label class="block text-[11px] font-bold uppercase text-slate-400 mb-2">1. Select Starter Spatial Blueprint (Hospital Admin Can Modify Anytime)</label>
            <div id="provBlueprintsGrid" class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <div class="p-3 text-slate-500">Syncing blueprints from API...</div>
            </div>
          </div>

          <!-- 2. Modular & Bundle Pricing Selector -->
          <div class="space-y-3 pt-2 border-t border-slate-800">
            <div class="flex items-center justify-between">
              <label class="block text-[11px] font-bold uppercase text-slate-400">2. Select Commercial Bundle or &Agrave; La Carte Modules</label>
              <div id="calculatedFeeDisplay" class="text-xs font-black mono text-cyan-300">Standard Fee: &#8377;0 / yr</div>
            </div>
            <div id="provBundlesGrid" class="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs"></div>
            <div id="provModulesGrid" class="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-slate-950/90 p-3.5 rounded-xl border border-slate-800 text-[11px]"></div>
          </div>

          <!-- 3. Identity & Contract Quotas -->
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-800">
            <div>
              <label class="block text-[10px] font-bold uppercase text-slate-400 mb-1">Hospital Legal Name</label>
              <input type="text" id="provHospitalName" required placeholder="e.g. KIMS Icon Hospital"
                class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 outline-none focus:border-cyan-400" />
            </div>
            <div>
              <label class="block text-[10px] font-bold uppercase text-slate-400 mb-1">Subdomain Slug</label>
              <input type="text" id="provSlug" required placeholder="e.g. kims"
                class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 mono lowercase outline-none focus:border-cyan-400" />
            </div>
            <div>
              <label class="block text-[10px] font-bold uppercase text-slate-400 mb-1">Branch Code</label>
              <input type="text" id="provBranchCode" required placeholder="e.g. KIMS-VIZ-01"
                class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 mono uppercase outline-none focus:border-cyan-400" />
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-4 gap-3">
            <div>
              <label class="block text-[10px] font-bold uppercase text-slate-400 mb-1">Superuser Admin Email</label>
              <input type="email" id="provAdminEmail" required placeholder="admin@hospital.com"
                class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 outline-none focus:border-cyan-400" />
            </div>
            <div>
              <label class="block text-[10px] font-bold uppercase text-slate-400 mb-1">Billing Cycle</label>
              <select id="provBillingCycle" class="w-full bg-slate-950 border border-slate-700 rounded-xl px-2.5 py-2 outline-none focus:border-cyan-400">
                <option value="ANNUAL_LICENSE" selected>Annual License</option>
                <option value="MONTHLY_SUBSCRIPTION">Monthly</option>
                <option value="PER_ACTIVE_BED_MONTHLY">Per-Bed Monthly</option>
                <option value="ONE_TIME_PERPETUAL">Perpetual + AMC</option>
              </select>
            </div>
            <div>
              <label class="block text-[10px] font-bold uppercase text-slate-400 mb-1">Custom Fee (&#8377; Optional)</label>
              <input type="number" id="provCustomFeeInr" placeholder="Auto from Bundle"
                class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 mono" />
            </div>
            <div>
              <label class="block text-[10px] font-bold uppercase text-slate-400 mb-1">Bed / User Quotas</label>
              <div class="flex gap-1.5">
                <input type="number" id="provMaxBeds" value="500" title="Max Beds" class="w-1/2 bg-slate-950 border border-slate-700 rounded-xl px-2 py-2 mono" />
                <input type="number" id="provMaxUsers" value="200" title="Max Users" class="w-1/2 bg-slate-950 border border-slate-700 rounded-xl px-2 py-2 mono" />
              </div>
            </div>
          </div>

          <!-- 4. Master Clinical Dictionaries Pre-Seeding -->
          <div class="bg-slate-950/80 border border-slate-800 rounded-xl p-3 flex flex-wrap items-center justify-between gap-2">
            <span class="text-[10px] font-bold uppercase text-slate-400">Pre-Seed Clinical Dictionaries:</span>
            <div class="flex flex-wrap gap-4 text-[11px]">
              <label class="flex items-center gap-1.5"><input type="checkbox" id="provSeedIcd10" checked /><span>WHO ICD-10 Diagnoses</span></label>
              <label class="flex items-center gap-1.5"><input type="checkbox" id="provSeedAllergies" checked /><span>CPOE Drug/Food Allergies</span></label>
              <label class="flex items-center gap-1.5"><input type="checkbox" id="provSeedComplaints" checked /><span>Complaints &amp; Physical Exams</span></label>
            </div>
          </div>

          <button type="submit" id="provSubmitBtn" class="w-full bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-black py-3 rounded-xl uppercase tracking-wider transition shadow-lg">
            Execute Zero-Touch Tenant Provisioning
          </button>
        </form>
      </div>

      <!-- RIGHT: LIVE DEVOPS PROVISIONING TERMINAL & CREDENTIAL VAULT -->
      <div class="lg:col-span-5 glass rounded-2xl p-6 flex flex-col justify-between space-y-4">
        <div>
          <div class="flex items-center justify-between border-b border-slate-800 pb-3 mb-3">
            <span class="text-xs font-bold uppercase tracking-wider text-cyan-300 mono">Provisioning Execution Telemetry</span>
            <span id="provStatusBadge" class="px-2 py-0.5 rounded text-[10px] mono bg-slate-800 text-slate-400">STANDBY</span>
          </div>
          <div id="provTerminalLog" class="bg-slate-950 border border-slate-800 rounded-xl p-4 h-64 overflow-y-auto mono text-[11px] space-y-1.5 text-slate-400">
            <div>[SYSTEM] Ready for zero-touch tenant orchestration.</div>
            <div>[SYSTEM] Select a spatial blueprint and module package to begin.</div>
          </div>
        </div>

        <div id="provCredentialVault" class="hidden bg-emerald-950/30 border border-emerald-500/40 rounded-xl p-4 space-y-2 text-xs">
          <div class="flex items-center justify-between">
            <span class="font-bold text-emerald-300 uppercase">Provisioned Security Envelope (Copy Now)</span>
            <button onclick="copyProvisionedJson()" class="px-2.5 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-200 text-[10px] font-bold">Copy JSON</button>
          </div>
          <div id="provVaultBody" class="mono text-[11px] space-y-1 text-slate-200 break-all"></div>
        </div>
      </div>
    </section>

    <!-- ================================================================= -->
    <!-- TAB 3: STAGED CANARY & FLEET SCHEMA SYNC                          -->
    <!-- ================================================================= -->
    <section id="tab-schema" class="hidden glass rounded-2xl p-6 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h2 class="text-base font-bold text-white">Staged Canary Migration &amp; Fleet Template Synchronizer</h2>
          <p class="text-xs text-slate-400">Stage 1: Upgrade &amp; test <span class="mono text-cyan-300">default</span> (or any single tenant) first. Stage 2: Push the verified <span class="mono">head</span> revision to all remaining hospitals.</p>
        </div>
        <div class="flex gap-2.5">
          <button onclick="loadSchemaStatus()" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-bold">Refresh Schema Parity</button>
          <button onclick="syncEntireFleet()" class="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold">Stage 2: Push Head &amp; Templates to Entire Fleet</button>
        </div>
      </div>
      <div id="schemaTableContainer" class="overflow-x-auto text-xs"></div>
    </section>

    <!-- ================================================================= -->
    <!-- TAB 4: ZERO-KNOWLEDGE FEDERATION NETWORK TOPOLOGY                 -->
    <!-- ================================================================= -->
    <section id="tab-topology" class="hidden glass rounded-2xl p-6 space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h2 class="text-base font-bold text-white">Federation Network &amp; Data Sovereignty Topology</h2>
          <p class="text-xs text-slate-400">Zero-Knowledge overview of connected hospital nodes and their configured data-sharing mode (<span class="mono">FULL_MESH</span>, <span class="mono">CORPORATE_GROUP_ONLY</span>, <span class="mono">EMERGENCY_ALLERGIES_ONLY</span>, or <span class="mono">ISOLATED_SILO</span>). Individual patient searches happen strictly inside the Hospital EMR.</p>
        </div>
        <button onclick="loadFederationTopology()" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-bold">Refresh Mesh</button>
      </div>
      <div id="topologyTableContainer" class="overflow-x-auto text-xs"></div>
    </section>

    <!-- ================================================================= -->
    <!-- TAB 5: IMMUTABLE VENDOR AUDIT TRAIL                               -->
    <!-- ================================================================= -->
    <section id="tab-audit" class="hidden glass rounded-2xl p-6 space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <h2 class="text-base font-bold text-white">Immutable Control Plane Audit Log</h2>
          <p class="text-xs text-slate-400">Forensic ledger of all provisioning, module contract updates, key rotations, snapshot restores, and circuit-breaker events.</p>
        </div>
        <button onclick="loadAuditLogs()" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-bold">Refresh Ledger</button>
      </div>
      <div id="auditLogsContainer" class="overflow-x-auto text-xs"></div>
    </section>
  </div>

  <!-- =================================================================== -->
  <!-- SLIDE-OVER TENANT COMMAND DRAWER (INSPECT / ENTITLEMENTS / BACKUPS) -->
  <!-- =================================================================== -->
  <div id="tenantDrawerBackdrop" class="hidden fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex justify-end">
    <div class="w-full max-w-3xl bg-slate-950 border-l border-slate-800 h-full overflow-y-auto p-6 space-y-6">
      <div class="flex items-start justify-between border-b border-slate-800 pb-4">
        <div>
          <div class="flex items-center gap-2.5">
            <h2 id="drawerHospitalName" class="text-xl font-black text-white">Hospital Name</h2>
            <span id="drawerStatusBadge" class="px-2.5 py-0.5 rounded-full text-[10px] font-bold mono">ACTIVE</span>
            <span id="drawerSovereigntyBadge" class="px-2.5 py-0.5 rounded-full text-[10px] font-bold mono bg-cyan-500/20 text-cyan-300">FULL_MESH</span>
          </div>
          <p id="drawerSubMeta" class="text-xs text-slate-400 mono mt-1"></p>
        </div>
        <button onclick="closeTenantDrawer()" class="text-slate-400 hover:text-white text-xl font-bold px-2.5 py-1 rounded-lg hover:bg-slate-900">&times;</button>
      </div>

      <!-- 1. LIVE DATA-PLANE TELEMETRY STRIP -->
      <div>
        <h3 class="text-xs font-bold uppercase tracking-wider text-cyan-300 mb-2.5">1. Live Data-Plane Telemetry</h3>
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div class="glass p-3.5 rounded-xl">
            <div class="text-[10px] text-slate-400 uppercase">Staff Users</div>
            <div id="drwUsers" class="text-lg font-black text-white mono mt-0.5">--</div>
          </div>
          <div class="glass p-3.5 rounded-xl">
            <div class="text-[10px] text-slate-400 uppercase">Registered Patients</div>
            <div id="drwPatients" class="text-lg font-black text-white mono mt-0.5">--</div>
          </div>
          <div class="glass p-3.5 rounded-xl">
            <div class="text-[10px] text-slate-400 uppercase">Active Encounters</div>
            <div id="drwEncounters" class="text-lg font-black text-emerald-400 mono mt-0.5">--</div>
          </div>
          <div class="glass p-3.5 rounded-xl">
            <div class="text-[10px] text-slate-400 uppercase">Occupied / Total Beds</div>
            <div id="drwBeds" class="text-lg font-black text-cyan-300 mono mt-0.5">-- / --</div>
          </div>
          <div class="glass p-3.5 rounded-xl col-span-2">
            <div class="text-[10px] text-slate-400 uppercase">Gross Billed Revenue</div>
            <div id="drwGrossRev" class="text-lg font-black text-white mono mt-0.5">&#8377;0</div>
          </div>
          <div class="glass p-3.5 rounded-xl col-span-2">
            <div class="text-[10px] text-slate-400 uppercase">Collected Revenue (Paid)</div>
            <div id="drwCollectedRev" class="text-lg font-black text-amber-300 mono mt-0.5">&#8377;0</div>
          </div>
        </div>
      </div>

      <!-- 2. MODULAR & BUNDLE ENTITLEMENTS, QUOTAS & MAINTENANCE MODE -->
      <div class="glass p-5 rounded-2xl space-y-4 text-xs">
        <div class="flex items-center justify-between">
          <h3 class="font-bold uppercase tracking-wider text-indigo-300">2. Licensed Modules, Commercial Contract &amp; Maintenance Freeze</h3>
          <button onclick="saveTenantEntitlements()" class="px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-bold">Save Contract &amp; Modules</button>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-5 gap-2.5">
          <div>
            <label class="block text-[10px] uppercase text-slate-400 mb-1">Pricing Model</label>
            <select id="drwPricingModel" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2" onchange="selectBundlePreset(this.value, true)">
            </select>
          </div>
          <div>
            <label class="block text-[10px] uppercase text-slate-400 mb-1">Billing Cycle</label>
            <select id="drwBillingCycle" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2">
              <option value="ANNUAL_LICENSE">ANNUAL_LICENSE</option>
              <option value="MONTHLY_SUBSCRIPTION">MONTHLY_SUBSCRIPTION</option>
              <option value="PER_ACTIVE_BED_MONTHLY">PER_ACTIVE_BED_MONTHLY</option>
              <option value="ONE_TIME_PERPETUAL">ONE_TIME_PERPETUAL</option>
            </select>
          </div>
          <div>
            <label class="block text-[10px] uppercase text-slate-400 mb-1">Contract Fee (&#8377;)</label>
            <input type="number" id="drwContractFeeInr" placeholder="Auto" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2 mono" />
          </div>
          <div>
            <label class="block text-[10px] uppercase text-slate-400 mb-1">Max Bed Quota</label>
            <input type="number" id="drwQuotaBeds" value="500" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2 mono" />
          </div>
          <div>
            <label class="block text-[10px] uppercase text-slate-400 mb-1">Max Staff Users</label>
            <input type="number" id="drwQuotaUsers" value="200" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2 mono" />
          </div>
        </div>

        <div>
          <label class="block text-[10px] uppercase text-slate-400 mb-2">Active Licensed Modules (Unchecked modules are blocked with HTTP 403)</label>
          <div id="drwModulesGrid" class="grid grid-cols-2 sm:grid-cols-4 gap-2"></div>
        </div>

        <div class="pt-2 border-t border-slate-800 flex flex-wrap items-center gap-3">
          <label class="flex items-center gap-2 font-bold text-amber-300">
            <input type="checkbox" id="drwMaintenanceMode" />
            <span>Read-Only Maintenance Freeze (Blocks Writes with HTTP 503)</span>
          </label>
          <input type="text" id="drwMaintenanceNotice" placeholder="Custom maintenance notice banner..."
            class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5" />
        </div>
      </div>

      <!-- 3. ZERO-TOUCH DISASTER RECOVERY SNAPSHOT VAULT -->
      <div class="glass p-5 rounded-2xl space-y-3 text-xs">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="font-bold uppercase tracking-wider text-emerald-300">3. AES-256 Catastrophic Backup Vault &amp; 1-Click Restore</h3>
            <p class="text-[11px] text-slate-400">Trigger an immediate encrypted snapshot or restore any historical <span class="mono">.dump.enc</span> snapshot into PostgreSQL.</p>
          </div>
          <button onclick="triggerDrawerBackup()" class="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold">
            + Take Snapshot Now
          </button>
        </div>
        <div id="drwSnapshotsList" class="space-y-2 max-h-44 overflow-y-auto"></div>
      </div>

      <!-- 4. DAY-2 SECURITY, KEY ROTATION, SANDBOX CLONE & CIRCUIT BREAKER -->
      <div class="glass p-5 rounded-2xl space-y-4 text-xs">
        <h3 class="font-bold uppercase tracking-wider text-rose-300">4. Zero-Trust Security Operations &amp; UAT Sandbox Cloning</h3>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <button onclick="rotateDrawerKeys()" class="p-3 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-700 text-left">
            <div class="font-bold text-white">Rotate Cryptographic Keys</div>
            <div class="text-[10px] text-slate-400 mt-0.5">Mints new JWT Secret, AES-256 Backup Key &amp; Federation Key.</div>
          </button>
          <button onclick="resetDrawerAdminPassword()" class="p-3 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-700 text-left">
            <div class="font-bold text-amber-300">Break-Glass Admin Password Reset</div>
            <div class="text-[10px] text-slate-400 mt-0.5">Unlocks superuser account and generates a 1-time password.</div>
          </button>

          <div class="p-3 rounded-xl bg-slate-900 border border-slate-700 flex items-center gap-2">
            <!-- FIX: Strict length bounds for sandbox slug to prevent 422 errors -->
            <input type="text" id="drwSandboxSlug" maxlength="50" placeholder="sandbox-slug" class="bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 mono text-xs flex-1" />
            <button onclick="cloneDrawerSandbox()" class="px-3 py-1.5 rounded bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold">Clone UAT</button>
          </div>

          <button onclick="toggleDrawerCircuitBreaker()" id="drwCircuitBtn" class="p-3 rounded-xl border text-left font-bold"></button>
        </div>
        <div id="drwActionOutput" class="hidden bg-slate-950 border border-cyan-500/40 rounded-xl p-3.5 mono text-[11px] text-cyan-200 break-all"></div>
      </div>

    </div>
  </div>

  <script>
    const INR_SYM = "\u20B9";
    let fleetCache = [];
    let activeDrawerTenant = null;
    let lastProvisionedResult = null;
    let activeBundlePreset = "BUNDLE_TOTAL_HOSPITAL";

    // Dynamic Catalogs
    let moduleCatalog = [];
    let bundleCatalog = [];
    let blueprintCatalog = [];

    // Safe clipboard fallback for HTTP environments
    function fallbackCopyTextToClipboard(text) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(() => showToast("Copied to clipboard.")).catch(() => showToast("Failed to copy.", true));
      } else {
        const textArea = document.createElement("textarea");
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
          document.execCommand('copy');
          showToast("Copied to clipboard securely.");
        } catch (err) {
          showToast("Failed to copy text.", true);
        }
        document.body.removeChild(textArea);
      }
    }

    function getHeaders(isJson = false) {
      const h = { "X-Master-Setup-Key": document.getElementById("masterKey").value.trim() };
      const cp = document.getElementById("cpSecretKey").value.trim();
      if (cp) h["X-Control-Plane-Secret"] = cp;
      if (isJson) h["Content-Type"] = "application/json";
      return h;
    }

    function showToast(msg, isError = false) {
      const box = document.getElementById("toastBanner");
      box.className = `rounded-xl p-3.5 text-xs font-semibold flex items-center justify-between border ${
        isError ? "bg-rose-500/15 border-rose-500/40 text-rose-200" : "bg-emerald-500/15 border-emerald-500/40 text-emerald-200"
      }`;
      box.innerHTML = `<span>${msg}</span><button onclick="this.parentElement.classList.add('hidden')" class="px-2 font-bold">&times;</button>`;
      box.classList.remove("hidden");
    }

    function fmtInr(paise) {
      return INR_SYM + ((paise || 0) / 100).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    }

    function renderDynamicCatalogs() {
      // Render Blueprints
      const bpGrid = document.getElementById("provBlueprintsGrid");
      bpGrid.innerHTML = blueprintCatalog.map((bp, i) => `
        <label class="${i === 0 ? 'glass-bright' : 'glass hover:border-cyan-400/40'} p-3 rounded-xl cursor-pointer block ${bp.key === 'BLANK_CANVAS' ? 'sm:col-span-2' : ''}">
          <input type="radio" name="blueprint_archetype" value="${bp.key}" ${i === 0 ? 'checked' : ''} class="mr-1.5" />
          <span class="font-bold ${bp.key === 'BLANK_CANVAS' ? 'text-cyan-300' : 'text-white'}">${bp.name}</span>
          <p class="text-[10px] text-slate-300 mt-0.5">${bp.description}</p>
        </label>
      `).join("");

      // Render Bundles
      const bundleGrid = document.getElementById("provBundlesGrid");
      bundleGrid.innerHTML = bundleCatalog.map(b => `
        <button type="button" onclick="selectBundlePreset('${b.code}')" id="bPreset-${b.code}" class="p-2.5 rounded-xl border border-slate-800 bg-slate-900 text-slate-300 text-left">
          <div class="font-bold">${b.name}</div>
          <div class="text-[10px] text-slate-400">(${fmtInr(b.bundle_annual_price_paise * 100)})</div>
        </button>
      `).join("");

      // Render Modules
      const modGrid = document.getElementById("provModulesGrid");
      modGrid.innerHTML = moduleCatalog.map(m => `
        <label class="flex items-center gap-1.5"><input type="checkbox" name="module_item" value="${m.code}" onchange="onModuleTick()" checked /><span>${m.name} (${fmtInr(m.base_annual_price_paise * 100)})</span></label>
      `).join("");

      // Update Drawer Select
      const drwSelect = document.getElementById("drwPricingModel");
      drwSelect.innerHTML = bundleCatalog.map(b => `<option value="${b.code}">${b.name}</option>`).join("");

      selectBundlePreset(bundleCatalog.length > 0 ? bundleCatalog[0].code : "CUSTOM_A_LA_CARTE");
    }

    function selectBundlePreset(preset, isDrawer = false) {
      if (!isDrawer) activeBundlePreset = preset;

      if (!isDrawer) {
        document.querySelectorAll("[id^='bPreset-']").forEach(el => {
          el.className = "p-2.5 rounded-xl border border-slate-800 bg-slate-900 text-slate-300 text-left";
        });
        const btn = document.getElementById(`bPreset-${preset}`);
        if (btn) btn.className = "p-2.5 rounded-xl border border-cyan-400/40 bg-cyan-500/20 text-cyan-200 text-left";
      }

      if (preset !== "CUSTOM_A_LA_CARTE") {
        const bundle = bundleCatalog.find(b => b.code === preset);
        const targets = bundle ? bundle.modules : [];
        const selector = isDrawer ? "#drwModulesGrid input[data-mod]" : "input[name='module_item']";
        document.querySelectorAll(selector).forEach(cb => {
          cb.checked = targets.includes(cb.value || cb.getAttribute("data-mod"));
        });
      }

      if (!isDrawer) recalculatePrice();
    }

    function onModuleTick() {
      selectBundlePreset("CUSTOM_A_LA_CARTE");
    }

    function recalculatePrice() {
      const selected = Array.from(document.querySelectorAll("input[name='module_item']:checked")).map(cb => cb.value);
      const bundle = bundleCatalog.find(b => b.code === activeBundlePreset);

      let totalInr = bundle ? bundle.bundle_annual_price_paise : 0;
      if (activeBundlePreset === "CUSTOM_A_LA_CARTE" || !bundle) {
        totalInr = selected.reduce((sum, mCode) => {
          const m = moduleCatalog.find(mod => mod.code === mCode);
          return sum + (m ? m.base_annual_price_paise : 0);
        }, 0);
      }
      document.getElementById("calculatedFeeDisplay").innerText = `Standard Fee: ${fmtInr(totalInr * 100)} / yr`;
    }

    function switchTab(tab) {
      ["fleet", "provision", "schema", "topology", "audit"].forEach(t => {
        document.getElementById(`tab-${t}`).classList.toggle("hidden", t !== tab);
        const btn = document.getElementById(`tabBtn-${t}`);
        btn.className = t === tab
          ? "px-4 py-2 rounded-xl text-xs font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-400/40"
          : "px-4 py-2 rounded-xl text-xs font-bold bg-slate-900 text-slate-400 hover:text-white border border-slate-800";
      });
      if (tab === "schema") loadSchemaStatus();
      if (tab === "topology") loadFederationTopology();
      if (tab === "audit") loadAuditLogs();
    }

    async function authenticateAndSync() {
      try {
        const [tRes, oRes, priceRes, bpRes] = await Promise.all([
          fetch("/api/tenants", { headers: getHeaders() }),
          fetch("/api/fleet/overview", { headers: getHeaders() }),
          fetch("/api/pricing/catalog", { headers: getHeaders() }),
          fetch("/api/blueprints", { headers: getHeaders() })
        ]);

        const tData = await tRes.json();
        if (!tRes.ok) throw new Error(tData.detail || "Authentication failed");

        const oData = await oRes.json();
        const pData = await priceRes.json();
        const bData = await bpRes.json();

        // Replace the old assignment block with this:
        fleetCache = tData.items || [];
        // Safely convert dictionary catalogs to arrays for the UI to map over
        moduleCatalog = Array.isArray(pData.modules) ? pData.modules : Object.values(pData.modules || {});
        bundleCatalog = Array.isArray(pData.bundles) ? pData.bundles : Object.values(pData.bundles || {});
        blueprintCatalog = Array.isArray(bData.blueprints) ? bData.blueprints : Object.values(bData.blueprints || {});

        document.getElementById("kpiHospitals").innerText = `${oData.active_hospitals} / ${oData.total_hospitals}`;
        document.getElementById("kpiOccupancy").innerText = `${oData.fleet_bed_occupancy_pct}%`;
        document.getElementById("kpiBedCounts").innerText = `${oData.occupied_beds} / ${oData.total_beds} Beds`;
        document.getElementById("kpiArr").innerText = fmtInr(oData.vendor_annual_license_arr_paise);
        document.getElementById("kpiPointers").innerText = oData.global_patient_pointers_indexed.toLocaleString("en-IN");
        document.getElementById("kpiRevenue").innerText = fmtInr(oData.fleet_gross_collected_revenue_paise);
        document.getElementById("kpiSnapshots").innerText = oData.encrypted_disaster_snapshots_count;

        renderDynamicCatalogs();
        renderFleetCards();
        showToast(`Synced ${fleetCache.length} hospital tenants and catalogs from Control Plane.`);
      } catch (err) {
        showToast(err.message, true);
      }
    }

    function renderFleetCards() {
      const q = (document.getElementById("fleetSearchInput").value || "").toLowerCase();
      const filtered = fleetCache.filter(t =>
        t.hospital_name.toLowerCase().includes(q) ||
        t.subdomain_slug.toLowerCase().includes(q) ||
        t.branch_code.toLowerCase().includes(q) ||
        (t.pricing_model || "").toLowerCase().includes(q)
      );

      const grid = document.getElementById("fleetCardsGrid");
      if (!filtered.length) {
        grid.innerHTML = `<div class="col-span-full glass rounded-2xl p-8 text-center text-xs text-slate-400">No matching hospital tenants found.</div>`;
        return;
      }

      grid.innerHTML = filtered.map(t => `
        <div class="glass rounded-2xl p-5 flex flex-col justify-between space-y-4 border ${
          !t.is_active ? "border-rose-500/40" : t.maintenance_mode ? "border-amber-500/40" : "border-slate-800 hover:border-cyan-500/40"
        } transition">
          <div>
            <div class="flex items-start justify-between gap-2">
              <div>
                <h3 class="font-bold text-sm text-white">${t.hospital_name}</h3>
                <div class="text-[11px] text-slate-400 mono mt-0.5">${t.subdomain_slug} &bull; ${t.branch_code}</div>
              </div>
              <span class="px-2 py-0.5 rounded-full text-[10px] font-bold mono ${
                !t.is_active ? "bg-rose-500/20 text-rose-300" : t.maintenance_mode ? "bg-amber-500/20 text-amber-300" : "bg-emerald-500/20 text-emerald-300"
              }">
                ${!t.is_active ? "SUSPENDED" : t.maintenance_mode ? "MAINTENANCE" : "ACTIVE"}
              </span>
            </div>
            <div class="grid grid-cols-2 gap-2 mt-4 bg-slate-950/70 p-3 rounded-xl text-[11px] mono">
              <div><span class="text-slate-500">DB:</span> <span class="text-slate-200">${t.db_name}</span></div>
              <div><span class="text-slate-500">Modules:</span> <span class="text-emerald-300">${t.active_modules_count} Active</span></div>
              <div><span class="text-slate-500">Package:</span> <span class="text-cyan-300 truncate" title="${t.pricing_model}">${t.pricing_model}</span></div>
              <div><span class="text-slate-500">Fee:</span> <span class="text-amber-300">${fmtInr(t.base_license_fee_paise)}</span></div>
            </div>
          </div>
          <div class="flex items-center justify-between pt-2 border-t border-slate-800/80 text-xs">
            <span class="text-[10px] text-slate-500 mono">MPI Pointers: ${t.global_patient_pointers_count}</span>
            <button onclick="openTenantDrawer('${t.subdomain_slug}')" class="px-3.5 py-1.5 rounded-xl bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 font-bold text-xs transition">
              Manage &amp; Inspect &rarr;
            </button>
          </div>
        </div>
      `).join("");
    }

    async function openTenantDrawer(slug) {
      try {
        const res = await fetch(`/api/tenants/${slug}/details`, { headers: getHeaders() });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || "Failed to load tenant details");

        activeDrawerTenant = d;
        document.getElementById("drawerHospitalName").innerText = d.hospital_name;
        document.getElementById("drawerSubMeta").innerText = `Subdomain: ${d.subdomain_slug} | Branch: ${d.branch_code} | DB: ${d.db_name} | Admin: ${d.admin_email}`;

        const badge = document.getElementById("drawerStatusBadge");
        badge.innerText = !d.is_active ? "SUSPENDED" : d.maintenance_mode ? "MAINTENANCE" : "ACTIVE";
        badge.className = `px-2.5 py-0.5 rounded-full text-[10px] font-bold mono ${
          !d.is_active ? "bg-rose-500/20 text-rose-300" : d.maintenance_mode ? "bg-amber-500/20 text-amber-300" : "bg-emerald-500/20 text-emerald-300"
        }`;

        const sovBadge = document.getElementById("drawerSovereigntyBadge");
        const sovMode = (d.hospital_policy && d.hospital_policy.federation_sharing_mode) || "FULL_MESH";
        sovBadge.innerText = `MPI: ${sovMode}`;

        document.getElementById("drwUsers").innerText = d.metrics.users_count;
        document.getElementById("drwPatients").innerText = d.metrics.patients_count;
        document.getElementById("drwEncounters").innerText = d.metrics.active_encounters_count;
        document.getElementById("drwBeds").innerText = `${d.metrics.occupied_beds_count} / ${d.metrics.total_beds_count}`;
        document.getElementById("drwGrossRev").innerText = fmtInr(d.metrics.gross_revenue_paise);
        document.getElementById("drwCollectedRev").innerText = fmtInr(d.metrics.collected_revenue_paise);

        document.getElementById("drwPricingModel").value = d.pricing_model || "BUNDLE_TOTAL_HOSPITAL";
        document.getElementById("drwBillingCycle").value = d.billing_cycle || "ANNUAL_LICENSE";
        document.getElementById("drwContractFeeInr").value = Math.round((d.base_license_fee_paise || 0) / 100);
        document.getElementById("drwQuotaBeds").value = d.quota_limits.max_beds || 500;
        document.getElementById("drwQuotaUsers").value = d.quota_limits.max_users || 200;
        document.getElementById("drwMaintenanceMode").checked = !!d.maintenance_mode;
        document.getElementById("drwMaintenanceNotice").value = d.maintenance_notice || "";

        document.getElementById("drwModulesGrid").innerHTML = moduleCatalog.map(m => `
          <label class="flex items-center gap-1.5 bg-slate-900 p-2 rounded-lg border border-slate-800">
            <input type="checkbox" data-mod="${m.code}" ${d.enabled_modules[m.code] !== false ? "checked" : ""} />
            <span class="text-[11px]">${m.name}</span>
          </label>
        `).join("");

        renderDrawerSnapshots(d.snapshots || []);
        document.getElementById("drwSandboxSlug").value = `${d.subdomain_slug}-uat`;

        const cBtn = document.getElementById("drwCircuitBtn");
        if (d.is_active) {
          cBtn.className = "p-3 rounded-xl bg-rose-950/40 hover:bg-rose-900/50 border border-rose-500/40 text-left text-rose-300";
          cBtn.innerHTML = `<div class="font-bold">Suspend Tenant (Circuit-Breaker)</div><div class="text-[10px] text-rose-200/70 mt-0.5">Immediately blocks all API &amp; Federation traffic with HTTP 403.</div>`;
        } else {
          cBtn.className = "p-3 rounded-xl bg-emerald-950/40 hover:bg-emerald-900/50 border border-emerald-500/40 text-left text-emerald-300";
          cBtn.innerHTML = `<div class="font-bold">Reactivate Hospital Tenant</div><div class="text-[10px] text-emerald-200/70 mt-0.5">Restores normal API &amp; Federation access immediately.</div>`;
        }

        document.getElementById("drwActionOutput").classList.add("hidden");
        document.getElementById("tenantDrawerBackdrop").classList.remove("hidden");
      } catch (err) {
        showToast(err.message, true);
      }
    }

    function closeTenantDrawer() {
      document.getElementById("tenantDrawerBackdrop").classList.add("hidden");
    }

    function renderDrawerSnapshots(snapshots) {
      const box = document.getElementById("drwSnapshotsList");
      if (!snapshots.length) {
        box.innerHTML = `<div class="text-slate-500 text-center py-3">No encrypted .dump.enc snapshots found for this hospital yet.</div>`;
        return;
      }
      box.innerHTML = snapshots.map(s => `
        <div class="flex items-center justify-between bg-slate-900 px-3.5 py-2 rounded-xl border border-slate-800">
          <div>
            <div class="mono text-[11px] text-white">${s.filename}</div>
            <div class="text-[10px] text-slate-400">${(s.size_bytes / 1024).toFixed(1)} KB &bull; ${new Date(s.created_at * 1000).toLocaleString()}</div>
          </div>
          <button onclick="restoreSnapshot('${s.filename}')" class="px-3 py-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 text-[10px] font-bold">
            Restore into DB
          </button>
        </div>
      `).join("");
    }

    async function saveTenantEntitlements() {
      if (!activeDrawerTenant) return;
      const mods = {};
      document.querySelectorAll("#drwModulesGrid input[data-mod]").forEach(el => {
        mods[el.getAttribute("data-mod")] = el.checked;
      });

      let customFeeInr = parseInt(document.getElementById("drwContractFeeInr").value, 10);

      let requestedDrawerBeds = parseInt(document.getElementById("drwQuotaBeds").value || "100", 10);
      if (requestedDrawerBeds > 100) requestedDrawerBeds = 100;

      const res = await fetch(`/api/tenants/${activeDrawerTenant.subdomain_slug}/entitlements`, {
        method: "PATCH",
        headers: getHeaders(true),
        body: JSON.stringify({
          master_setup_key: document.getElementById("masterKey").value.trim(),
          pricing_model: document.getElementById("drwPricingModel").value,
          billing_cycle: document.getElementById("drwBillingCycle").value,
          base_license_fee_paise: customFeeInr !== null && !isNaN(customFeeInr) ? customFeeInr * 100 : null,
          enabled_modules: mods,
          quota_limits: {
            max_beds: requestedDrawerBeds,
            max_users: parseInt(document.getElementById("drwQuotaUsers").value || "200", 10)
          },
          maintenance_mode: document.getElementById("drwMaintenanceMode").checked,
          maintenance_notice: document.getElementById("drwMaintenanceNotice").value.trim() || null
        })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Failed to update entitlements", true); return; }

      showToast(`Updated modular contract & entitlements for ${activeDrawerTenant.subdomain_slug}.`);
      await authenticateAndSync();
      await openTenantDrawer(activeDrawerTenant.subdomain_slug);
    }

    async function triggerDrawerBackup() {
      if (!activeDrawerTenant) return;
      const res = await fetch(`/api/tenants/${activeDrawerTenant.subdomain_slug}/backups/trigger`, {
        method: "POST",
        headers: getHeaders(true),
        body: JSON.stringify({ master_setup_key: document.getElementById("masterKey").value.trim() })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Snapshot failed", true); return; }
      showToast(d.message);
      await openTenantDrawer(activeDrawerTenant.subdomain_slug);
      await authenticateAndSync();
    }

    async function restoreSnapshot(filename) {
      if (!activeDrawerTenant) return;
      if (!confirm(`Confirm Point-in-Time Restore: Overwrite database '${activeDrawerTenant.db_name}' with snapshot '${filename}'?`)) return;

      const res = await fetch(`/api/tenants/${activeDrawerTenant.subdomain_slug}/backups/${encodeURIComponent(filename)}/restore`, {
        method: "POST",
        headers: getHeaders(true),
        body: JSON.stringify({ master_setup_key: document.getElementById("masterKey").value.trim() })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Restore failed", true); return; }
      showToast(d.message);
      await openTenantDrawer(activeDrawerTenant.subdomain_slug);
    }

    function showDrawerOutput(html) {
      const box = document.getElementById("drwActionOutput");
      box.innerHTML = html;
      box.classList.remove("hidden");

      // NEW FIX: Auto-clear DOM
      setTimeout(() => {
          box.classList.add("hidden");
          box.innerHTML = "";
      }, 60000);
    }

    async function rotateDrawerKeys() {
      if (!activeDrawerTenant) return;
      if (!confirm(`Rotate JWT, Backup & Federation keys for '${activeDrawerTenant.subdomain_slug}'? Active staff sessions will require re-login.`)) return;

      const res = await fetch(`/api/tenants/${activeDrawerTenant.subdomain_slug}/rotate-keys`, {
        method: "POST",
        headers: getHeaders(true),
        body: JSON.stringify({
          master_setup_key: document.getElementById("masterKey").value.trim(),
          rotate_jwt_secret: true,
          rotate_backup_key: true,
          rotate_federation_key: true
        })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Key rotation failed", true); return; }

      showDrawerOutput(`
        <div class="font-bold text-emerald-300 mb-1">&check; Cryptographic Keys Rotated &amp; Sealed via AES-256-GCM:</div>
        <div>New JWT Secret: ${d.new_jwt_secret_key}</div>
        <div>New Backup Key: ${d.new_backup_encryption_key}</div>
        <div>New Federation API Key: ${d.new_federation_api_key}</div>
      `);
      await authenticateAndSync();
    }

    async function resetDrawerAdminPassword() {
      if (!activeDrawerTenant) return;
      const res = await fetch(`/api/tenants/${activeDrawerTenant.subdomain_slug}/reset-admin-password`, {
        method: "POST",
        headers: getHeaders(true),
        body: JSON.stringify({ master_setup_key: document.getElementById("masterKey").value.trim() })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Password reset failed", true); return; }

      showDrawerOutput(`
        <div class="font-bold text-amber-300 mb-1">&check; Break-Glass Superuser Account Unlocked:</div>
        <div>Admin Email: ${d.admin_email}</div>
        <div>One-Time Password: <span class="text-white font-bold">${d.one_time_password}</span></div>
      `);
    }

    async function cloneDrawerSandbox() {
      if (!activeDrawerTenant) return;
      const sbxSlug = document.getElementById("drwSandboxSlug").value.trim().toLowerCase();
      const res = await fetch(`/api/tenants/${activeDrawerTenant.subdomain_slug}/clone-sandbox`, {
        method: "POST",
        headers: getHeaders(true),
        body: JSON.stringify({
          master_setup_key: document.getElementById("masterKey").value.trim(),
          sandbox_subdomain_slug: sbxSlug
        })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Sandbox clone failed", true); return; }

      showDrawerOutput(`
        <div class="font-bold text-cyan-300 mb-1">&check; Isolated Training / UAT Sandbox Cloned:</div>
        <div>Sandbox URL: ${d.sandbox_url}</div>
        <div>Database Cloned: ${d.database_name}</div>
      `);
      await authenticateAndSync();
    }

    async function toggleDrawerCircuitBreaker() {
      if (!activeDrawerTenant) return;
      const nextState = !activeDrawerTenant.is_active;
      const res = await fetch(`/api/tenants/${activeDrawerTenant.subdomain_slug}/status`, {
        method: "PATCH",
        headers: getHeaders(true),
        body: JSON.stringify({
          master_setup_key: document.getElementById("masterKey").value.trim(),
          is_active: nextState
        })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Status change failed", true); return; }

      showToast(d.message);
      await authenticateAndSync();
      await openTenantDrawer(activeDrawerTenant.subdomain_slug);
    }

    // PROVISIONING PIPELINE
    function appendTermLog(msg, cls = "text-slate-300") {
      const box = document.getElementById("provTerminalLog");
      const ts = new Date().toISOString().split("T")[1].slice(0, 12);
      box.innerHTML += `<div class="${cls}">[${ts}] ${msg}</div>`;
      box.scrollTop = box.scrollHeight;
    }

    document.getElementById("provisionForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = document.getElementById("masterKey").value.trim();
      if (!key) { showToast("Please enter your Master Vendor Key in the top right bar first.", true); return; }

      const btn = document.getElementById("provSubmitBtn");
      const badge = document.getElementById("provStatusBadge");
      document.getElementById("provCredentialVault").classList.add("hidden");
      document.getElementById("provTerminalLog").innerHTML = "";

      btn.disabled = true;
      badge.innerText = "EXECUTING";
      badge.className = "px-2 py-0.5 rounded text-[10px] mono bg-amber-500/20 text-amber-300";

      const slug = document.getElementById("provSlug").value.trim().toLowerCase();
      const bp = document.querySelector('input[name="blueprint_archetype"]:checked').value;
      const selectedModules = Array.from(document.querySelectorAll("input[name='module_item']:checked")).map(cb => cb.value);
      const customFeeInr = parseInt(document.getElementById("provCustomFeeInr").value, 10);

      appendTermLog(`STAGE 1/5: Validating Master Vendor Key & checking '${slug}' availability...`, "text-cyan-300");
      appendTermLog(`STAGE 2/5: Creating isolated PostgreSQL database 'hms_${slug}'...`);
      appendTermLog(`STAGE 3/5: Applying 36-table Alembic baseline & seeding 9 RBAC roles + 10 Print Templates...`);
      appendTermLog(`STAGE 4/5: Executing spatial blueprint '${bp}' & licensing ${selectedModules.length} modules...`);

      try {
        const res = await fetch("/api/provision", {
          method: "POST",
          headers: getHeaders(true),
          body: JSON.stringify({
            master_setup_key: key,
            hospital_name: document.getElementById("provHospitalName").value.trim(),
            subdomain_slug: slug,
            branch_code: document.getElementById("provBranchCode").value.trim().toUpperCase(),
            admin_email: document.getElementById("provAdminEmail").value.trim().toLowerCase(),
            pricing_model: activeBundlePreset,
            billing_cycle: document.getElementById("provBillingCycle").value,
            selected_modules: selectedModules,
            base_license_fee_paise: customFeeInr !== null && !isNaN(customFeeInr) ? customFeeInr * 100 : null,
            quota_max_beds: parseInt(document.getElementById("provMaxBeds").value || "200", 10),
            quota_max_users: parseInt(document.getElementById("provMaxUsers").value || "100", 10),
            blueprint_archetype: bp,
            seed_icd10: document.getElementById("provSeedIcd10").checked,
            seed_allergies: document.getElementById("provSeedAllergies").checked,
            seed_complaints: document.getElementById("provSeedComplaints").checked
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Provisioning failed");

        lastProvisionedResult = data;
        appendTermLog(`STAGE 5/5: Sealed JWT & Backup keys via AES-256-GCM. Spatial summary: ${JSON.stringify(data.spatial_summary)}`, "text-emerald-400 font-bold");

        badge.innerText = "COMPLETED";
        badge.className = "px-2 py-0.5 rounded text-[10px] mono bg-emerald-500/20 text-emerald-300";

        document.getElementById("provVaultBody").innerHTML = `
          <div><span class="text-slate-400">Hospital URL:</span> <span class="text-emerald-300 font-bold">${data.subdomain_url}</span></div>
          <div><span class="text-slate-400">Database:</span> ${data.database_name} (${data.pricing_model} &bull; ${fmtInr(data.base_license_fee_paise)})</div>
          <div><span class="text-slate-400">Spatial Seeded:</span> ${data.spatial_summary.floors} Floors, ${data.spatial_summary.wards} Wards, ${data.spatial_summary.beds} Beds, ${data.spatial_summary.theatres} OTs</div>
          <div><span class="text-slate-400">Admin Email:</span> ${data.admin_email}</div>
          <div><span class="text-slate-400">One-Time Password:</span> <span class="text-amber-300 font-bold">${data.admin_password}</span></div>
          <div><span class="text-slate-400">Federation API Key:</span> ${data.federation_api_key}</div>
          <div><span class="text-slate-400">AES-256 Recovery Key:</span> ${data.backup_encryption_key}</div>
        `;
        document.getElementById("provCredentialVault").classList.remove("hidden");

        // NEW FIX: Auto-clear DOM
        setTimeout(() => {
            document.getElementById("provCredentialVault").classList.add("hidden");
            document.getElementById("provVaultBody").innerHTML = "";
            if (lastProvisionedResult) lastProvisionedResult = null;
        }, 60000);

        await authenticateAndSync();
      } catch (err) {
        appendTermLog(`FATAL ERROR: ${err.message}`, "text-rose-400 font-bold");
        badge.innerText = "FAILED";
        badge.className = "px-2 py-0.5 rounded text-[10px] mono bg-rose-500/20 text-rose-300";
      } finally {
        btn.disabled = false;
      }
    });

    function copyProvisionedJson() {
      if (lastProvisionedResult) {
        fallbackCopyTextToClipboard(JSON.stringify(lastProvisionedResult, null, 2));
      }
    }

    // TAB 3: STAGED CANARY & FLEET SCHEMA SYNC
    async function loadSchemaStatus() {
      const res = await fetch("/api/fleet/schema-status", { headers: getHeaders() });
      const data = await res.json();
      if (!res.ok) { showToast(data.detail || "Failed to load schema status", true); return; }

      const rows = (data.parity || []).map(p => `
        <tr class="border-b border-slate-800/80">
          <td class="py-2.5 px-3 font-bold text-white mono">${p.subdomain_slug} ${p.subdomain_slug === "default" ? '<span class="ml-1 px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 text-[9px]">CANARY</span>' : ''}</td>
          <td class="py-2.5 px-3 mono text-slate-300">${p.db_name}</td>
          <td class="py-2.5 px-3 mono text-cyan-300">${p.schema_version}</td>
          <td class="py-2.5 px-3 mono text-slate-400">${p.head_revision}</td>
          <td class="py-2.5 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] font-bold mono ${
              p.status === "UP_TO_DATE" ? "bg-emerald-500/20 text-emerald-300" : "bg-amber-500/20 text-amber-300"
            }">${p.status}</span>
          </td>
          <td class="py-2.5 px-3 text-right">
            <button onclick="syncSingleTenant('${p.subdomain_slug}')" class="px-3 py-1 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 font-bold text-[11px]">
              Upgrade &amp; Sync Tenant
            </button>
          </td>
        </tr>
      `).join("");

      document.getElementById("schemaTableContainer").innerHTML = `
        <table class="w-full text-left">
          <thead><tr class="text-slate-400 border-b border-slate-800 text-[11px] uppercase">
            <th class="py-2 px-3">Tenant Slug</th><th class="py-2 px-3">Database</th><th class="py-2 px-3">Current DB Version</th><th class="py-2 px-3">Target Head</th><th class="py-2 px-3">Parity Status</th><th class="py-2 px-3 text-right">Stage 1 / Single Action</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    async function syncSingleTenant(slug) {
      const res = await fetch(`/api/tenants/${slug}/sync`, {
        method: "POST",
        headers: getHeaders(true),
        body: JSON.stringify({ master_setup_key: document.getElementById("masterKey").value.trim() })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Single-tenant sync failed", true); return; }
      showToast(d.message);
      await loadSchemaStatus();
    }

    async function syncEntireFleet() {
      const res = await fetch("/api/tenants/sync-fleet", {
        method: "POST",
        headers: getHeaders(true),
        body: JSON.stringify({ master_setup_key: document.getElementById("masterKey").value.trim() })
      });
      const d = await res.json();
      if (!res.ok) { showToast(d.detail || "Fleet sync failed", true); return; }
      showToast(`Stage 2 Rollout Complete: Synced ${d.synced_tenants_count} cloud hospitals (${d.synced_subdomains.join(", ")}).`);
      await loadSchemaStatus();
    }

    // TAB 4: ZERO-KNOWLEDGE FEDERATION TOPOLOGY
    async function loadFederationTopology() {
      const res = await fetch("/api/federation/topology", { headers: getHeaders() });
      const data = await res.json();
      if (!res.ok) { showToast(data.detail || "Failed to load federation topology", true); return; }

      const rows = (data.nodes || []).map(n => `
        <tr class="border-b border-slate-800/80">
          <td class="py-2.5 px-3 font-bold text-white mono">${n.branch_code}</td>
          <td class="py-2.5 px-3 text-slate-200">${n.hospital_name} (${n.subdomain_slug})</td>
          <td class="py-2.5 px-3 mono text-slate-400">${n.api_key_prefix}...</td>
          <td class="py-2.5 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] font-bold mono ${
              n.federation_sharing_mode === "ISOLATED_SILO" ? "bg-rose-500/20 text-rose-300" :
              n.federation_sharing_mode === "CORPORATE_GROUP_ONLY" ? "bg-amber-500/20 text-amber-300" :
              "bg-cyan-500/20 text-cyan-300"
            }">${n.federation_sharing_mode}</span>
          </td>
          <td class="py-2.5 px-3 mono text-indigo-300">${n.indexed_pointers_count}</td>
          <td class="py-2.5 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] font-bold mono ${
              n.is_active ? "bg-emerald-500/20 text-emerald-300" : "bg-rose-500/20 text-rose-300"
            }">${n.is_active ? "ONLINE" : "REVOKED"}</span>
          </td>
        </tr>
      `).join("");

      document.getElementById("topologyTableContainer").innerHTML = `
        <table class="w-full text-left">
          <thead><tr class="text-slate-400 border-b border-slate-800 text-[11px] uppercase">
            <th class="py-2 px-3">Branch Code</th><th class="py-2 px-3">Hospital Node</th><th class="py-2 px-3">Fed Key Prefix</th><th class="py-2 px-3">Hospital Sovereignty Mode</th><th class="py-2 px-3">Indexed Pointers</th><th class="py-2 px-3">Node Status</th>
          </tr></thead>
          <tbody>${rows || '<tr><td colspan="6" class="py-6 text-center text-slate-500">No federated nodes registered yet.</td></tr>'}</tbody>
        </table>
      `;
    }

    // TAB 5: VENDOR AUDIT LOGS
    async function loadAuditLogs() {
      const res = await fetch("/api/fleet/audit-logs", { headers: getHeaders() });
      const data = await res.json();
      if (!res.ok) { showToast(data.detail || "Failed to load audit logs", true); return; }

      const rows = (data.items || []).map(l => `
        <tr class="border-b border-slate-800/80">
          <td class="py-2 px-3 mono text-slate-400">#${l.id}</td>
          <td class="py-2 px-3 font-bold text-cyan-300 mono">${l.action}</td>
          <td class="py-2 px-3 font-bold text-white mono">${l.target_slug}</td>
          <td class="py-2 px-3 text-slate-300">${l.details || ""}</td>
          <td class="py-2 px-3 mono text-slate-400">${l.created_at || ""}</td>
        </tr>
      `).join("");

      document.getElementById("auditLogsContainer").innerHTML = `
        <table class="w-full text-left">
          <thead><tr class="text-slate-400 border-b border-slate-800 text-[11px] uppercase">
            <th class="py-2 px-3">ID</th><th class="py-2 px-3">Action</th><th class="py-2 px-3">Target Tenant</th><th class="py-2 px-3">Forensic Details</th><th class="py-2 px-3">Timestamp (UTC)</th>
          </tr></thead>
          <tbody>${rows || '<tr><td colspan="5" class="py-6 text-center text-slate-500">No vendor audit events recorded yet.</td></tr>'}</tbody>
        </table>
      `;
    }
  </script>
</body>
</html>
"""