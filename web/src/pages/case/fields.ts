import type { CaseDetail, Field } from "../../api";

/** One row of the fields panel: a field, or all extras folded into one row. */
export interface FieldRow {
  key: string; // name used for corrections: "pickup_date", "licence.expires_on", "extras"
  name: string; // field name without prefix
  group: "booking" | "licence" | "invoice";
  value: string | null;
  values?: string[];
  field: Field | null; // the field that carries the evidence
  confidence: number;
}

export function buildRows(detail: CaseDetail): FieldRow[] {
  const state = detail.state;
  if (!state) return [];
  const rows: FieldRow[] = [];
  if (state.booking && (detail.category === "new_booking" || detail.category === "change")) {
    const change = detail.category === "change";
    const extras = state.booking.fields.filter((f) => f.name === "extras");
    for (const field of state.booking.fields) {
      if (field.name === "extras") continue;
      if (change && field.value === null) continue;
      rows.push({
        key: field.name,
        name: field.name,
        group: "booking",
        value: field.value,
        field,
        confidence: field.confidence,
      });
    }
    if (!change || extras.length) {
      const values = extras.map((f) => f.value).filter((v): v is string => !!v);
      rows.push({
        key: "extras",
        name: "extras",
        group: "booking",
        value: values.join(", ") || null,
        values,
        field: extras[0] ?? null,
        confidence: extras.length ? Math.min(...extras.map((f) => f.confidence)) : 1,
      });
    }
  }
  const licence = detail.category === "new_booking" ? state.licences[0] : undefined;
  for (const field of licence?.fields ?? []) {
    rows.push({
      key: `licence.${field.name}`,
      name: field.name,
      group: "licence",
      value: field.value,
      field,
      confidence: field.confidence,
    });
  }
  const invoice = detail.category === "supplier_invoice" ? state.invoices[0] : undefined;
  for (const field of invoice?.fields ?? []) {
    rows.push({
      key: `invoice.${field.name}`,
      name: field.name,
      group: "invoice",
      value: field.value,
      field,
      confidence: field.confidence,
    });
  }
  return rows;
}

/** Characters [start, end) of the e-mail body to highlight for a field, if any. */
export function spanFor(row: FieldRow | undefined, body: string): [number, number] | null {
  const field = row?.field;
  if (!field || field.source !== "email") return null;
  if (field.span && field.span.end <= body.length) return [field.span.start, field.span.end];
  if (field.evidence) {
    const at = body.toLowerCase().indexOf(field.evidence.toLowerCase());
    if (at >= 0) return [at, at + field.evidence.length];
  }
  return null;
}
