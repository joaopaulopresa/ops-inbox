import { useEffect, useRef } from "react";
import { pageUrl, type Box, type CaseDetail } from "../../api";
import { received } from "../../format";
import type { FieldRow } from "./fields";
import { spanFor } from "./fields";

interface Props {
  detail: CaseDetail;
  tab: string; // "email" or an attachment id
  onTab: (tab: string) => void;
  active: FieldRow | undefined;
}

const ENGINES: Record<string, string> = {
  vision: "Read by the vision model; boxes come from local OCR.",
  "pdf-text": "Text taken from the PDF itself; boxes come from local OCR of the page.",
  tesseract: "Read by local OCR.",
  unreadable: "This file could not be read.",
};

export function SourcePane({ detail, tab, onTab, active }: Props) {
  const message = detail.message;
  const markRef = useRef<HTMLElement | null>(null);
  const span = spanFor(active, message.body);
  const box = active?.field?.box && active.field.source === tab ? active.field.box : null;

  useEffect(() => {
    markRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [active?.key, tab]);

  const body = message.body;
  return (
    <section className="panel source" aria-label="Source">
      <header role="tablist">
        <button className="tab" role="tab" aria-selected={tab === "email"} onClick={() => onTab("email")}>
          E-mail
        </button>
        {message.attachments.map((attachment) => (
          <button
            key={attachment.id}
            className="tab"
            role="tab"
            aria-selected={tab === attachment.id}
            onClick={() => onTab(attachment.id)}
          >
            {attachment.filename}
          </button>
        ))}
      </header>
      <div className="source-body">
        {tab === "email" ? (
          <>
            <div className="mail-head">
              <span>From</span>
              <b>
                {message.from_name} &lt;{message.from_address}&gt;
              </b>
              {message.reply_to && (
                <>
                  <span>Reply to</span>
                  <b>{message.reply_to}</b>
                </>
              )}
              <span>Subject</span>
              <b>{message.subject}</b>
              <span>Sent</span>
              <b>{message.sent_at ? received(message.sent_at) : "unknown"}</b>
            </div>
            <div className="mail-text">
              {span ? (
                <>
                  {body.slice(0, span[0])}
                  <mark key={active?.key} ref={markRef}>
                    {body.slice(span[0], span[1])}
                  </mark>
                  {body.slice(span[1])}
                </>
              ) : (
                body || "(empty message)"
              )}
            </div>
          </>
        ) : (
          <Attachment detail={detail} id={tab} box={box} />
        )}
      </div>
    </section>
  );
}

function Attachment({ detail, id, box }: { detail: CaseDetail; id: string; box: Box | null }) {
  const attachment = detail.message.attachments.find((a) => a.id === id);
  const read = detail.documents_read.find((d) => d.attachment_id === id);
  if (!attachment) return null;
  const readable = attachment.content_type.startsWith("image/") || attachment.content_type === "application/pdf";
  if (!readable) {
    return (
      <p className="engine-note">
        {attachment.filename} ({attachment.content_type}) is kept with the case but not read.
      </p>
    );
  }
  return (
    <div>
      {Array.from({ length: attachment.pages }, (_, page) => (
        <div className="scan" key={page}>
          <img src={pageUrl(id, page)} alt={`${attachment.filename}, page ${page + 1}`} />
          {box && box.page === page && (
            <span
              className="box"
              key={`${box.x0}-${box.y0}`}
              style={{
                left: `${box.x0 * 100}%`,
                top: `${box.y0 * 100}%`,
                width: `${(box.x1 - box.x0) * 100}%`,
                height: `${(box.y1 - box.y0) * 100}%`,
              }}
            />
          )}
        </div>
      ))}
      <p className="engine-note">{read ? ENGINES[read.engine] ?? read.engine : "Not read: not needed for this kind of e-mail."}</p>
    </div>
  );
}
