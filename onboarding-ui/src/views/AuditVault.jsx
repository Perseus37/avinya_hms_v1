import React, { useEffect, useState } from "react";

const PAGE_SIZE = 25;

export default function AuditVault({ auditLogs = [], request, notify }) {
  const [items, setItems] = useState(auditLogs);
  const [total, setTotal] = useState(auditLogs.length);
  const [offset, setOffset] = useState(0);
  const [actionInput, setActionInput] = useState("");
  const [targetInput, setTargetInput] = useState("");
  const [filters, setFilters] = useState({ action: "", target: "" });
  const [loading, setLoading] = useState(false);
  const [refreshSequence, setRefreshSequence] = useState(0);

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (filters.action) params.set("action", filters.action);
    if (filters.target) params.set("target", filters.target);

    setLoading(true);
    request(`/api/fleet/audit-logs?${params.toString()}`)
      .then((data) => {
        if (!active) return;
        setItems(data.items || []);
        setTotal(data.total || 0);
      })
      .catch((error) => {
        if (active) notify(error.message || "Could not load audit records.", "err");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [filters, offset, refreshSequence, request, notify]);

  const applyFilters = (event) => {
    event.preventDefault();
    setOffset(0);
    setFilters({ action: actionInput.trim(), target: targetInput.trim() });
  };

  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-2xl font-black tracking-tight">Control Plane Forensic Ledger</h1>
          <button type="button" onClick={() => setRefreshSequence((current) => current + 1)} disabled={loading} className="theme-panel rounded-lg px-3 py-2 text-xs font-bold disabled:opacity-50">
            {loading ? "Refreshing..." : "Refresh ledger"}
          </button>
        </div>
        <p className="text-xs theme-text-muted">
          Audit events are stored in the control-plane database. Backup and restore task outcomes and unhandled API failures are recorded here.
        </p>
      </div>

      <form onSubmit={applyFilters} className="theme-panel rounded-2xl p-3 flex flex-wrap items-end gap-3 text-xs">
        <label className="space-y-1">
          <span className="block theme-text-muted">Action (exact)</span>
          <input value={actionInput} onChange={(event) => setActionInput(event.target.value)} className="theme-input rounded-lg px-2.5 py-2" placeholder="e.g. RESTORE_FAILED" />
        </label>
        <label className="space-y-1">
          <span className="block theme-text-muted">Tenant contains</span>
          <input value={targetInput} onChange={(event) => setTargetInput(event.target.value)} className="theme-input rounded-lg px-2.5 py-2" placeholder="Tenant slug" />
        </label>
        <button type="submit" className="theme-accent-bg rounded-lg px-3 py-2 font-bold">Apply filters</button>
        <span className="theme-text-muted">{total} matching events</span>
      </form>

      <div className="theme-panel rounded-2xl text-xs font-mono overflow-x-auto">
        <table className="w-full min-w-[760px] text-left">
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
            {items.length === 0 ? (
              <tr>
                <td colSpan="5" className="py-8 text-center text-slate-500 font-sans">
                  {loading ? "Loading audit events..." : "No audit events match these filters."}
                </td>
              </tr>
            ) : (
              items.map((log) => (
                <tr key={log.id} className="border-b border-slate-500/10 theme-row-hover transition-colors">
                  <td className="py-2.5 px-4 theme-text-muted">#{log.id}</td>
                  <td className="py-2.5 px-4 font-bold theme-accent-text">{log.action}</td>
                  <td className="py-2.5 px-4 font-bold theme-text-main">{log.target_slug}</td>
                  <td className="py-2.5 px-4 font-sans theme-text-main">{log.details}</td>
                  <td className="py-2.5 px-4 theme-text-muted">
                    {log.created_at ? new Date(log.created_at).toLocaleString() : ""}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="theme-text-muted">{loading ? "Refreshing..." : `Page ${pageNumber} of ${pageCount}`}</span>
        <div className="flex gap-2">
          <button type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} className="theme-panel rounded-lg px-3 py-1.5 disabled:opacity-40">Previous</button>
          <button type="button" disabled={offset + PAGE_SIZE >= total || loading} onClick={() => setOffset(offset + PAGE_SIZE)} className="theme-panel rounded-lg px-3 py-1.5 disabled:opacity-40">Next</button>
        </div>
      </div>
    </div>
  );
}
