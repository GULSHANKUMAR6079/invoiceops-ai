import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { FormEvent, ReactNode } from 'react';
import { api, request } from './api';
import type { Audit, Detail, Evaluation, HeaderField, Invoice, Line, Metrics, Persona, RuleSet, State, Summary } from './api';
import './style.css';

const states: State[] = ['UPLOADED', 'EXTRACTED', 'MATCHED', 'REVIEW_REQUIRED', 'APPROVED', 'REJECTED', 'PROCESSING_FAILED'];
const labels: Record<HeaderField, string> = {
  invoice_number: 'Invoice number', invoice_date: 'Invoice date', vendor_name: 'Supplier name',
  vendor_id: 'Supplier ID', purchase_order_number: 'PO reference', currency: 'Currency',
  subtotal: 'Subtotal', tax_amount: 'Tax amount', total_amount: 'Invoice total',
};
const fields = Object.keys(labels) as HeaderField[];
const human = (value: string) => value.toLowerCase().replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const pct = (value: number) => `${(value * 100).toFixed(1)}%`;
const limit = 20;
type Screen = 'Overview' | 'Upload' | 'Review queue' | 'Audit & evaluation' | 'Rule settings';

function Badge({ state }: { state: string }) {
  return <span className={`badge ${state.toLowerCase()}`}>{human(state)}</span>;
}

