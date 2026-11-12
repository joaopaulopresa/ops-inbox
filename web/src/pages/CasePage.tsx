import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, documentPageUrl, documentUrl, type CaseDetail, type Revalidation } from "../api";
import { carCodes, carName, categoryLabel, displayValue, fieldLabel, percent } from "../format";
import { useKeys } from "../useKeys";
import { buildRows, type FieldRow } from "./case/fields";
import { SourcePane } from "./case/SourcePane";

const GROUP_TITLES = { booking: "Request", licence: "Driving licence", invoice: "Supplier invoice" };
const PURPOSES = ["wedding", "film_shoot", "weekend_drive", "corporate_event", "other"];

type Corrections = Record<string, string | string[] | null>;

export function CasePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [corrections, setCorrections] = useState<Corrections>({});
  const [check, setCheck] = useState<Revalidation | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [editing, setEditing] = useState<string | null>(null);
  const [tab, setTab] = useState("email");
  const [preview, setPreview] = useState<"reply" | "contract" | "invoice">("reply");
  const [replyText, setReplyText] = useState("");
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [help, setHelp] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(0.8);

  useEffect(() => {
    fetch("/api/automation")
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { threshold?: number } | null) => data?.threshold !== undefined && setThreshold(data.threshold))
      .catch(() => undefined);
  }, []);

  const load = useCallback(() => {
    return api
      .case(id)
      .then((data) => {
        setDetail(data);
        setError(null);
        return data;
      })
      .catch((e: Error) => {
        setError(e.message);
        return null;
      });
  }, [id]);

  useEffect(() => {
    setDetail(null);
    setCorrections({});
    setCheck(null);
    setActiveIndex(0);
    setTab("email");
    setActionError(null);
    void load().then((data) => {
      if (data?.reply) setReplyText(data.reply.body);
      else setReplyText("");
    });
  }, [load]);

  const rows = useMemo(() => (detail ? buildRows(detail) : []), [detail]);
  const active = rows[activeIndex];
  const reviewing = detail?.status === "review";
  const question = detail?.category === "question";
  const bookingLike = detail?.category === "new_booking";

  // Re-validate whenever the person changes a value.
  useEffect(() => {
    if (!reviewing || Object.keys(corrections).length === 0) {
      setCheck(null);
      return;
    }
    const timer = window.setTimeout(() => {
      api.revalidate(id, corrections).then(setCheck).catch(() => setCheck(null));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [corrections, id, reviewing]);

  // Previews of what approval would produce (booking cases in review).
  useEffect(() => {
    if (!detail) return;
    let url: string | null = null;
    setPdfUrl(null);
    if (reviewing && bookingLike && preview !== "reply") {
      api
        .previewDocument(id, preview, corrections)
        .then((blob) => {
          url = URL.createObjectURL(blob);
          setPdfUrl(url);
        })
        .catch(() => setPdfUrl(null));
    }
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [preview, corrections, id, detail, reviewing, bookingLike]);

  // Follow the field's evidence to the attachment it came from.
  useEffect(() => {
    if (active?.field && active.field.source !== "email" && active.field.box) setTab(active.field.source);
    else if (active?.field?.source === "email") setTab("email");
  }, [active]);

  const reasons = check ? check.reasons : (detail?.reasons ?? []);
  const blocking = check ? check.blocking : [];
  const blockingCodes = new Set(blocking.map((b) => b.reason));

  const nextCase = useCallback(async () => {
    const queue = await api.cases("review");
    const next = queue.find((c) => c.id !== id);
    navigate(next ? `/cases/${next.id}` : "/review");
  }, [id, navigate]);

  const waitForResult = useCallback(async () => {
    for (let i = 0; i < 60; i += 1) {
      await new Promise((r) => window.setTimeout(r, 1000));
      const data = await load();
      if (data && data.status !== "processing") {
        if (data.reply) setReplyText(data.reply.body);
        return data;
      }
    }
    return null;
  }, [load]);

  const approve = async () => {
    if (!detail || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.approve(id, corrections, question ? replyText : undefined);
      setToast("Approved. Carrying it out…");
      const done = await waitForResult();
      setToast(done ? (done.outcome ?? "Done.") : "Still working; it will appear in Ready to send.");
      setCorrections({});
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const reject = async (note: string) => {
    setBusy(true);
    try {
      await api.reject(id, note);
      setRejecting(false);
      setToast("Rejected. Nothing was created or sent.");
      await waitForResult();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    if (!detail?.reply || busy) return;
    setBusy(true);
    try {
      if (replyText !== detail.reply.body) await api.editReply(id, replyText);
      await api.send(id);
      setToast("Sending the reply…");
      const done = await waitForResult();
      setToast(done?.status === "done" ? "Reply sent." : "Queued for sending.");
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useKeys(
    {
      j: () => setActiveIndex((i) => Math.min(i + 1, rows.length - 1)),
      ArrowDown: () => setActiveIndex((i) => Math.min(i + 1, rows.length - 1)),
      k: () => setActiveIndex((i) => Math.max(i - 1, 0)),
      ArrowUp: () => setActiveIndex((i) => Math.max(i - 1, 0)),
      e: () => reviewing && active && setEditing(active.key),
      Enter: () => reviewing && active && setEditing(active.key),
      a: () => reviewing && void approve(),
      r: () => reviewing && setRejecting(true),
      s: () => detail?.status === "ready" && void send(),
      n: () => void nextCase(),
      u: () => navigate(-1),
      "?": () => setHelp((h) => !h),
      Escape: () => {
        setHelp(false);
        setRejecting(false);
      },
    },
    !editing && !rejecting,
  );

  if (error) return <div className="page"><p className="error">Could not load this case: {error}</p></div>;
  if (!detail) return <div className="page"><p className="loading">Loading the case…</p></div>;

  const issued = (kind: string) => detail.documents.find((d) => d.kind === kind && d.status === "issued");
  const groups = (["booking", "licence", "invoice"] as const).filter((g) => rows.some((r) => r.group === g));
  const rules = check ? check.rules : (detail.state?.rules ?? []);
  const failed = rules.filter((r) => r.outcome === "failed" || r.outcome === "error");

  return (
    <div className="page">
      <div className="case-head">
        <div>
          <h1>{detail.message.subject || "(no subject)"}</h1>
          <p className="meta">
            {categoryLabel(detail.category)} from {detail.message.from_name || detail.message.from_address}
            {detail.booking && <> for booking {detail.booking.reference}</>}
          </p>
        </div>
        <span className={`status ${detail.status}`}>{statusLabel(detail.status)}</span>
      </div>

      {reviewing && (
        <div className={`why${reasons.length ? "" : " clear"}`}>
          <h2>{reasons.length ? "Why did this stop?" : "Every check passes with your corrections"}</h2>
          {reasons.length > 0 && (
            <ul>
              {reasons.map((reason) => (
                <li key={reason.code + reason.message} className={blockingCodes.has(reason.code) ? "blocking" : ""}>
                  {reason.message}
                  {blockingCodes.has(reason.code) && " Fix this before approving."}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!reviewing && detail.outcome && (
        <div className="why clear">
          <h2>{detail.outcome}</h2>
          {detail.decided_by && <p>Decided by {detail.decided_by}.</p>}
        </div>
      )}

      <div className="workspace">
        <SourcePane detail={detail} tab={tab} onTab={setTab} active={active} />

        <div>
          {rows.length > 0 && (
            <section className="panel fields" aria-label="Extracted fields">
              {groups.map((group) => (
                <section key={group}>
                  <h2>{GROUP_TITLES[group]}</h2>
                  {rows.map((row, index) =>
                    row.group !== group ? null : (
                      <FieldLine
                        key={row.key}
                        row={row}
                        threshold={threshold}
                        active={index === activeIndex}
                        editing={editing === row.key}
                        corrected={corrections[row.key]}
                        onSelect={() => setActiveIndex(index)}
                        onEdit={() => reviewing && setEditing(row.key)}
                        onSave={(value) => {
                          setCorrections((c) => ({ ...c, [row.key]: value }));
                          setEditing(null);
                        }}
                        onCancel={() => setEditing(null)}
                      />
                    ),
                  )}
                </section>
              ))}
              <section>
                <h2>Checks</h2>
                <ul className="rules">
                  {failed.map((r) => (
                    <li key={r.rule_id + r.reason} className="failed">
                      <span aria-hidden="true">✕</span>
                      <span>{r.name}{r.message ? `: ${r.message}` : ""}</span>
                    </li>
                  ))}
                  {rules
                    .filter((r) => r.outcome === "passed")
                    .map((r) => (
                      <li key={r.rule_id + r.reason}>
                        <span aria-hidden="true">✓</span>
                        <span>{r.name}</span>
                      </li>
                    ))}
                </ul>
              </section>
            </section>
          )}

          <section className="panel preview" aria-label="What goes out">
            <header role="tablist">
              <button className="tab" role="tab" aria-selected={preview === "reply"} onClick={() => setPreview("reply")}>
                Reply
              </button>
              {((bookingLike && reviewing) || detail.documents.length > 0) && (
                <>
                  <button className="tab" role="tab" aria-selected={preview === "contract"} onClick={() => setPreview("contract")}>
                    {reviewing ? "Contract preview" : "Contract"}
                  </button>
                  <button className="tab" role="tab" aria-selected={preview === "invoice"} onClick={() => setPreview("invoice")}>
                    {reviewing ? "Invoice preview" : "Invoice"}
                  </button>
                </>
              )}
            </header>
            {preview === "reply" && (
              <ReplyPreview
                detail={detail}
                corrections={corrections}
                text={replyText}
                onText={setReplyText}
                editable={question ? reviewing : detail.reply?.status === "draft"}
              />
            )}
            {preview !== "reply" && reviewing && (pdfUrl ? <img className="page-image" alt={`${preview} preview, first page`} src={pdfUrl} /> : <p className="note">Preparing the preview…</p>)}
            {preview !== "reply" && !reviewing && issued(preview) && (
              <img className="page-image" alt={`${preview}, first page`} src={documentPageUrl(issued(preview)!.id)} />
            )}
            {detail.documents.length > 0 && (
              <div className="docs">
                {detail.documents.map((d) => (
                  <a key={d.id} className="button" href={documentUrl(d.id)} target="_blank" rel="noreferrer">
                    {d.kind === "contract" ? "Contract" : "Invoice"} {d.number}
                    {d.status === "void" ? " (void)" : ""}
                  </a>
                ))}
              </div>
            )}
          </section>

          {detail.audit.length > 0 && (
            <section className="panel preview" aria-label="History">
              <header>
                <strong>History</strong>
              </header>
              <ul className="audit">
                {detail.audit.map((entry, index) => (
                  <li key={index}>
                    {new Date(entry.at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}{" "}
                    {entry.actor}: {entry.action.replace(".", " ")}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>

      <div className="actions">
        <Link className="button" to={detail.status === "ready" ? "/ready" : "/review"}>
          Back <kbd>u</kbd>
        </Link>
        {actionError && <span className="message">{actionError}</span>}
        <span className="spacer" />
        {reviewing && (
          <>
            <button className="button danger" onClick={() => setRejecting(true)} disabled={busy}>
              Reject <kbd>r</kbd>
            </button>
            <button className="button primary" onClick={() => void approve()} disabled={busy || blocking.length > 0 || (question && !replyText.trim())}>
              {question ? "Approve answer" : "Approve"} <kbd>a</kbd>
            </button>
          </>
        )}
        {detail.status === "ready" && detail.reply && (
          <button className="button primary" onClick={() => void send()} disabled={busy}>
            Send reply <kbd>s</kbd>
          </button>
        )}
        <button className="button" onClick={() => void nextCase()}>
          Next <kbd>n</kbd>
        </button>
      </div>

      {rejecting && <RejectDialog onCancel={() => setRejecting(false)} onReject={(note) => void reject(note)} />}
      {help && <Shortcuts onClose={() => setHelp(false)} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

function statusLabel(status: string) {
  return (
    { review: "Waiting for review", ready: "Reply ready", processing: "Working", done: "Done", rejected: "Rejected", failed: "Failed" }[status] ??
    status
  );
}

interface LineProps {
  row: FieldRow;
  threshold: number;
  active: boolean;
  editing: boolean;
  corrected: string | string[] | null | undefined;
  onSelect: () => void;
  onEdit: () => void;
  onSave: (value: string | string[] | null) => void;
  onCancel: () => void;
}

function FieldLine({ row, threshold, active, editing, corrected, onSelect, onEdit, onSave, onCancel }: LineProps) {
  const ref = useRef<HTMLDivElement>(null);
  const isCorrected = corrected !== undefined || row.field?.corrected;
  const low = !isCorrected && row.confidence < threshold;
  const shown = corrected !== undefined ? (Array.isArray(corrected) ? corrected.join(", ") : corrected) : row.value;

  useEffect(() => {
    if (active) ref.current?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <div
      ref={ref}
      className={`field${active ? " active" : ""}${low ? " low" : ""}${isCorrected ? " corrected" : ""}`}
      onClick={onSelect}
      onDoubleClick={onEdit}
      aria-current={active}
    >
      <span className="label">{fieldLabel(row.name)}</span>
      {editing ? (
        <Editor row={row} initial={shown ?? ""} onSave={onSave} onCancel={onCancel} />
      ) : (
        <span className={`value${shown ? "" : " none"}`}>
          {shown ? (row.name === "extras" ? shown.replace(/_/g, " ") : displayValue(row.name, shown)) : "not stated"}
          {corrected !== undefined && row.value !== shown && (
            <span className="was">{row.value ? displayValue(row.name, row.value) : "empty"}</span>
          )}
        </span>
      )}
      <span className="confidence" title={signalsText(row)}>
        {isCorrected ? "fixed" : low ? `low ${percent(row.confidence)}` : percent(row.confidence)}
      </span>
    </div>
  );
}

function signalsText(row: FieldRow) {
  const signals = row.field?.signals ?? {};
  const names: Record<string, string> = {
    format: "format",
    evidence: "quote found in source",
    support: "code reads the same value",
    agreement: "second reading agrees",
    present: "present",
    derived_from_location: "derived from the location",
    person: "set by a person",
  };
  return Object.entries(signals)
    .map(([k, v]) => `${names[k] ?? k}: ${percent(v)}`)
    .join("\n");
}

function Editor({ row, initial, onSave, onCancel }: { row: FieldRow; initial: string; onSave: (v: string | string[] | null) => void; onCancel: () => void }) {
  const [value, setValue] = useState(initial);
  const input = useRef<HTMLInputElement & HTMLSelectElement>(null);
  useEffect(() => input.current?.focus(), []);
  const save = () => {
    if (row.name === "extras") onSave(value.split(",").map((v) => v.trim().replace(/ /g, "_")).filter(Boolean));
    else onSave(value.trim() === "" ? null : value.trim());
  };
  const keys = (event: React.KeyboardEvent) => {
    if (event.key === "Enter") save();
    if (event.key === "Escape") onCancel();
  };
  const date = /_date$|^expires_on$|^date_of_birth$/.test(row.name);
  const time = /_time$/.test(row.name);
  if (row.name === "car" || row.name === "purpose") {
    const options = row.name === "car" ? carCodes : PURPOSES;
    return (
      <select ref={input} value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={keys} onBlur={save} aria-label={fieldLabel(row.name)}>
        <option value="">not stated</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {row.name === "car" ? carName(o) : o.replace(/_/g, " ")}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      ref={input}
      type={date ? "date" : time ? "time" : "text"}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={keys}
      onBlur={save}
      aria-label={fieldLabel(row.name)}
    />
  );
}

function ReplyPreview({ detail, corrections, text, onText, editable }: { detail: CaseDetail; corrections: Corrections; text: string; onText: (t: string) => void; editable: boolean }) {
  const [drafted, setDrafted] = useState<string | null>(null);
  const reviewing = detail.status === "review";
  const booking = detail.category === "new_booking";

  useEffect(() => {
    setDrafted(null);
    if (!reviewing || !booking) return;
    const timer = window.setTimeout(() => {
      api
        .previewReply(detail.id, corrections)
        .then((r) => setDrafted(r.body))
        .catch(() => setDrafted(null));
    }, 400);
    return () => window.clearTimeout(timer);
  }, [detail.id, corrections, reviewing, booking]);

  if (detail.category === "question" && reviewing) {
    return (
      <textarea
        aria-label="Answer to the customer"
        placeholder="Write the answer. It is sent only after you approve and then send it."
        value={text}
        onChange={(e) => onText(e.target.value)}
      />
    );
  }
  if (detail.reply) {
    return editable ? (
      <textarea aria-label="Reply to the customer" value={text} onChange={(e) => onText(e.target.value)} />
    ) : (
      <p className="note" style={{ whiteSpace: "pre-wrap" }}>
        {detail.reply.body}
        {"\n\n"}
        <em>{detail.reply.status === "sent" ? "Sent." : "Approved for sending."}</em>
      </p>
    );
  }
  if (reviewing && booking) {
    return <p className="note" style={{ whiteSpace: "pre-wrap" }}>{drafted ?? "Drafting the reply that approval would send…"}</p>;
  }
  if (reviewing) return <p className="note">The reply is drafted when you approve.</p>;
  return <p className="note">No reply for this e-mail{detail.category === "supplier_invoice" ? ": supplier invoices are recorded, not answered" : ""}.</p>;
}

function RejectDialog({ onCancel, onReject }: { onCancel: () => void; onReject: (note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Reject">
      <div className="dialog">
        <h2>Reject this e-mail</h2>
        <p>Nothing is created and nothing is sent. Say why, for the history.</p>
        <textarea autoFocus value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Escape" && onCancel()} />
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="button" onClick={onCancel}>Keep it</button>
          <button className="button danger" onClick={() => onReject(note)}>Reject</button>
        </div>
      </div>
    </div>
  );
}

function Shortcuts({ onClose }: { onClose: () => void }) {
  const keys: [string, string][] = [
    ["j / k", "Next or previous field"],
    ["e", "Edit the field"],
    ["Enter", "Save the value"],
    ["Esc", "Cancel"],
    ["a", "Approve"],
    ["r", "Reject"],
    ["s", "Send the reply"],
    ["n", "Next e-mail"],
    ["u", "Back to the list"],
  ];
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" role="dialog" aria-label="Keyboard shortcuts">
        <h2>Keyboard shortcuts</h2>
        <dl>
          {keys.map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt><kbd>{k}</kbd></dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

