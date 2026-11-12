import { useEffect, useState } from "react";

interface Message {
  id: string;
  from_address: string;
  subject: string;
  received_at: string;
  attachments: { id: string; filename: string }[];
}

export function App() {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/messages")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setMessages)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 760, margin: "48px auto", padding: "0 16px" }}>
      <h1>Ops Inbox</h1>
      {error && <p role="alert">Could not load messages: {error}</p>}
      {!error && messages === null && <p>Loading…</p>}
      {messages?.length === 0 && <p>No messages yet. Send one to bookings@larkspur.example.</p>}
      <ul>
        {messages?.map((m) => (
          <li key={m.id}>
            <strong>{m.subject || "(no subject)"}</strong> — {m.from_address}
            {m.attachments.length > 0 && ` · ${m.attachments.length} attachment(s)`}
          </li>
        ))}
      </ul>
    </main>
  );
}