function Panel({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return <section className="panel"><div className="panel-heading"><h2>{title}</h2>{note && <p>{note}</p>}</div>{children}</section>;
}

function Pager({ offset, count, onChange }: { offset: number; count: number; onChange: (value: number) => void }) {
  return <div className="pager"><button className="quiet" disabled={!offset} onClick={() => onChange(offset - limit)}>Previous</button>
    <span>Page {1 + offset / limit}</span><button className="quiet" disabled={count < limit}
      onClick={() => onChange(offset + limit)}>Next</button></div>;
}

function Queue({ persona, open, overview = false }: { persona: Persona; open: (id: string) => void; overview?: boolean }) {
  const [rows, setRows] = useState<Summary[]>([]);
  const [filter, setFilter] = useState(overview ? '' : 'REVIEW_REQUIRED');
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setError('');
    setLoading(true);
    api<Summary[]>(persona, `/invoices?limit=${overview ? 5 : limit}&offset=${offset}${filter ? '&state=' + filter : ''}`)
      .then(data => { if (active) setRows(data); })
      .catch(err => { if (active) setError(errorText(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [persona, filter, offset, overview]);
  return <Panel title={overview ? 'Recent invoices' : 'Invoice queue'} note="Select a document to inspect its evidence and workflow.">
    {!overview && <label className="inline">Status <select value={filter} onChange={event => {
      setFilter(event.target.value); setOffset(0);
    }}><option value="">All invoices</option>{states.map(state => <option key={state} value={state}>{human(state)}</option>)}</select></label>}
    {error && <p className="error" role="alert">{error}</p>}
    {loading ? <p role="status">Loading invoices…</p> : <div className="table-wrap"><table><thead><tr>
      <th>Invoice</th><th>Supplier</th><th>Status</th><th>Exceptions</th><th>Uploaded</th>
    </tr></thead><tbody>{rows.map(row => <tr key={row.id}><td><button className="text-button" onClick={() => open(row.id)}>
      {row.invoice_number || row.filename}</button><small>{row.filename}</small></td>
      <td>{row.vendor_name || '—'}</td><td><Badge state={row.state} /></td>
      <td>{row.exception_codes.length ? row.exception_codes.map(human).join(', ') : '—'}</td>
      <td>{new Date(row.created_at).toLocaleDateString()}</td></tr>)}</tbody></table>
    {!rows.length && <p className="empty">No invoices in this view.</p>}</div>}
    {!overview && !loading && <Pager offset={offset} count={rows.length} onChange={setOffset} />}
  </Panel>;
}

function Overview({ persona, open }: { persona: Persona; open: (id: string) => void }) {
  const [metrics, setMetrics] = useState<Metrics>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    api<Metrics>(persona, '/dashboard/metrics').then(data => { if (active) setMetrics(data); })
      .catch(err => { if (active) setError(errorText(err)); });
    return () => { active = false; };
  }, [persona]);
  const stats = [
    ['Processed', metrics?.total_processed], ['Matched', metrics?.matched],
    ['Needs review', metrics?.review_required], ['Rejected', metrics?.rejected],
    ['Average processing', metrics?.average_processing_ms != null ? metrics.average_processing_ms.toFixed(1) + ' ms' : undefined],
  ];
  return <>{error && <p className="error" role="alert">{error}</p>}
    <div className="stats">{stats.map(([name, value]) => <div className="stat" key={name}><span>{name}</span><strong>{value ?? '—'}</strong></div>)}</div>
    <Queue persona={persona} open={open} overview />
    <p className="footnote">Status counts reflect the current queue. Processing time covers extraction and reconciliation work.</p>
  </>;
}

function Upload({ persona, show }: { persona: Persona; show: (row: Detail) => void }) {
  const [file, setFile] = useState<File>();
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState<Detail>();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || progress) return;
    setError('');
    setSaved(undefined);
    try {
      setProgress('Uploading PDF…');
      const form = new FormData();
      form.append('file', file);
      let row = await api<Detail>(persona, '/invoices/upload', { method: 'POST', body: form });
      setSaved(row);
      if (row.duplicate_upload) {
        show(row);
        return;
      }
      setProgress('Extracting invoice fields…');
      row = await api<Detail>(persona, `/invoices/${row.id}/extract`, { method: 'POST' });
      setSaved(row);
      if (row.state === 'EXTRACTED') {
        setProgress('Checking purchase order and totals…');
        row = await api<Detail>(persona, `/invoices/${row.id}/reconcile`, { method: 'POST' });
      }
      show(row);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setProgress('');
    }
  }
  return <Panel title="Upload an invoice" note="Use a text-based PDF up to 8 MB. The original is available alongside the extracted fields.">
    <form onSubmit={submit} className="upload">
      <label className="drop">Choose invoice PDF<input type="file" accept=".pdf,application/pdf"
        disabled={!!progress || persona === 'viewer'} onChange={event => setFile(event.target.files?.[0])} required /></label>
      <button type="submit" disabled={!file || !!progress || persona === 'viewer'}>Upload and process</button>
      {progress && <p role="status">{progress}</p>}
      {persona === 'viewer' && <p>This persona has read-only access.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {error && saved && <button type="button" className="quiet" onClick={() => show(saved)}>Open saved invoice</button>}
    </form>
  </Panel>;
}

function PdfPreview({ persona, row }: { persona: Persona; row: Detail }) {
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    let blobUrl = '';
    setUrl('');
    setError('');
    request(persona, `/invoices/${row.id}/preview/${page}`).then(async response => {
      const blob = await response.blob();
      if (active) {
        blobUrl = URL.createObjectURL(blob);
        setUrl(blobUrl);
        setPages(Number(response.headers.get('X-Page-Count') || 1));
      }
    }).catch(err => { if (active) setError(errorText(err)); });
    return () => { active = false; if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [persona, row.id, page]);
  async function download() {
    try {
      const response = await request(persona, `/invoices/${row.id}/document`);
      const href = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = href;
      link.download = row.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(href), 5000);
    } catch (err) { setError(errorText(err)); }
  }
  return <Panel title="Original document" note={row.filename}>
    {error ? <p className="error" role="alert">{error}</p> : url
      ? <img className="pdf" src={url} alt={`Original PDF, page ${page}`} /> : <p>Rendering page…</p>}
    <div className="pager"><button className="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
      <span>{page} / {pages}</span><button className="quiet" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
    <button className="quiet" onClick={download}>Download original PDF</button>
  </Panel>;
}

