import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { X } from "lucide-react";

// Components & Views
import Sidebar from "./components/Sidebar";
import HospitalDrawer from "./components/HospitalDrawer";
import CommandCenter from "./views/CommandCenter";
import FleetExplorer from "./views/FleetExplorer";
import ProvisioningStudio from "./views/ProvisioningStudio";
import MigrationStudio from "./views/MigrationStudio";
import CanaryPipeline from "./views/CanaryPipeline";
import FederationMesh from "./views/FederationMesh";
import AuditVault from "./views/AuditVault";
import useControlPlaneApi from "./hooks/useControlPlaneApi";
import useFleetData from "./hooks/useFleetData";

function formatNotificationMessage(message) {
  if (typeof message === "string") return message;
  try {
    const formatted = JSON.stringify(message);
    return typeof formatted === "string" ? formatted : "An unexpected response was received.";
  } catch {
    return "An unexpected response was received.";
  }
}

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem("avinya_cp_theme") || "obsidian-gold");
  const [masterKey, setMasterKey] = useState(() => localStorage.getItem("avinya_master_key") || "");
  const [controlPlaneKey, setControlPlaneKey] = useState(() => localStorage.getItem("avinya_control_plane_key") || "");
  const [autoSyncOnMount] = useState(() => Boolean(localStorage.getItem("avinya_master_key")?.trim()));
  const [activeView, setActiveView] = useState("command-center");

  // Global State
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const { request, busyKey, apiLogs } = useControlPlaneApi(masterKey, controlPlaneKey);
  const notify = useCallback((msg, type = "ok") => {
    const id = Date.now() + Math.random();
    setToast({ id, msg: formatNotificationMessage(msg), type });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => {
      setToast((current) => current?.id === id ? null : current);
    }, 6000);
  }, []);
  useEffect(() => () => clearTimeout(toastTimer.current), []);
  const {
    loading,
    tenants,
    overview,
    analytics,
    schemaParity,
    topology,
    auditLogs,
    catalogs,
    syncAll,
    lastSyncedAt,
    lastSyncError,
  } = useFleetData({
    masterKey,
    request,
    notify,
    autoSync: autoSyncOnMount,
  });

  useEffect(() => {
    const savedControlPlaneKey = localStorage.getItem("avinya_control_plane_key") || "";
    if (controlPlaneKey.trim() === savedControlPlaneKey.trim()) return;
    if (controlPlaneKey.trim()) {
      localStorage.setItem("avinya_control_plane_key", controlPlaneKey.trim());
    } else {
      localStorage.removeItem("avinya_control_plane_key");
    }
  }, [controlPlaneKey]);

  // Telemetry & UI State
  const [devtoolsOpen, setDevtoolsOpen] = useState(false);
  const [apiLogSearch, setApiLogSearch] = useState("");
  const [apiLogPage, setApiLogPage] = useState(0);
  const apiLogPageSize = 10;
  const [selectedTenant, setSelectedTenant] = useState(null);
  const filteredApiLogs = useMemo(() => {
    const query = apiLogSearch.trim().toLowerCase();
    if (!query) return apiLogs;
    return apiLogs.filter((log) =>
      `${log.method} ${log.status} ${log.path} ${JSON.stringify(log.body)}`.toLowerCase().includes(query),
    );
  }, [apiLogSearch, apiLogs]);
  const visibleApiLogs = filteredApiLogs.slice(
    apiLogPage * apiLogPageSize,
    (apiLogPage + 1) * apiLogPageSize,
  );
  const apiLogPageCount = Math.max(1, Math.ceil(filteredApiLogs.length / apiLogPageSize));

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("avinya_cp_theme", theme);
  }, [theme]);

  const inspectHospital = async (slug) => {
    try {
      const d = await request(`/api/tenants/${slug}/details`, {}, `inspect-${slug}`);
      setSelectedTenant(d);
    } catch (err) {
      notify(err.message, "err");
    }
  };

  const refreshHospital = async (slug) => {
    const detail = request(`/api/tenants/${slug}/details`, {}, `inspect-${slug}`);
    const [, result] = await Promise.all([syncAll(true), detail]);
    if (result) setSelectedTenant(result);
  };

  const viewProps = {
    tenants, overview, analytics, schemaParity, topology, auditLogs,
    catalogs, request, notify, syncAll, masterKey, busyKey, inspectHospital, refreshHospital,
    loading, lastSyncedAt, lastSyncError
  };

  return (
    <div className="min-h-screen flex flex-col md:h-screen md:min-h-0 md:overflow-hidden md:flex-row">
      <Sidebar
        theme={theme} setTheme={setTheme}
        activeView={activeView} setActiveView={setActiveView}
        masterKey={masterKey} setMasterKey={setMasterKey}
        controlPlaneKey={controlPlaneKey} setControlPlaneKey={setControlPlaneKey}
        syncAll={syncAll} loading={loading}
        apiLogs={apiLogs} setDevtoolsOpen={setDevtoolsOpen}
      />
      <main className="min-w-0 flex-1 min-h-0 p-4 sm:p-6 md:h-full md:p-8 overflow-visible md:overflow-y-auto space-y-6 relative no-scrollbar">
        {toast && (
          <div
            role={toast.type === "err" ? "alert" : "status"}
            aria-live={toast.type === "err" ? "assertive" : "polite"}
            className={`fixed bottom-4 right-4 sm:bottom-6 sm:right-6 z-[100] p-4 rounded-2xl border text-xs font-bold flex items-start justify-between gap-4 shadow-xl w-[min(24rem,calc(100vw-2rem))] max-h-[40vh] overflow-y-auto ${
            toast.type === "err" ? "bg-rose-500/90 text-white border-rose-500" : "bg-emerald-500/90 text-white border-emerald-500"
          }`}>
            <span className="break-words">{toast.msg}</span>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => {
                clearTimeout(toastTimer.current);
                setToast(null);
              }}
            >
              <X className="w-4 h-4 shrink-0" />
            </button>
          </div>
        )}

        {activeView === "command-center" && <CommandCenter {...viewProps} setActiveView={setActiveView} />}
        {activeView === "fleet-matrix" && <FleetExplorer {...viewProps} />}
        {activeView === "provision-studio" && <ProvisioningStudio {...viewProps} />}

        {/* DOM-Persisted Canvas View */}
        <div className={activeView === "migration-studio" ? "h-full w-full block" : "hidden"}>
          <MigrationStudio tenants={tenants} request={request} masterKey={masterKey} catalogs={catalogs} notify={notify} />
        </div>

        {activeView === "canary-migrations" && <CanaryPipeline {...viewProps} />}
        {activeView === "federation-mesh" && <FederationMesh {...viewProps} />}
        {activeView === "audit-vault" && <AuditVault {...viewProps} />}

        {devtoolsOpen && (
          <div className="absolute bottom-6 left-6 right-6 z-40 theme-panel rounded-2xl p-4 space-y-2 border-2 shadow-2xl">
            <div className="flex items-center justify-between text-xs font-mono font-bold">
              <span className="theme-accent-text">LIVE CONTROL PLANE API INSPECTOR ({filteredApiLogs.length}/{apiLogs.length} calls)</span>
              <button onClick={() => setDevtoolsOpen(false)} className="hover:text-rose-500 transition-colors">Close &times;</button>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-[10px]">
              <input
                value={apiLogSearch}
                onChange={(event) => {
                  setApiLogSearch(event.target.value);
                  setApiLogPage(0);
                }}
                placeholder="Filter method, status, path, or response..."
                aria-label="Filter live API calls"
                className="theme-input rounded-lg px-2.5 py-1.5 text-xs"
              />
              <span className="theme-text-muted">Browser-session only; these inspector entries are not persisted to the database.</span>
            </div>
            <div className="max-h-52 overflow-y-auto space-y-1.5 font-mono text-[11px] no-scrollbar">
              {visibleApiLogs.map((l) => (
                <details key={l.id} className="theme-elevated p-2 rounded-lg">
                  <summary className="cursor-pointer flex justify-between outline-none">
                    <span>
                      <b className={l.ok ? "text-emerald-500" : "text-rose-500"}>{l.method} {l.status}</b> {l.path}
                    </span>
                    <span className="theme-accent-text">{l.ms}ms &bull; {l.ts}</span>
                  </summary>
                  <pre className="mt-2 p-2 theme-panel rounded overflow-x-auto text-[10px]">
                    {JSON.stringify(l.body, null, 2)}
                  </pre>
                </details>
              ))}
              {visibleApiLogs.length === 0 && <div className="theme-text-muted py-3 text-center">No API calls match this filter.</div>}
            </div>
            <div className="flex items-center justify-end gap-2 text-[10px]">
              <span className="theme-text-muted">Page {apiLogPage + 1} of {apiLogPageCount}</span>
              <button type="button" disabled={apiLogPage === 0} onClick={() => setApiLogPage((page) => Math.max(0, page - 1))} className="theme-elevated rounded px-2 py-1 disabled:opacity-40">Previous</button>
              <button type="button" disabled={apiLogPage + 1 >= apiLogPageCount} onClick={() => setApiLogPage((page) => Math.min(apiLogPageCount - 1, page + 1))} className="theme-elevated rounded px-2 py-1 disabled:opacity-40">Next</button>
            </div>
          </div>
        )}
      </main>

      {selectedTenant && (
        <HospitalDrawer
          key={selectedTenant.subdomain_slug}
          tenant={selectedTenant}
          onClose={() => setSelectedTenant(null)}
          {...viewProps}
        />
      )}
    </div>
  );
}