export type State = 'UPLOADED' | 'EXTRACTING' | 'EXTRACTED' | 'RECONCILING' | 'MATCHED' |
  'REVIEW_REQUIRED' | 'APPROVED' | 'REJECTED' | 'PROCESSING_FAILED';

export type Persona = 'analyst' | 'admin' | 'viewer';
export type Line = {
  product_code: string | null; description: string | null; quantity: string | null;
  unit_price: string | null; line_total?: string | null;
};
export type Invoice = {
  invoice_number: string | null; invoice_date: string | null; vendor_name: string | null;
  vendor_id: string | null; purchase_order_number: string | null; currency: string | null;
  subtotal: string | null; tax_amount: string | null; total_amount: string | null; line_items: Line[];
};
export type HeaderField = Exclude<keyof Invoice, 'line_items'>;
export type FieldEvidence = { status: string; sources: { page: number; excerpt: string }[] };
type Extraction = {
  invoice: Invoice; fields: Record<string, FieldEvidence>; provider: string;
  confidence: number; warnings: string[];
};
export type Summary = {
  id: string; filename: string; state: State; version: number; created_at: string;
  invoice_number: string | null; vendor_name: string | null; exception_codes: string[]; processing_ms: number | null;
};
export type Detail = Summary & {
  extraction: Extraction | null; original: Extraction | null; error: string | null; duplicate_upload: boolean;
  purchase_order: { number: string; vendor_id: string; currency: string; tax_rate: string; lines: Line[] } | null;
  result: {
    status: State; rule_version: number; decision_explanation: string;
    discrepancy_details: { code: string; field: string; explanation: string; actual: string | null; expected: string | null }[];
  } | null;
};
export type Metrics = {
  total_uploaded: number; total_processed: number; matched: number; review_required: number;
  approved: number; rejected: number; processing_failed: number; average_processing_ms: number | null;
};
export type Audit = {
  id: number; invoice_id: string | null; timestamp: string; actor: string; action: string;
  previous_state: string | null; new_state: string | null; reason: string | null; changes: unknown;
};
export type RuleSet = {
  version: number; rules: { amount_absolute: string; amount_percent: string; quantity_absolute: string;
    quantity_percent: string; minimum_confidence: number };
};
export type Evaluation = {
  metadata: { dataset_version: string; split: string; sample_size: number; generated_at: string; provider: string };
  baseline: {
    extraction: { micro: { f1: number }; total_amount_accuracy: number };
    reconciliation: { accuracy: number; exception_recall: number };
    operational: { p50_ms: number; p95_ms: number; manual_review_rate: number };
    groups: Record<string, Record<string, { n: number; decision_accuracy: number }>>;
  };
  llm: { status: string };
};

export const request = async (persona: Persona, path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  headers.set('X-Demo-User', persona);
  if (typeof init.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch('/api' + path, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = Array.isArray(body.detail)
      ? body.detail.map((error: { loc: string[]; msg: string }) => error.loc.slice(1).join('.') + ': ' + error.msg).join('; ')
      : body.detail;
    throw new Error(message || `Request failed (${response.status}).`);
  }
  return response;
};

export async function api<T>(persona: Persona, path: string, init?: RequestInit): Promise<T> {
  return (await request(persona, path, init)).json();
}

