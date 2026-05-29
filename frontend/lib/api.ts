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
  company_name: string | null;
  title: string | null;
  phone: string | null;
  role: string;
  is_active: boolean;
};

export type UserUpdate = {
  full_name?: string | null;
  company_name?: string | null;
  title?: string | null;
  phone?: string | null;
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

export function storeUser(user: User): void {
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
  updateMe: (payload: UserUpdate) =>
    request<User>("/api/v1/auth/me", { method: "PATCH", json: payload }),

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
    opportunity_id?: number;
    supplier_lead_id?: number;
    buyer_lead_id?: number;
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

  // opportunities (V2 multi-counterparty orchestration)
  listOpportunities: () =>
    request<Opportunity[]>("/api/v1/opportunities"),
  createOpportunity: (payload: OpportunityInput) =>
    request<Opportunity>("/api/v1/opportunities", {
      method: "POST",
      json: payload,
    }),
  getOpportunity: (id: number) =>
    request<Opportunity>(`/api/v1/opportunities/${id}`),
  updateOpportunity: (id: number, payload: Partial<OpportunityInput> & { status?: string }) =>
    request<Opportunity>(`/api/v1/opportunities/${id}`, {
      method: "PATCH",
      json: payload,
    }),
  deleteOpportunity: (id: number) =>
    request<void>(`/api/v1/opportunities/${id}`, { method: "DELETE" }),
  getOpportunityDashboard: (id: number) =>
    request<OpportunityDashboard>(`/api/v1/opportunities/${id}/dashboard`),
  createSupplierLead: (opportunityId: number, payload: SupplierLeadInput) =>
    request<SupplierLead>(
      `/api/v1/opportunities/${opportunityId}/supplier-leads`,
      { method: "POST", json: payload },
    ),
  updateSupplierLead: (
    opportunityId: number,
    leadId: number,
    payload: Partial<SupplierLeadInput>,
  ) =>
    request<SupplierLead>(
      `/api/v1/opportunities/${opportunityId}/supplier-leads/${leadId}`,
      { method: "PATCH", json: payload },
    ),
  deleteSupplierLead: (opportunityId: number, leadId: number) =>
    request<void>(
      `/api/v1/opportunities/${opportunityId}/supplier-leads/${leadId}`,
      { method: "DELETE" },
    ),
  listCuratedSuppliers: (opportunityId: number) =>
    request<CuratedCounterparty[]>(
      `/api/v1/opportunities/${opportunityId}/curated-suppliers`,
    ),
  seedCuratedSuppliers: (opportunityId: number, names: string[] = []) =>
    request<SupplierLead[]>(
      `/api/v1/opportunities/${opportunityId}/curated-suppliers/seed`,
      { method: "POST", json: { names } },
    ),
  createBuyerLead: (opportunityId: number, payload: BuyerLeadInput) =>
    request<BuyerLead>(`/api/v1/opportunities/${opportunityId}/buyer-leads`, {
      method: "POST",
      json: payload,
    }),
  updateBuyerLead: (
    opportunityId: number,
    leadId: number,
    payload: Partial<BuyerLeadInput>,
  ) =>
    request<BuyerLead>(
      `/api/v1/opportunities/${opportunityId}/buyer-leads/${leadId}`,
      { method: "PATCH", json: payload },
    ),
  deleteBuyerLead: (opportunityId: number, leadId: number) =>
    request<void>(
      `/api/v1/opportunities/${opportunityId}/buyer-leads/${leadId}`,
      { method: "DELETE" },
    ),
  promoteMatchToDeal: (
    opportunityId: number,
    payload: { supplier_lead_id: number; buyer_lead_id: number; title?: string },
  ) =>
    request<{
      deal_id: number;
      opportunity_id: number;
      supplier_lead_id: number;
      buyer_lead_id: number;
      title: string;
      buy_price: number;
      sell_price: number;
      volume_mt: number;
      margin_per_mt: number;
      total_margin: number;
    }>(`/api/v1/opportunities/${opportunityId}/deals`, {
      method: "POST",
      json: payload,
    }),

  // prices (Yahoo Finance)
  listCommodities: () => request<{ commodities: CommodityInfo[] }>("/api/v1/prices"),
  getPrice: (commodity: string, refresh = false) =>
    request<CommodityQuote>(
      `/api/v1/prices/${encodeURIComponent(commodity)}${refresh ? "?refresh=true" : ""}`,
    ),
};

export type CommodityInfo = {
  slug: string;
  display: string;
  ticker: string;
  exchange: string;
  quoted_unit: string;
  supports_mt: boolean;
};

export type CommodityQuote = {
  commodity: string;
  display: string;
  ticker: string;
  exchange: string;
  quoted_unit: string;
  raw_price: number;
  price_mt: number | null;
  currency: string;
  timestamp: number;
  previous_close: number | null;
  change_pct: number | null;
  source: string;
};

// --- V2 opportunity types ---

export type OpportunityInput = {
  title: string;
  commodity: string;
  volume_mt?: number;
  destination_country?: string | null;
  destination_port?: string | null;
  incoterms?: string | null;
  target_price_min?: number | null;
  target_price_max?: number | null;
  currency?: string;
  notes?: string | null;
};

export type Opportunity = {
  id: number;
  title: string;
  commodity: string;
  volume_mt: number;
  destination_country: string | null;
  destination_port: string | null;
  incoterms: string | null;
  target_price_min: number | null;
  target_price_max: number | null;
  currency: string;
  notes: string | null;
  status: string;
  owner_id: number | null;
  created_at: string | null;
  updated_at: string | null;
};

export type SupplierLeadInput = {
  supplier_id?: number | null;
  supplier_name?: string | null;
  country?: string | null;
  email?: string | null;
  contact_name?: string | null;
  contact_title?: string | null;
  price_mt?: number | null;
  quoted_incoterms?: string | null;
  min_order_mt?: number | null;
  lead_time_days?: number | null;
  payment_terms?: string | null;
  credibility_score?: number;
  responsiveness_score?: number;
  notes?: string | null;
  status?: string;
  last_contacted_at?: string | null;
  negotiation_stage?: number;
  intel?: Record<string, unknown>;
  disclosed?: Record<string, unknown>;
};

export type CuratedCounterparty = {
  name: string;
  country: string;
  commodity: string;
  website: string;
  type: string;
  description: string;
  already_added: boolean;
};

export type SupplierLead = Required<
  Pick<SupplierLeadInput, "credibility_score" | "responsiveness_score">
> & {
  id: number;
  opportunity_id: number;
  status: string;
  last_contacted_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  supplier_id: number | null;
  supplier_name: string | null;
  country: string | null;
  email: string | null;
  contact_name: string | null;
  contact_title: string | null;
  price_mt: number | null;
  quoted_incoterms: string | null;
  min_order_mt: number | null;
  lead_time_days: number | null;
  payment_terms: string | null;
  notes: string | null;
  negotiation_stage: number;
  intel: Record<string, unknown>;
  disclosed: Record<string, unknown>;
};

export type BuyerLeadInput = {
  buyer_id?: number | null;
  buyer_name?: string | null;
  country?: string | null;
  email?: string | null;
  target_price_mt?: number | null;
  volume_mt?: number | null;
  appetite?: "low" | "medium" | "high";
  urgency?: "low" | "medium" | "high";
  feedback?: string | null;
  notes?: string | null;
  status?: string;
  last_contacted_at?: string | null;
  negotiation_stage?: number;
  intel?: Record<string, unknown>;
  disclosed?: Record<string, unknown>;
};

export type BuyerLead = {
  id: number;
  opportunity_id: number;
  status: string;
  last_contacted_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  buyer_id: number | null;
  buyer_name: string | null;
  country: string | null;
  email: string | null;
  target_price_mt: number | null;
  volume_mt: number | null;
  appetite: "low" | "medium" | "high";
  urgency: "low" | "medium" | "high";
  feedback: string | null;
  notes: string | null;
  negotiation_stage: number;
  intel: Record<string, unknown>;
  disclosed: Record<string, unknown>;
};

export const NEGOTIATION_STAGE_LABELS: Record<number, string> = {
  1: "Cold outreach",
  2: "First response / SCO",
  3: "Counter-offer",
  4: "Terms negotiation",
  5: "Close / SPA",
};

export type MatchPair = {
  supplier_lead_id: number;
  supplier_name: string | null;
  supplier_price_mt: number | null;
  buyer_lead_id: number;
  buyer_name: string | null;
  buyer_target_price_mt: number | null;
  margin_per_mt: number;
  total_margin: number | null;
  score: number;
  reasoning: string[];
};

export type MatchingResult = {
  opportunity_id: number;
  total_pairs: number;
  viable_pairs: number;
  pairs: MatchPair[];
};

export type HealthFactor = {
  name: string;
  weight: number;
  value: number;
  contribution: number;
  detail: string;
};

export type HealthScore = {
  opportunity_id: number;
  score: number;
  status: string;
  factors: HealthFactor[];
  recommendation: string;
};

export type NextAction = {
  action: string;
  priority: "high" | "medium" | "low";
  reasoning: string;
};

export type NextActionsOut = {
  opportunity_id: number;
  actions: NextAction[];
};

export type OpportunityDashboard = {
  opportunity: Opportunity;
  supplier_leads: SupplierLead[];
  buyer_leads: BuyerLead[];
  matches: MatchingResult;
  health: HealthScore;
  next_actions: NextActionsOut;
};

export const OPPORTUNITY_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  sourcing: "Sourcing",
  negotiating: "Negotiating",
  matched: "Matched",
  closed: "Closed",
  lost: "Lost",
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
