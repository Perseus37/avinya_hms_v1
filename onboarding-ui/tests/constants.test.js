import assert from "node:assert/strict";
import test from "node:test";

import {
  calculateDynamicPrice,
  fmtInr,
  formatRupeesInput,
} from "../src/lib/constants.js";

const modules = [
  { code: "opd", base_annual_price_paise: 2_500_000 },
  { code: "ipd_adt", base_annual_price_paise: 4_000_000 },
];
const bundles = [
  {
    code: "BUNDLE_HOSPITAL",
    modules: ["opd", "ipd_adt"],
    bundle_annual_price_paise: 5_000_000,
  },
];

test("quotes bundle pricing from the live paise catalog", () => {
  const quote = calculateDynamicPrice(
    "BUNDLE_HOSPITAL",
    ["opd", "ipd_adt"],
    "ANNUAL_LICENSE",
    40,
    false,
    "",
    modules,
    bundles,
  );

  assert.equal(quote.listAnnual, 6_500_000);
  assert.equal(quote.baseAnnual, 5_000_000);
  assert.equal(quote.saved, 1_500_000);
  assert.equal(quote.autoCycleFee, 5_000_000);
  assert.equal(quote.finalFeeInr, 50_000);
});

test("applies monthly and active-bed cycles in paise", () => {
  const monthly = calculateDynamicPrice(
    "BUNDLE_HOSPITAL",
    ["opd", "ipd_adt"],
    "MONTHLY_SUBSCRIPTION",
    40,
    false,
    "",
    modules,
    bundles,
  );
  const perBed = calculateDynamicPrice(
    "BUNDLE_HOSPITAL",
    ["opd", "ipd_adt"],
    "PER_ACTIVE_BED_MONTHLY",
    3,
    false,
    "",
    modules,
    bundles,
  );

  assert.equal(monthly.autoCycleFee, 416_700);
  assert.equal(monthly.normArr, 5_000_400);
  assert.equal(perBed.autoCycleFee, 1_500_000);
  assert.equal(perBed.suffix, "/ mo (3 beds)");
});

test("preserves a zero-fee override and formats rupee values", () => {
  const quote = calculateDynamicPrice(
    "BUNDLE_HOSPITAL",
    ["opd"],
    "ANNUAL_LICENSE",
    0,
    true,
    "0",
    modules,
    bundles,
  );

  assert.equal(quote.finalFeeInr, 0);
  assert.equal(quote.contractFeePaise, 0);
  assert.equal(quote.normArr, 0);
  assert.equal(fmtInr(1_234_567), "\u20B912,346");
  assert.equal(formatRupeesInput(1234.5), "1234.5");
  assert.equal(formatRupeesInput(""), "");
});
