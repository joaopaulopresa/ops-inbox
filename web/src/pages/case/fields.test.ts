import { describe, expect, it } from "vitest";
import type { CaseDetail, Field } from "../../api";
import { buildRows, spanFor } from "./fields";

const field = (name: string, value: string | null, extra: Partial<Field> = {}): Field => ({
  name,
  value,
  evidence: value,
  source: "email",
  span: null,
  box: null,
  confidence: 1,
  signals: {},
  corrected: false,
  ...extra,
});

const detail = (category: string, booking: Field[], licence: Field[] = []): CaseDetail =>
  ({
    category,
    state: {
      category,
      language: "en",
      booking: { document: "booking", fields: booking, lines: [] },
      licences: licence.length ? [{ document: "licence", fields: licence, lines: [] }] : [],
      invoices: [],
      rules: [],
      reasons: [],
      cost_usd: 0,
      model_calls: 0,
    },
  }) as unknown as CaseDetail;

describe("field rows", () => {
  it("folds extras into one row and prefixes document fields", () => {
    const rows = buildRows(
      detail(
        "new_booking",
        [field("car", "SPRITE"), field("extras", "chauffeur", { confidence: 0.9 }), field("extras", "delivery")],
        [field("expires_on", "2031-05-01", { source: "a1" })],
      ),
    );
    expect(rows.map((r) => r.key)).toEqual(["car", "extras", "licence.expires_on"]);
    expect(rows[1]!.values).toEqual(["chauffeur", "delivery"]);
    expect(rows[1]!.confidence).toBe(0.9);
  });

  it("shows only what a change request changes", () => {
    const rows = buildRows(detail("change", [field("car", null), field("pickup_date", "2027-06-12")]));
    expect(rows.map((r) => r.key)).toEqual(["pickup_date"]);
  });

  it("finds the evidence in the e-mail by span or by text", () => {
    const body = "Pick-up on 12 June 2027 at 10:00";
    const [row] = buildRows(detail("new_booking", [field("pickup_time", "10:00", { evidence: "at 10:00" })]));
    expect(spanFor(row, body)).toEqual([24, 32]);
  });
});
