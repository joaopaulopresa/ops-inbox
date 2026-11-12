// Typed access to the Ops Inbox API.

export type Status = "review" | "ready" | "processing" | "resuming" | "done" | "rejected" | "failed" | "all";

export interface Reason {
  code: string;
  message: string;
}

export interface CaseSummary {
  id: string;
  status: string;
  category: string | null;
  route: string | null;
  subject: string;
  from_name: string;
  from_address: string;
  received_at: string;
  urgent_at: string | null;
  reasons: Reason[];
  outcome: string | null;
  booking_reference: string | null;
}

export interface Box {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Field {
  name: string;
  value: string | null;
  evidence: string | null;
  source: string;
  span: { start: number; end: number } | null;
  box: Box | null;
  confidence: number;
  signals: Record<string, number>;
  corrected: boolean;
}

export interface Extraction {
  document: "booking" | "licence" | "invoice";
  fields: Field[];
  lines: { description: string; quantity: string; unit_price: string; amount: string }[];
}

export interface RuleResult {
  rule_id: string;
  name: string;
  outcome: "passed" | "failed" | "not_applicable" | "error";
  reason: string;
  message: string;
}

export interface AttachmentInfo {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  pages: number;
}

export interface CaseDetail {
  id: string;
  status: string;
  category: string | null;
  route: string | null;
  reasons: Reason[];
  outcome: string | null;
  decided_by: string | null;
  urgent_at: string | null;
  message: {
    id: string;
    subject: string;
    from_name: string;
    from_address: string;
    reply_to: string;
    to: string;
    sent_at: string | null;
    body: string;
    attachments: AttachmentInfo[];
  };
  state: {
    category: string | null;
    language: string;
    booking: Extraction | null;
    licences: Extraction[];
    invoices: Extraction[];
    rules: RuleResult[];
    reasons: Reason[];
    cost_usd: number;
    model_calls: number;
  } | null;
  documents_read: { attachment_id: string; engine: string; kind: string }[];
  facts: Record<string, unknown>;
  booking: { reference: string; status: string; total: string; car: string } | null;
  documents: { id: string; kind: string; number: string; status: string }[];
  reply: { to: string; subject: string; body: string; status: string; documents: string[] } | null;
  corrections: { field: string; old: string | null; new: string | null; actor: string; at: string }[];
  audit: { at: string; actor: string; action: string; detail: Record<string, unknown> }[];
}

export interface Revalidation {
  rules: RuleResult[];
  reasons: Reason[];
  blocking: RuleResult[];
  facts: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? response.json() : response.blob()) as Promise<T>;
}

export const ACTOR = "olivia";

export const api = {
  cases: (status: Status) => request<CaseSummary[]>(`/api/cases?status=${status}`),
  counts: () => request<Record<string, number>>("/api/cases/counts"),
  case: (id: string) => request<CaseDetail>(`/api/cases/${id}`),
  revalidate: (id: string, corrections: Record<string, unknown>) =>
    request<Revalidation>(`/api/cases/${id}/revalidate`, {
      method: "POST",
      body: JSON.stringify({ corrections }),
    }),
  approve: (id: string, corrections: Record<string, unknown>, reply_body?: string) =>
    request<{ status: string }>(`/api/cases/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ actor: ACTOR, corrections, reply_body: reply_body ?? null }),
    }),
  reject: (id: string, note: string) =>
    request<{ status: string }>(`/api/cases/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ actor: ACTOR, note }),
    }),
  editReply: (id: string, body: string) =>
    request<{ status: string }>(`/api/cases/${id}/reply`, {
      method: "PUT",
      body: JSON.stringify({ actor: ACTOR, body }),
    }),
  send: (id: string) =>
    request<{ status: string }>(`/api/cases/${id}/send`, {
      method: "POST",
      body: JSON.stringify({ actor: ACTOR }),
    }),
  previewReply: (id: string, corrections: Record<string, unknown>) =>
    request<{ body: string; origin: string }>(`/api/cases/${id}/preview/reply`, {
      method: "POST",
      body: JSON.stringify({ corrections }),
    }),
  previewDocument: (id: string, kind: "contract" | "invoice", corrections: Record<string, unknown>) =>
    request<Blob>(`/api/cases/${id}/preview/${kind}?format=png`, {
      method: "POST",
      body: JSON.stringify({ corrections }),
    }),
};

export const pageUrl = (attachmentId: string, page: number) =>
  `/api/attachments/${attachmentId}/page/${page}`;
export const documentUrl = (documentId: string) => `/api/documents/${documentId}/pdf`;
export const documentPageUrl = (documentId: string, page = 0) => `/api/documents/${documentId}/page/${page}`;