function LineTable({ lines }: { lines: Line[] }) {
  if (!lines.length) return <p className="empty">No line items available.</p>;
  return <div className="table-wrap"><table><thead><tr><th>Code / item</th><th>Qty</th><th>Unit price</th><th>Total</th></tr></thead>
    <tbody>{lines.map((line, index) => <tr key={index}><td>{line.description || '—'}<small>{line.product_code}</small></td>
      <td>{line.quantity ?? '—'}</td><td>{line.unit_price ?? '—'}</td><td>{line.line_total ?? '—'}</td></tr>)}</tbody></table></div>;
}

function Corrections({ invoice, busy, close, save }: {
  invoice: Invoice; busy: boolean; close: () => void; save: (body: Partial<Invoice>, reason: string) => void;
}) {
  const [values, setValues] = useState(invoice);
  const [lines, setLines] = useState(JSON.stringify(invoice.line_items, null, 2));
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  function submit(event: FormEvent) {
    event.preventDefault();
    const changed: Partial<Invoice> = {};
    fields.forEach(field => { if (values[field] !== invoice[field]) changed[field] = values[field]; });
    try {
      const parsed: unknown = JSON.parse(lines);
      if (!Array.isArray(parsed)) throw new Error('Line items must be a JSON array.');
      if (JSON.stringify(parsed) !== JSON.stringify(invoice.line_items)) changed.line_items = parsed;
      if (!Object.keys(changed).length) throw new Error('Edit at least one field.');
      setError('');
      save(changed, reason);
    } catch (err) { setError(errorText(err)); }
  }
  return <form className="stack correction" onSubmit={submit}><div className="form-grid">
    {fields.map(field => <label key={field}>{labels[field]}<input value={values[field] ?? ''}
      onChange={event => setValues({ ...values, [field]: event.target.value || null })} /></label>)}</div>
    <label>Line items (JSON)<textarea rows={7} value={lines} onChange={event => setLines(event.target.value)} /></label>
    <label>Correction reason<input required maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="actions"><button disabled={busy}>Save and reconcile</button><button type="button" className="quiet" onClick={close}>Cancel</button></div>
  </form>;
}

