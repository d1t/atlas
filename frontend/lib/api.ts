const API_BASE =
  (typeof window !== "undefined" && process.env.NEXT_PUBLIC_API_BASE) ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:8000";

const TOKEN_KEY = "atlas_token";
const USER_KEY = "atlas_user";

export type User = {
  id: number;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
};

export type Supplier = {
  id: number;
  name: string;
  type: string | null;
  country: string | null;
  commodity: string | null;
  website: string | null;
  email: string | null;
  phone: string | null;
  contact_name: string | null;
  description: string | null;
  source: string | null;
  credibility_score: number;
  risk_score: number;
  red_flags: string[];
  classification_confidence: number;
  extra_data: Record<string, unknown>;
};

export type Deal = {
  id: number;
  title: string;
  commodity: string;
  volume_mt: number;
  buy_price: number;
  sell_price: number;
  freight_estimate: number;
  incoterms: string | null;
  currency: string;
  supplier_id: number | null;
  buyer_id: number | null;
  notes: string | null;
  stage: string;
  structure: string | null;
  margin_per_mt: number;
  total_value: number;
  total_margin: number;
  probability: number;
  metrics: { rationale?: string; scenarios?: Scenario[] };
};

export type Scenario = {
  name: string;
  sell_price: number;
  freight: number;
  margin_per_mt: number;
  total_margin: number;
};

export type Document = {
  id: number;
  type: string;
  title: string;
  content: string;
  inputs: Record<string, unknown>;
  deal_id: number | null;
  supplier_id: number | null;
};

export type PipelineBoard = {
  stages: string[];
  columns: Record<string, Deal[]>;
};

export type PipelineStats = {
  by_stage: Record<string, { count: number; value: number }>;
  total_deals: number;
  total_value: number;
  total_margin: number;
};

export type Activity = {
  id: number;
  deal_id: number | null;
  user_id: number | null;
  type: string;
  message: string;
  created_at: string;
};

export type Task = {
  id: number;
  deal_id: number | null;
  title: string;
  due_at: string | null;
  done: boolean;
};

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function getCurrentUser(): User | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  return raw ? (JSON.parse(raw) as User) : null;
}

export function setSession(token: string, user: User): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers || {});
  headers.set("Accept", "application/json");
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
    cache: "no-store",
  });

  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`API ${status}: ${body}`);
  }
}

export const api = {
  // auth
  register: (email: string, password: string, full_name?: string) =>
    request<{ access_token: string; user: User }>("/api/v1/auth/register", {
      method: "POST",
      json: { email, password, full_name },
    }),
  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/api/v1/auth/login", {
      method: "POST",
      json: { email, password },
    }),
  me: () => request<User>("/api/v1/auth/me"),

  // suppliers
  listSuppliers: (params: { q?: string; country?: string; commodity?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.country) qs.set("country", params.country);
    if (params.commodity) qs.set("commodity", params.commodity);
    const s = qs.toString();
    return request<Supplier[]>(`/api/v1/suppliers${s ? `?${s}` : ""}`);
  },
  getSupplier: (id: number) => request<Supplier>(`/api/v1/suppliers/${id}`),
  createSupplier: (payload: Partial<Supplier> & { name: string }) =>
    request<Supplier>("/api/v1/suppliers", { method: "POST", json: payload }),
  discoverSuppliers: (commodity: string, country?: string, limit = 10, persist = true) =>
    request<Supplier[]>(`/api/v1/suppliers/discover?persist=${persist}`, {
      method: "POST",
      json: { commodity, country, limit },
    }),
  classifySupplier: (id: number) =>
    request<{ type: string; confidence: number; reasoning: string }>(
      `/api/v1/suppliers/${id}/classify`,
      { method: "POST" },
    ),

  // deals
  listDeals: (stage?: string) =>
    request<Deal[]>(`/api/v1/deals${stage ? `?stage=${stage}` : ""}`),
  getDeal: (id: number) => request<Deal>(`/api/v1/deals/${id}`),
  createDeal: (payload: Partial<Deal> & { title: string; commodity: string }) =>
    request<Deal>("/api/v1/deals", { method: "POST", json: payload }),
  updateDeal: (id: number, payload: Partial<Deal>) =>
    request<Deal>(`/api/v1/deals/${id}`, { method: "PATCH", json: payload }),
  changeStage: (id: number, stage: string) =>
    request<Deal>(`/api/v1/deals/${id}/stage`, {
      method: "POST",
      json: { stage },
    }),
  structureDeal: (payload: {
    buy_price: number;
    sell_price: number;
    freight_estimate: number;
    volume_mt: number;
    incoterms?: string | null;
  }) =>
    request<{
      margin_per_mt: number;
      total_value: number;
      total_margin: number;
      recommended_structure: string;
      rationale: string;
      scenarios: Scenario[];
    }>("/api/v1/deals/structure", { method: "POST", json: payload }),

  listActivity: (dealId: number) =>
    request<Activity[]>(`/api/v1/deals/${dealId}/activity`),
  addActivity: (dealId: number, message: string) =>
    request<Activity>(`/api/v1/deals/${dealId}/activity`, {
      method: "POST",
      json: { message, type: "note" },
    }),

  listTasks: (dealId: number) => request<Task[]>(`/api/v1/deals/${dealId}/tasks`),
  addTask: (dealId: number, title: string, due_at?: string) =>
    request<Task>(`/api/v1/deals/${dealId}/tasks`, {
      method: "POST",
      json: { title, due_at },
    }),
  toggleTask: (taskId: number, done: boolean) =>
    request<Task>(`/api/v1/deals/tasks/${taskId}?done=${done}`, {
      method: "PATCH",
    }),

  // documents
  listDocuments: (params: { deal_id?: number; supplier_id?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.deal_id) qs.set("deal_id", String(params.deal_id));
    if (params.supplier_id) qs.set("supplier_id", String(params.supplier_id));
    const s = qs.toString();
    return request<Document[]>(`/api/v1/documents${s ? `?${s}` : ""}`);
  },
  generateDocument: (payload: {
    type: string;
    deal_id?: number;
    supplier_id?: number;
    inputs?: Record<string, unknown>;
  }) =>
    request<Document>("/api/v1/documents/generate", {
      method: "POST",
      json: payload,
    }),
  getDocument: (id: number) => request<Document>(`/api/v1/documents/${id}`),
  updateDocument: (id: number, payload: { title?: string; content?: string }) =>
    request<Document>(`/api/v1/documents/${id}`, {
      method: "PATCH",
      json: payload,
    }),
  documentMarkdownUrl: (id: number) =>
    `${API_BASE}/api/v1/documents/${id}/export.md`,
  documentDocxUrl: (id: number) =>
    `${API_BASE}/api/v1/documents/${id}/export.docx`,

  // pipeline
  pipelineBoard: () => request<PipelineBoard>("/api/v1/pipeline/board"),
  pipelineStats: () => request<PipelineStats>("/api/v1/pipeline/stats"),
};

export const STAGE_LABELS: Record<string, string> = {
  lead: "Lead",
  contacted: "Contacted",
  qualified: "Qualified",
  pricing: "Pricing",
  buyer_matched: "Buyer Matched",
  spa: "SPA",
  lc: "LC",
  shipment: "Shipment",
  closed: "Closed",
  lost: "Lost",
};
