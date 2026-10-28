import { useCallback, useEffect, useRef, useState } from "react";
import { FLEET_REFRESH_INTERVAL_MS } from "../lib/constants";

export default function useFleetData({ operatorToken, request, notify }) {
  const [loading, setLoading] = useState(false);
  const [tenants, setTenants] = useState([]);
  const [overview, setOverview] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [schemaParity, setSchemaParity] = useState(null);
  const [topology, setTopology] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [catalogs, setCatalogs] = useState({
    modules: [],
    bundles: [],
    blueprints: [],
    migrationSchemas: {},
  });
  const [lastSyncedAt, setLastSyncedAt] = useState(null);
  const [lastSyncError, setLastSyncError] = useState(null);
  const previousOperatorToken = useRef(false);
  const syncing = useRef(false);
  const mounted = useRef(false);

  const syncAll = useCallback(async (silent = false) => {
    if (!operatorToken) {
      if (!silent) notify("Sign in as a named operator before syncing.", "err");
      return;
    }
    if (syncing.current) return;
    syncing.current = true;
    if (!silent) setLoading(true);
    try {
      const results = await Promise.allSettled([
        request("/api/tenants"),
        request("/api/fleet/overview"),
        request("/api/fleet/analytics"),
        request("/api/fleet/schema-status"),
        request("/api/federation/topology"),
        request("/api/fleet/audit-logs?limit=25"),
        request("/api/pricing/catalog"),
        request("/api/blueprints"),
      ]);
      const values = results.map((result) =>
        result.status === "fulfilled" ? result.value : null,
      );
      const [
        tenantData,
        overviewData,
        analyticsData,
        parityData,
        topologyData,
        auditData,
        pricingData,
        blueprintData,
      ] = values;

      if (!tenantData || !overviewData) {
        const failed = results.slice(0, 2).find((result) => result.status === "rejected");
        if (failed) throw failed.reason;
        throw new Error("The control plane returned an incomplete fleet response.");
      }

      if (!mounted.current) return;
      setTenants(tenantData.items || []);
      setOverview(overviewData);
      setLastSyncedAt(new Date());
      if (analyticsData) setAnalytics(analyticsData);
      if (parityData) setSchemaParity(parityData);
      if (topologyData) setTopology(topologyData.nodes || []);
      if (auditData) setAuditLogs(auditData.items || []);
      if (pricingData || blueprintData) {
        setCatalogs((previous) => ({
          modules: pricingData?.modules || previous.modules,
          bundles: pricingData?.bundles || previous.bundles,
          migrationSchemas:
            pricingData?.migrationSchemas || previous.migrationSchemas,
          blueprints: blueprintData?.blueprints || previous.blueprints,
        }));
      }

      const optionalNames = [
        "analytics",
        "schema status",
        "federation topology",
        "audit log",
        "pricing/migration catalogs",
        "blueprint catalog",
      ];
      const optionalFailures = results.slice(2).flatMap((result, index) =>
        result.status === "rejected"
          ? [`${optionalNames[index]}: ${result.reason?.message || "request failed"}`]
          : [],
      );
      setLastSyncError(optionalFailures.length ? optionalFailures.join("; ") : null);
      if (optionalFailures.length && !silent) {
        notify(`Some onboarding data did not load: ${optionalFailures.join("; ")}`, "err");
      } else if (!silent) {
        notify(`Synced ${tenantData.items?.length || 0} environments & catalogs.`);
      }
    } catch (error) {
      if (mounted.current) setLastSyncError(error.message || "Control-plane synchronization failed.");
      if (!silent) notify(error.message || "Control-plane synchronization failed.", "err");
    } finally {
      syncing.current = false;
      if (mounted.current && !silent) setLoading(false);
    }
  }, [operatorToken, notify, request]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const currentOperatorToken = Boolean(operatorToken);
    if (
      (!previousOperatorToken.current && currentOperatorToken) ||
      (currentOperatorToken && !lastSyncedAt && !syncing.current)
    ) {
      syncAll(true);
    }
    previousOperatorToken.current = currentOperatorToken;
  }, [operatorToken, syncAll, lastSyncedAt]);

  useEffect(() => {
    if (!operatorToken) return undefined;

    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") syncAll(true);
    };
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") syncAll(true);
    }, FLEET_REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenVisible);
    };
  }, [operatorToken, syncAll]);

  return {
    loading,
    tenants,
    overview,
    analytics,
    schemaParity,
    topology,
    auditLogs,
    catalogs,
    lastSyncedAt,
    lastSyncError,
    syncAll,
  };
}
