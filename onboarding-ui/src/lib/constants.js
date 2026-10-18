export const INR = "\u20B9";
export const FLEET_REFRESH_INTERVAL_MS = 30_000;

export const formatRupees = (amount) =>
  Math.round(Number(amount) || 0).toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  });

export const formatRupeesInput = (amount) => {
  if (amount === "" || amount === null || amount === undefined) return "";
  const value = Number(amount);
  return Number.isFinite(value) ? String(value) : "";
};

export const fmtInr = (paise) => {
  return `${INR}${formatRupees((Number(paise) || 0) / 100)}`;
};

export const THEMES = [
  { id: "obsidian-gold", name: "Obsidian Gold", dot: "#f59e0b" },
  { id: "clinical-daylight", name: "Clinical Blue (Light)", dot: "#2563eb" },
  { id: "cyber-violet", name: "Cyber Violet", dot: "#8b5cf6" },
  { id: "bio-emerald", name: "Bio Emerald", dot: "#10b981" },
];

export function calculateDynamicPrice(
  pricingModel, 
  enabledCodesList, 
  billingCycle, 
  maxBeds, 
  customOverrideEnabled, 
  customFeeInr, 
  moduleCatalog = [], 
  bundleCatalog = []
) {
  const rateMap = Object.fromEntries(
    moduleCatalog.map((m) => [m.code, m.base_annual_price_paise || m.priceInr || 0])
  );

  const listAnnual = enabledCodesList.reduce((s, c) => s + (rateMap[c] || 0), 0);
  const bundle = bundleCatalog.find((b) => b.code === pricingModel);
  const baseAnnual = bundle?.bundle_annual_price_paise || bundle?.priceInr || listAnnual;
  
  const saved = Math.max(0, listAnnual - baseAnnual);
  const hasIpd = enabledCodesList.includes("ipd_adt");
  const beds = hasIpd ? Math.max(0, Number(maxBeds) || 0) : 0;
  
  let autoCycleFee = baseAnnual;
  let suffix = "/ year";

  if (billingCycle === "MONTHLY_SUBSCRIPTION") {
    autoCycleFee = Math.round(baseAnnual / 12 / 100) * 100;
    suffix = "/ month";
  } else if (billingCycle === "PER_ACTIVE_BED_MONTHLY") {
    // FIX: Normalize ₹1500 to 150000 paise to prevent mixing currencies in arithmetic
    autoCycleFee = beds > 0 ? Math.max(10, beds) * 150000 : Math.round(baseAnnual / 12 / 100) * 100;
    suffix = `/ mo (${beds} beds)`;
  } else if (billingCycle === "ONE_TIME_PERPETUAL") {
    autoCycleFee = baseAnnual * 3;
    suffix = "one-time buyout";
  }

  const finalFeeInr = customOverrideEnabled && String(customFeeInr).trim() !== "" && Number(customFeeInr) >= 0
    ? Number(customFeeInr)
    : autoCycleFee / 100;
  const contractFeePaise = Math.round(finalFeeInr * 100);

  const normArr = billingCycle === "MONTHLY_SUBSCRIPTION" || billingCycle === "PER_ACTIVE_BED_MONTHLY"
    ? contractFeePaise * 12
    : billingCycle === "ONE_TIME_PERPETUAL" ? 0 : contractFeePaise;

  return { 
    listAnnual, 
    baseAnnual, 
    saved, 
    autoCycleFee, 
    contractFeePaise,
    finalFeeInr, 
    normArr, 
    suffix, 
    beds 
  };
}