function InvoiceDetail({ persona, row, show }: { persona: Persona; row: Detail; show: (row: Detail) => void }) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const current = row.extraction?.invoice;
  const original = row.original?.invoice;
  const editor = persona !== 'viewer';
  const review = row.state === 'MATCHED' || row.state === 'REVIEW_REQUIRED';
  async function mutate(action: string, method = 'POST', payload?: unknown) {
    setError('');
    setBusy(action);
    try {
      let next = await api<Detail>(persona, `/invoices/${row.id}/${action}`,
        { method, body: payload === undefined ? undefined : JSON.stringify(payload) });
      if (action === 'extract' && next.state === 'EXTRACTED') {
        show(next);
        next = await api<Detail>(persona, `/invoices/${row.id}/reconcile`, { method: 'POST' });
      }
      show(next);
      setEditing(false);
      setReason('');
    } catch (err) { setError(errorText(err)); } finally { setBusy(''); }
  }
  return <>
    <div className="detail-status"><Badge state={row.state} />
      <span>{row.extraction ? `Extraction via ${row.extraction.provider}` : 'Original upload'} · revision {row.version}</span></div>
    {row.duplicate_upload && <p className="notice">This file already exists. The dashboard opened its existing record.</p>}
    {row.error && <p className="error" role="alert">{row.error}</p>}
    {error && <p className="error" role="alert">{error} <button className="text-button"
      onClick={() => api<Detail>(persona, `/invoices/${row.id}`).then(show).catch(err => setError(errorText(err)))}>Refresh record</button></p>}
    {busy && <p role="status">Working on {human(busy)}…</p>}
    {editor && (['UPLOADED', 'PROCESSING_FAILED'].includes(row.state) || row.state === 'EXTRACTED') &&
      <div className="actions"><button disabled={!!busy} onClick={() => mutate(row.state === 'EXTRACTED' ? 'reconcile' : 'extract')}>
        {row.state === 'PROCESSING_FAILED' ? 'Retry extraction' : 'Continue processing'}</button></div>}
    <div className="detail-grid"><PdfPreview persona={persona} row={row} /><div className="stack">
      <Panel title="Reconciliation" note={row.result?.decision_explanation}>
        {row.result?.discrepancy_details.map((item, index) => <div className="discrepancy" key={index}>
          <strong>{human(item.code)}</strong><p>{item.explanation}</p><small>{item.field}
          {item.actual !== null && ` · Invoice ${item.actual}`}{item.expected !== null && ` · Expected ${item.expected}`}</small>
        </div>)}
        {row.result && <p className="footnote">Rule version {row.result.rule_version}</p>}
        {editor && review && <form className="stack" onSubmit={event => event.preventDefault()}>
          <label>Decision reason<input maxLength={1000} placeholder="Required for rejection or exception override"
            value={reason} onChange={event => setReason(event.target.value)} /></label>
          <div className="actions"><button type="button" disabled={!!busy || (row.state === 'REVIEW_REQUIRED' && !reason.trim())
            || row.exception_codes.includes('DUPLICATE_INVOICE')} onClick={() => mutate('approve', 'POST', { version: row.version, reason: reason.trim() || null })}>Approve</button>
            <button type="button" className="danger" disabled={!!busy || !reason.trim()} onClick={() =>
              mutate('reject', 'POST', { version: row.version, reason: reason.trim() })}>Reject</button></div>
          <div><button type="button" className="quiet" disabled={!!busy}
            onClick={() => mutate('reconcile?rerun=true')}>Recheck with current rules</button></div>
        </form>}
      </Panel>
      <Panel title="Extracted fields" note="Open a field to see the source excerpt and prior value.">
        {current && !editing && <><dl className="fields">{fields.map(field => <div key={field}><dt>{labels[field]}</dt><dd>
          <details><summary>{current[field] ?? '—'} <small>{row.extraction?.fields[field]?.status}</small></summary>
            {original?.[field] !== current[field] && <p>Original value: {original?.[field] ?? '—'}</p>}
            {row.extraction?.fields[field]?.sources.map((source, index) =>
              <p className="excerpt" key={index}>Page {source.page} · {source.excerpt}</p>)}
          </details></dd></div>)}</dl><LineTable lines={current.line_items} />
          {row.extraction?.fields.line_items?.sources.length ? <details className="line-source"><summary>Line item evidence</summary>
            {row.extraction.fields.line_items.sources.map((source, index) =>
              <p className="excerpt" key={index}>Page {source.page} · {source.excerpt}</p>)}
          </details> : null}
          {editor && (review || row.state === 'EXTRACTED') && <button className="quiet" onClick={() => setEditing(true)}>Correct extracted fields</button>}</>}
        {current && editing && <Corrections invoice={current} busy={!!busy} close={() => setEditing(false)}
          save={(body, why) => mutate('extracted-fields', 'PATCH', { fields: body, version: row.version, reason: why })} />}
        {!current && <p className="empty">Extraction has no structured output yet.</p>}
      </Panel>
      <Panel title="Purchase order" note={row.purchase_order ? `${row.purchase_order.number} · ${row.purchase_order.currency}` : 'No purchase order linked.'}>
        {row.purchase_order && <><p>Supplier {row.purchase_order.vendor_id} · Tax {pct(Number(row.purchase_order.tax_rate))}</p>
          <LineTable lines={row.purchase_order.lines} /></>}
      </Panel>
    </div></div>
    <AuditList persona={persona} invoiceId={row.id} revision={row.version} />
  </>;
}

