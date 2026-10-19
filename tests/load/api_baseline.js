import http from "k6/http";
import { check } from "k6";

const baseUrl = (__ENV.BASE_URL || "").replace(/\/+$/, "");
const accessToken = __ENV.ACCESS_TOKEN;
const tenantSlug = __ENV.TENANT_SLUG;

if (__ENV.TARGET_ENV !== "staging") {
  throw new Error("This load test is restricted to TARGET_ENV=staging.");
}
if (!baseUrl || !accessToken || !tenantSlug) {
  throw new Error("BASE_URL, ACCESS_TOKEN, and TENANT_SLUG are required.");
}
if (!baseUrl.startsWith("https://")) {
  throw new Error("BASE_URL must use HTTPS.");
}

export const options = {
  discardResponseBodies: true,
  stages: [
    { duration: "2m", target: 25 },
    { duration: "2m", target: 50 },
    { duration: "3m", target: 100 },
    { duration: "3m", target: 200 },
    { duration: "15m", target: 200 },
    { duration: "2m", target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<500"],
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
  },
};

export default function () {
  const response = http.get(
    `${baseUrl}/v1/api/patients?page=1&page_size=20`,
    {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "X-Hospital-Subdomain": tenantSlug,
      },
      tags: { name: "patient_list_read" },
      timeout: "10s",
    },
  );
  check(response, {
    "patient list responds successfully": (result) => result.status === 200,
  });
}
