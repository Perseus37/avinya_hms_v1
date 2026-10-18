import { useCallback, useState } from "react";

export function normalizeApiError(detail, fallback) {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      if (!item || typeof item !== "object") return "";
      const location = Array.isArray(item.loc)
        ? item.loc.filter((part) => part !== "body").join(".")
        : "";
      const message = item.msg || item.message;
      return [location, message].filter(Boolean).join(": ");
    }).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }
  return fallback;
}

export default function useControlPlaneApi(masterKey, controlPlaneKey) {
  const [busyKey, setBusyKey] = useState(null);
  const [apiLogs, setApiLogs] = useState([]);

  const request = useCallback(async (path, options = {}, tag = null) => {
    if (tag) setBusyKey(tag);
    const startedAt = performance.now();
    const isFormData = options.body instanceof FormData;
    let body = isFormData ? new FormData() : options.body;
    if (isFormData) {
      for (const [key, value] of options.body.entries()) {
        body.append(key, value);
      }
    }
    if (isFormData) body.set("master_setup_key", masterKey.trim());
    if (!isFormData && masterKey.trim() && body !== undefined) {
      if (typeof body === "string") {
        try {
          const parsedBody = JSON.parse(body);
          if (parsedBody && typeof parsedBody === "object" && !Array.isArray(parsedBody)) {
            body = JSON.stringify({
              ...parsedBody,
              master_setup_key: parsedBody.master_setup_key || masterKey.trim(),
            });
          }
        } catch {
          // Leave non-JSON bodies unchanged; the endpoint will validate its own payload.
        }
      }
    } else if (
      !isFormData &&
      masterKey.trim() &&
      options.body === undefined &&
      (options.method || "GET").toUpperCase() !== "GET"
    ) {
      body = JSON.stringify({ master_setup_key: masterKey.trim() });
    }
    const headers = new Headers(options.headers);
    headers.set("X-Master-Setup-Key", masterKey.trim());
    if (controlPlaneKey.trim()) {
      headers.set("X-Control-Plane-Secret", controlPlaneKey.trim());
    }
    if (body && !isFormData && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    try {
      const response = await fetch(path, { ...options, body, headers });
      const duration = Math.round(performance.now() - startedAt);
      const responseBody = await response.json().catch(() => ({}));

      setApiLogs((previous) => [
        {
          id: Date.now() + Math.random(),
          ts: new Date().toLocaleTimeString(),
          method: options.method || "GET",
          path,
          status: response.status,
          ok: response.ok,
          ms: duration,
          body: responseBody,
        },
        ...previous.slice(0, 49),
      ]);

      if (!response.ok) {
        throw new Error(normalizeApiError(
          responseBody?.detail ?? responseBody?.message,
          `HTTP ${response.status}`,
        ));
      }
      return responseBody;
    } finally {
      if (tag) setBusyKey(null);
    }
  }, [controlPlaneKey, masterKey]);

  return { request, busyKey, apiLogs };
}