function AuditList({ persona, invoiceId, revision }: { persona: Persona; invoiceId?: string; revision?: number }) {
  const [events, setEvents] = useState<Audit[]>([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    api<Audit[]>(persona, `/audit-logs?limit=${limit}&offset=${offset}${invoiceId ? '&invoice_id=' + invoiceId : ''}`)
      .then(data => { if (active) { setEvents(data); setError(''); } })
      .catch(err => { if (active) setError(errorText(err)); });
    return () => { active = false; };
  }, [persona, invoiceId, revision, offset]);
  return <Panel title={invoiceId ? 'Invoice history' : 'Audit log'} note="Workflow transitions, reviewer reasons, and rule changes.">
    {error && <p className="error" role="alert">{error}</p>}
    <div className="table-wrap"><table><thead><tr><th>Action</th><th>Actor</th><th>Transition</th><th>Reason</th><th>Time</th></tr></thead>
      <tbody>{events.map(event => <tr key={event.id}><td>{human(event.action)}
        {event.changes != null && <details><summary>Changes</summary><pre>{JSON.stringify(event.changes, null, 2)}</pre></details>}</td>
        <td>{event.actor}</td><td>{event.previous_state ? human(event.previous_state) + ' → ' : ''}{event.new_state ? human(event.new_state) : '—'}</td>
        <td>{event.reason || '—'}</td><td>{new Date(event.timestamp).toLocaleString()}</td></tr>)}</tbody></table></div>
    {!events.length && <p className="empty">No audit events yet.</p>}
    <Pager offset={offset} count={events.length} onChange={setOffset} />
  </Panel>;
}

function EvaluationPanel({ persona }: { persona: Persona }) {
  const [report, setReport] = useState<Evaluation>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    api<Evaluation>(persona, '/evaluation').then(data => { if (active) setReport(data); })
      .catch(err => { if (active) setError(errorText(err)); });
    return () => { active = false; };
  }, [persona]);
  return <Panel title="Synthetic benchmark" note="Saved evaluation run. These metrics are separate from live workflow activity.">
    {error ? <p className="notice">{error}</p> : !report ? <p>Loading benchmark…</p> : <>
      <p>{report.metadata.dataset_version} · {report.metadata.split} split · {report.metadata.sample_size} documents · {report.metadata.provider}</p>
      <dl className="fields report">
        <div><dt>Field extraction micro F1</dt><dd>{pct(report.baseline.extraction.micro.f1)}</dd></div>
        <div><dt>Decision accuracy</dt><dd>{pct(report.baseline.reconciliation.accuracy)}</dd></div>
        <div><dt>Exception recall</dt><dd>{pct(report.baseline.reconciliation.exception_recall)}</dd></div>
        <div><dt>p50 / p95 pipeline latency</dt><dd>{report.baseline.operational.p50_ms.toFixed(2)} / {report.baseline.operational.p95_ms.toFixed(2)} ms</dd></div>
        <div><dt>Manual review rate</dt><dd>{pct(report.baseline.operational.manual_review_rate)}</dd></div>
        <div><dt>Groq extraction</dt><dd>{report.llm.status}</dd></div>
      </dl>
      {Object.entries(report.baseline.groups.layout_group ?? {}).map(([name, group]) =>
        <div className="bar-row" key={name}><label htmlFor={name}>{human(name)} layout · {group.n} documents</label>
          <meter id={name} min={0} max={1} value={group.decision_accuracy} /><strong>{pct(group.decision_accuracy)}</strong></div>)}
    </>}
  </Panel>;
}

function RuleSettings({ persona }: { persona: Persona }) {
  const [data, setData] = useState<RuleSet>();
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    api<RuleSet>(persona, '/rules').then(setData).catch(err => setError(errorText(err)));
  }, [persona]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!data || saving) return;
    setSaving(true); setError(''); setMessage('');
    try {
      setData(await api<RuleSet>(persona, '/rules', { method: 'PUT', body: JSON.stringify({ ...data, reason }) }));
      setReason(''); setMessage('Rule version saved.');
    } catch (err) { setError(errorText(err)); } finally { setSaving(false); }
  }
  return <Panel title="Matching policy" note="Tolerances apply to subsequent reconciliations. Each result retains the rule version used.">
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="notice" role="status">{message}</p>}
    {data && <form className="stack" onSubmit={save}><p>Current version {data.version}</p><div className="form-grid">
      {(Object.keys(data.rules) as (keyof RuleSet['rules'])[]).map(key => <label key={key}>{human(key)}
        <input type="number" min="0" step="any" required value={data.rules[key]} onChange={event =>
          setData({ ...data, rules: { ...data.rules, [key]: event.target.value } })} /></label>)}</div>
      <label>Change reason<input required maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <div><button disabled={saving}>Save rule version</button></div>
    </form>}
  </Panel>;
}

