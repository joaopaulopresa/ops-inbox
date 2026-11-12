import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { fieldLabel, shortReason } from "../../format";
import { BarList, Columns, LineChart, type Series } from "./charts";
import { owner, type Automation, type Dashboard, type RuleDef, type RuleTest } from "./ownerApi";

const TUNING = "#00875A";
const HELDOUT = "#C0802B";
const pct = (v: number) => `${Math.round(v * 100)}%`;

export function OwnerRoutes() {
  return (
    <Routes>
      <Route path="dashboard" element={<DashboardPage />} />
      <Route path="thresholds" element={<AutomationPage />} />
      <Route path="rules" element={<RulesPage />} />
      <Route path="templates" element={<TemplatesPage />} />
      <Route path="records" element={<RecordsPage />} />
      <Route path="*" element={<Navigate to="dashboard" replace />} />
    </Routes>
  );
}

function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    load()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [load]);
  useEffect(reload, [reload]);
  return { data, error, reload };
}

// ------------------------------------------------------------------------------ dashboard

function DashboardPage() {
  const { data, error } = useLoad<Dashboard>(owner.dashboard);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>The last 30 days of the inbox.</p>
        </div>
        <a className="button" href="/api/export/bookings.csv">
          Download bookings sheet
        </a>
      </div>
      {error && <p className="error">Could not load the dashboard: {error}</p>}
      {!data && !error && <p className="loading">Loading…</p>}
      {data && (
        <>
          <div className="tiles">
            <div className="tile">
              <strong>{data.emails}</strong>
              <span>e-mails received</span>
            </div>
            <div className="tile">
              <strong>{data.straight_through_rate === null ? "n/a" : pct(data.straight_through_rate)}</strong>
              <span>handled without a person ({data.automatic} of {data.decided})</span>
            </div>
            <div className="tile">
              <strong>{data.median_review_minutes === null ? "n/a" : `${data.median_review_minutes} min`}</strong>
              <span>median time from arrival to a decision, for {data.reviewed} reviewed</span>
            </div>
            <div className="tile">
              <strong>{data.flagged_after_the_fact}</strong>
              <span>errors found after the fact</span>
            </div>
          </div>
          <div className="charts">
            <Columns title="E-mails per day" items={data.per_day.map((d) => ({ label: d.day.slice(5), value: d.emails }))} />
            <BarList title="Why e-mails stopped" items={data.stop_reasons.slice(0, 8).map((r) => ({ label: shortReason(r.code), value: r.count }))} />
            <BarList
              title="Corrections by field"
              items={data.corrections.map((c) => ({ label: fieldLabel(c.field.replace(/^(licence|invoice)\./, "")), value: c.count }))}
            />
          </div>
          <p className="muted">Model cost over the period: ${data.cost_usd.toFixed(4)}.</p>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------- automation level

function AutomationPage() {
  const { data, error, reload } = useLoad<Automation>(owner.automation);
  const [value, setValue] = useState<number | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const threshold = value ?? data?.threshold ?? 0.45;

  const series = (pick: (p: Automation["tuning"] & object) => { x: number; y: number }[]): Series[] =>
    [
      data?.tuning ? { name: "Tuning set", color: TUNING, points: pick(data.tuning) } : null,
      data?.heldout ? { name: "Held-out set", color: HELDOUT, points: pick(data.heldout) } : null,
    ].filter((s): s is Series => s !== null);

  const at = (evaluation: Automation["heldout"]) =>
    evaluation?.curve.reduce((best, p) => (Math.abs(p.threshold - threshold) < Math.abs(best.threshold - threshold) ? p : best));

  const save = async () => {
    await owner.setThreshold(threshold);
    setSaved(`Saved. E-mails from now on need every field at ${pct(threshold)} or more to run without a person.`);
    reload();
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Automation level</h1>
          <p>How sure every field must be before an e-mail runs without a person. Measured on the labelled set.</p>
        </div>
      </div>
      {error && <p className="error">Could not load the measurements: {error}</p>}
      {data && (
        <>
          <div className="threshold-control panel">
            <label htmlFor="threshold">Confidence needed</label>
            <input
              id="threshold"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={threshold}
              onChange={(e) => {
                setValue(Number(e.target.value));
                setSaved(null);
              }}
            />
            <strong>{pct(threshold)}</strong>
            <button className="button primary" onClick={() => void save()} disabled={threshold === data.threshold}>
              Save level
            </button>
            {data.recommended && (
              <span className="muted">
                Recommended {pct(data.recommended.threshold)}: the lowest level with at most {data.recommended.target_errors_per_100_automatic} wrong
                automatic e-mail per 100 on the tuning set, plus a margin.
              </span>
            )}
          </div>
          {saved && <p className="saved">{saved}</p>}
          <div className="charts two">
            <LineChart
              title="Share of all e-mails handled without a person"
              series={series((e) => e.curve.map((p) => ({ x: p.threshold, y: p.straight_through_rate })))}
              marker={threshold}
              markerLabel="selected"
              yMax={1}
              yFormat={pct}
              xFormat={pct}
              xLabel="Confidence needed"
            />
            <LineChart
              title="Wrong automatic e-mails per 100 automatic"
              series={series((e) => e.curve.map((p) => ({ x: p.threshold, y: p.errors_per_100_automatic })))}
              marker={threshold}
              markerLabel="selected"
              yMax={10}
              yFormat={(v) => v.toFixed(v < 1 && v > 0 ? 1 : 0)}
              xFormat={pct}
              xLabel="Confidence needed"
            />
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>At {pct(threshold)}</th>
                <th>E-mails</th>
                <th>Handled without a person</th>
                <th>Of those that could be</th>
                <th>Wrong automatic e-mails</th>
              </tr>
            </thead>
            <tbody>
              {(["tuning", "heldout"] as const).map((name) => {
                const evaluation = data[name];
                const point = at(evaluation);
                if (!evaluation || !point) return null;
                return (
                  <tr key={name}>
                    <td>{name === "tuning" ? "Tuning set (used to choose)" : "Held-out set (never tuned on)"}</td>
                    <td>{evaluation.emails}</td>
                    <td>
                      {point.automatic} ({pct(point.straight_through_rate)})
                    </td>
                    <td>{pct(point.straight_through_of_clean)}</td>
                    <td>
                      {point.erroneous_automatic} ({point.errors_per_100_automatic} per 100)
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted">
            The curves are flat between the steps because confidence is built from a few checks (format, quote found in the
            e-mail, code reads the same value, second reading agrees), each scored on a short scale.
          </p>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------ rules

function RulesPage() {
  const { data, error, reload } = useLoad<RuleDef[]>(owner.rules);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<RuleDef | null>(null);
  const [test, setTest] = useState<RuleTest | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const editable = data?.filter((r) => r.kind === "check") ?? [];

  useEffect(() => {
    if (!data || selected) return;
    setSelected(data.find((r) => r.id === "price_matches_tariff")?.id ?? data[0]?.id ?? null);
  }, [data, selected]);

  useEffect(() => {
    const rule = data?.find((r) => r.id === selected);
    setDraft(rule ? { ...rule } : null);
    setTest(null);
    setMessage(null);
  }, [selected, data]);

  const run = async () => {
    if (!draft) return;
    setMessage(null);
    try {
      setTest(await owner.testRule(draft));
    } catch (e) {
      setMessage((e as Error).message);
      setTest(null);
    }
  };

  const save = async () => {
    if (!draft) return;
    try {
      await owner.saveRule(draft);
      setMessage("Saved. It applies to e-mails processed from now on.");
      reload();
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Rules</h1>
          <p>Checks every e-mail must pass to run without a person. Test a change on past e-mails before saving it.</p>
        </div>
      </div>
      {error && <p className="error">Could not load the rules: {error}</p>}
      <div className="rules-layout">
        <ul className="panel rule-list">
          {editable.map((rule) => (
            <li key={rule.id}>
              <button className={rule.id === selected ? "active" : ""} onClick={() => setSelected(rule.id)}>
                <strong>{rule.name}</strong>
                <span>{rule.enabled ? rule.description : "Switched off"}</span>
              </button>
            </li>
          ))}
        </ul>
        {draft && (
          <section className="panel rule-editor">
            <h2>{draft.name}</h2>
            <p className="muted">{draft.description}</p>
            <label>
              Check (must be true)
              <textarea value={draft.check ?? ""} onChange={(e) => setDraft({ ...draft, check: e.target.value })} spellCheck={false} rows={3} />
            </label>
            <label>
              Only when (optional)
              <input value={draft.when ?? ""} onChange={(e) => setDraft({ ...draft, when: e.target.value || null })} spellCheck={false} />
            </label>
            <label>
              Message shown in review
              <input value={draft.message} onChange={(e) => setDraft({ ...draft, message: e.target.value })} />
            </label>
            <label className="inline">
              <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> Rule is on
            </label>
            <div className="row">
              <button className="button" onClick={() => void run()}>
                Test on past e-mails
              </button>
              <button className="button primary" onClick={() => void save()}>
                Save rule
              </button>
              {message && <span className="muted">{message}</span>}
            </div>
            {test && (
              <div className="test-result">
                <p>
                  Checked {test.checked} past e-mails of this kind. It stops <strong>{test.would_stop}</strong> (today: {test.stops_now}).
                </p>
                {test.newly_stopped.length > 0 && (
                  <>
                    <h3>Would now stop</h3>
                    <ul>
                      {test.newly_stopped.map((r) => (
                        <li key={r.case_id}>
                          <a href={`/cases/${r.case_id}`}>{r.subject || "(no subject)"}</a> from {r.from}: {r.message}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {test.no_longer_stopped.length > 0 && (
                  <>
                    <h3>Would no longer stop</h3>
                    <ul>
                      {test.no_longer_stopped.map((r) => (
                        <li key={r.case_id}>
                          <a href={`/cases/${r.case_id}`}>{r.subject || "(no subject)"}</a> from {r.from}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {test.newly_stopped.length === 0 && test.no_longer_stopped.length === 0 && <p className="muted">No past e-mail changes outcome.</p>}
              </div>
            )}
            <details className="muted">
              <summary>What a check can use</summary>
              Facts such as quoted_total, tariff_total, price_tolerance, pickup_at, return_at, received_at, rental_days, conflicts,
              licence.expires_on, licence.classes, holder_name_similarity, subtotal, tax, total, lines_total; functions days(),
              hours(), present(), abs(), len(), years_between(), date_of(), decimal(); and, or, not, comparisons.
            </details>
          </section>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- templates

function TemplatesPage() {
  const [kind, setKind] = useState<"invoice" | "contract">("invoice");
  const [source, setSource] = useState("");
  const [customised, setCustomised] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    owner
      .template(kind)
      .then((t) => {
        setSource(t.source);
        setCustomised(t.customised);
      })
      .catch((e: Error) => setMessage(e.message));
  }, [kind]);
  useEffect(load, [load]);

  const preview = useCallback(async (html: string) => {
    try {
      const blob = await owner.previewTemplate(kind, html);
      setUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
      setMessage(null);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }, [kind]);

  useEffect(() => {
    if (source) void preview(source);
    // preview the stored version when the kind changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, source === ""]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Templates</h1>
          <p>Contract and invoice layout. Amounts, dates and numbers are filled in by the system; the preview uses the latest real booking.</p>
        </div>
      </div>
      <div className="row tabs-row" role="tablist">
        {(["invoice", "contract"] as const).map((k) => (
          <button key={k} className="tab" role="tab" aria-selected={kind === k} onClick={() => setKind(k)}>
            {k === "invoice" ? "Invoice" : "Rental agreement"}
          </button>
        ))}
        {customised && <span className="chip neutral">Edited</span>}
      </div>
      <div className="template-layout">
        <section className="panel">
          <textarea className="code" value={source} onChange={(e) => setSource(e.target.value)} spellCheck={false} aria-label="Template HTML" />
          <div className="row" style={{ padding: 12 }}>
            <button className="button" onClick={() => void preview(source)}>
              Preview
            </button>
            <button
              className="button primary"
              onClick={() =>
                owner
                  .saveTemplate(kind, source)
                  .then(() => {
                    setMessage("Saved. Documents issued from now on use this template.");
                    setCustomised(true);
                  })
                  .catch((e: Error) => setMessage(e.message))
              }
            >
              Save template
            </button>
            {customised && (
              <button className="button danger" onClick={() => owner.resetTemplate(kind).then(load)}>
                Restore the original
              </button>
            )}
            {message && <span className="muted">{message}</span>}
          </div>
        </section>
        <section className="panel preview">{url ? <img className="page-image" alt="Template preview, first page" src={url} /> : <p className="note">Rendering…</p>}</section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------- records

const RECORD_TABS = { bookings: "Bookings", cars: "Cars", payables: "Supplier invoices", audit: "Audit trail" } as const;
type RecordKind = keyof typeof RECORD_TABS;

function RecordsPage() {
  const [kind, setKind] = useState<RecordKind>("bookings");
  const load = useMemo(() => () => owner.records(kind), [kind]);
  const { data, error } = useLoad(load);
  const columns = data && data.length ? Object.keys(data[0]!) : [];
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Records</h1>
          <p>Everything the inbox created, and who or what did each action.</p>
        </div>
        <a className="button" href="/api/export/bookings.csv">
          Download bookings sheet
        </a>
      </div>
      <div className="row tabs-row" role="tablist">
        {(Object.keys(RECORD_TABS) as RecordKind[]).map((k) => (
          <button key={k} className="tab" role="tab" aria-selected={kind === k} onClick={() => setKind(k)}>
            {RECORD_TABS[k]}
          </button>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      {data && data.length === 0 && <p className="queue empty">Nothing recorded yet.</p>}
      {data && data.length > 0 && (
        <div className="table-wrap panel">
          <table className="table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c}>{c.replace(/_/g, " ")}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td key={c}>{format(row[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function format(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("; ");
  const text = String(value);
  return /^\d{4}-\d{2}-\d{2}T/.test(text) ? new Date(text).toLocaleString("en-GB") : text;
}
