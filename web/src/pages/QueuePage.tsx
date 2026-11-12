import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CaseSummary, type Status } from "../api";
import { categoryLabel, received, shortReason, urgency } from "../format";
import { useKeys } from "../useKeys";

const TITLES: Record<string, { title: string; lead: string; empty: string }> = {
  review: {
    title: "To review",
    lead: "E-mails that stopped for a person, soonest pick-up first.",
    empty: "Nothing to review. New e-mails appear here when a check needs you.",
  },
  ready: {
    title: "Ready to send",
    lead: "Bookings and changes are done; the reply waits for one click.",
    empty: "No replies waiting. Approved and automatic cases land here.",
  },
  processing: {
    title: "In progress",
    lead: "E-mails being read or resuming after a decision.",
    empty: "Nothing in progress.",
  },
  done: { title: "Done", lead: "Sent, recorded or archived.", empty: "Nothing finished yet." },
  rejected: { title: "Rejected", lead: "Stopped by a person; nothing was created or sent.", empty: "Nothing rejected." },
};

export function QueuePage({ status }: { status: Status }) {
  const [rows, setRows] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const navigate = useNavigate();
  const text = TITLES[status] ?? TITLES.review!;

  const load = useCallback(() => {
    api
      .cases(status)
      .then((data) => {
        setRows(data);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [status]);

  useEffect(() => {
    setRows(null);
    setSelected(0);
    load();
    const timer = window.setInterval(load, 4000);
    return () => window.clearInterval(timer);
  }, [load]);

  const open = (row: CaseSummary | undefined) => row && navigate(`/cases/${row.id}`, { state: { from: status } });

  useKeys({
    j: () => setSelected((i) => Math.min(i + 1, (rows?.length ?? 1) - 1)),
    ArrowDown: () => setSelected((i) => Math.min(i + 1, (rows?.length ?? 1) - 1)),
    k: () => setSelected((i) => Math.max(i - 1, 0)),
    ArrowUp: () => setSelected((i) => Math.max(i - 1, 0)),
    Enter: () => open(rows?.[selected]),
    o: () => open(rows?.[selected]),
  });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{text.title}</h1>
          <p>{text.lead}</p>
        </div>
      </div>
      {error && <p className="error">Could not load the queue: {error}. It retries every few seconds.</p>}
      {rows === null && !error && <p className="loading">Loading the queue…</p>}
      {rows !== null && rows.length === 0 && (
        <div className="queue empty">
          <strong>All clear</strong>
          {text.empty}
        </div>
      )}
      {rows !== null && rows.length > 0 && (
        <div className="queue" role="list">
          {rows.map((row, index) => {
            const when = urgency(row.urgent_at);
            const soon = /Today|Tomorrow|In [1-6] days/.test(when);
            return (
              <div
                key={row.id}
                role="listitem"
                className={`queue-row${index === selected ? " selected" : ""}`}
                onClick={() => open(row)}
                onMouseEnter={() => setSelected(index)}
              >
                <span className={`when${soon ? " soon" : ""}`} title="Pick-up">
                  {status === "review" ? when : received(row.received_at)}
                </span>
                <span className="who">
                  {row.from_name || row.from_address}
                  <small>{row.from_address}</small>
                </span>
                <span className="what">
                  {row.subject || "(no subject)"}
                  <small>{row.outcome ?? `Received ${received(row.received_at)}`}</small>
                </span>
                <span className="category">{categoryLabel(row.category)}</span>
                <span className="chips">
                  {row.reasons.slice(0, 3).map((reason) => (
                    <span
                      key={reason.code}
                      className={`chip${reason.code.startsWith("low_confidence") ? " low" : ""}`}
                    >
                      {shortReason(reason.code)}
                    </span>
                  ))}
                  {row.reasons.length > 3 && <span className="chip neutral">+{row.reasons.length - 3}</span>}
                  {row.booking_reference && <span className="chip neutral">{row.booking_reference}</span>}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