const navigation: { name: Screen; icon: string }[] = [
  { name: 'Overview', icon: '01' }, { name: 'Upload', icon: '02' }, { name: 'Review queue', icon: '03' },
  { name: 'Audit & evaluation', icon: '04' }, { name: 'Rule settings', icon: '05' },
];
const descriptions: Record<Screen, string> = {
  Overview: 'Track invoice processing and pending decisions.',
  Upload: 'Start with an invoice PDF. We’ll extract fields and check the purchase order.',
  'Review queue': 'Inspect discrepancies and record a human decision.',
  'Audit & evaluation': 'Trace workflow decisions and inspect measured benchmark results.',
  'Rule settings': 'Manage the tolerances used for deterministic reconciliation.',
};

function App() {
  const [persona, setPersona] = useState<Persona>('analyst');
  const [screen, setScreen] = useState<Screen>('Overview');
  const [row, setRow] = useState<Detail>();
  const [error, setError] = useState('');
  const [provider, setProvider] = useState('');
  useEffect(() => {
    api<{ provider: string }>(persona, '/health').then(data => setProvider(data.provider)).catch(err => setError(errorText(err)));
  }, [persona]);
  const go = (next: Screen) => { setRow(undefined); setScreen(next); setError(''); };
  const show = (next: Detail) => { setRow(next); setError(''); };
  async function open(id: string) {
    try { show(await api<Detail>(persona, `/invoices/${id}`)); }
    catch (err) { setError(errorText(err)); }
  }
  return <div className="shell">
    <aside className="sidebar"><div className="brand"><span className="logo">I</span><div>InvoiceOps AI<small>ACCOUNTS PAYABLE</small></div></div>
      <nav aria-label="Workspace">{navigation.filter(item => item.name !== 'Rule settings' || persona === 'admin').map(item =>
        <button key={item.name} className={screen === item.name && !row ? 'active' : ''} onClick={() => go(item.name)}>
          <span aria-hidden="true">{item.icon}</span>{item.name}</button>)}</nav>
      <div className="side-note"><span className="dot" /> Local demo workspace</div>
    </aside>
    <main><header className="top"><span>OPERATIONS / WORKSPACE</span>
      <label className="persona">Demo persona <select value={persona} onChange={event => {
        setPersona(event.target.value as Persona); go('Overview');
      }}><option value="analyst">AP Analyst</option><option value="admin">Finance Administrator</option>
        <option value="viewer">Read-only Viewer</option></select></label></header>
      <div className="content">
        {row && <button className="text-button back" onClick={() => setRow(undefined)}>← Back to {screen.toLowerCase()}</button>}
        <div className="intro"><div><div className="eyebrow">{row ? 'DOCUMENT RECORD' : 'INVOICE OPERATIONS'}</div>
          <h1>{row ? row.invoice_number || row.filename : screen}</h1>
          <p>{row ? 'Review the original, extracted values, and matching evidence.' : descriptions[screen]}</p></div>
          {!row && <span className="mode">{provider === 'groq' ? 'Groq extraction' : provider ? `Offline · ${provider}` : 'Connecting'}</span>}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {row ? <InvoiceDetail key={row.id} persona={persona} row={row} show={show} />
          : screen === 'Overview' ? <Overview persona={persona} open={open} />
          : screen === 'Upload' ? <Upload persona={persona} show={show} />
          : screen === 'Review queue' ? <Queue persona={persona} open={open} />
          : screen === 'Rule settings' ? <RuleSettings persona={persona} />
          : <><EvaluationPanel persona={persona} /><AuditList persona={persona} /></>}
      </div>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);

