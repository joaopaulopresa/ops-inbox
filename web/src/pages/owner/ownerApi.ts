// Owner endpoints.

export interface CurvePoint {
  threshold: number;
  automatic: number;
  straight_through_rate: number;
  straight_through_of_clean: number;
  erroneous_automatic: number;
  errors_per_100_automatic: number;
  should_have_stopped_but_automatic: number;
}

export interface Evaluation {
  emails: number;
  threshold: number;
  curve: CurvePoint[];
  operating_point: CurvePoint;
  classification: { accuracy: number | null; scored: number };
  adversarial: { total: number; caught: number; catch_rate: number | null };
  cost: { per_email_usd: number; model_calls_per_email: number };
}

export interface Automation {
  threshold: number;
  recommended: { threshold: number; target_errors_per_100_automatic: number } | null;
  tuning: Evaluation | null;
  heldout: Evaluation | null;
}

export interface Dashboard {
  days: number;
  emails: number;
  decided: number;
  automatic: number;
  straight_through_rate: number | null;
  reviewed: number;
  median_review_minutes: number | null;
  per_day: { day: string; emails: number }[];
  corrections: { field: string; count: number }[];
  stop_reasons: { code: string; count: number }[];
  flagged_after_the_fact: number;
  by_status: Record<string, number>;
  cost_usd: number;
}

export interface RuleDef {
  id: string;
  name: string;
  description: string;
  applies_to: string[];
  kind: "check" | "required";
  when: string | null;
  check: string | null;
  fields: string[];
  message: string;
  enabled: boolean;
}

export interface RuleTest {
  checked: number;
  would_stop: number;
  stops_now: number;
  newly_stopped: { case_id: string; subject: string; from: string; message: string }[];
  no_longer_stopped: { case_id: string; subject: string; from: string }[];
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json" } });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? response.json() : response.blob()) as Promise<T>;
}

export const owner = {
  dashboard: () => call<Dashboard>("/api/dashboard"),
  automation: () => call<Automation>("/api/automation"),
  setThreshold: (value: number) =>
    call<{ threshold: number }>("/api/automation/threshold", { method: "PUT", body: JSON.stringify({ value }) }),
  rules: () => call<RuleDef[]>("/api/rules"),
  testRule: (rule: RuleDef) => call<RuleTest>("/api/rules/test", { method: "POST", body: JSON.stringify(rule) }),
  saveRule: (rule: RuleDef) => call<RuleDef>(`/api/rules/${rule.id}`, { method: "PUT", body: JSON.stringify(rule) }),
  template: (kind: string) => call<{ source: string; customised: boolean }>(`/api/templates/${kind}`),
  previewTemplate: (kind: string, source: string) =>
    call<Blob>(`/api/templates/${kind}/preview`, { method: "POST", body: JSON.stringify({ source }) }),
  saveTemplate: (kind: string, source: string) =>
    call<{ status: string }>(`/api/templates/${kind}`, { method: "PUT", body: JSON.stringify({ source }) }),
  resetTemplate: (kind: string) => call<{ status: string }>(`/api/templates/${kind}`, { method: "DELETE" }),
  records: (kind: "bookings" | "cars" | "payables" | "audit") => call<Record<string, unknown>[]>(`/api/records/${kind}`),
};
