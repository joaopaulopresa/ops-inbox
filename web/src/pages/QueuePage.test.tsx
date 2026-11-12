import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueuePage } from "./QueuePage";

afterEach(() => vi.restoreAllMocks());

describe("QueuePage", () => {
  it("says the queue is clear when there is nothing to review", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
    );
    render(
      <MemoryRouter>
        <QueuePage status="review" />
      </MemoryRouter>,
    );
    expect(await screen.findByText("All clear")).toBeInTheDocument();
  });

  it("lists cases with their reasons in plain words", async () => {
    const rows = [
      {
        id: "c1", status: "review", category: "new_booking", route: "review",
        subject: "Booking request", from_name: "Ada Quill", from_address: "ada@x.example",
        received_at: "2026-11-12T10:00:00Z", urgent_at: null, outcome: null, booking_reference: null,
        reasons: [{ code: "licence_valid_for_rental", message: "The licence expires." }],
      },
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(rows), { status: 200, headers: { "content-type": "application/json" } }),
    );
    render(
      <MemoryRouter>
        <QueuePage status="review" />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Licence expires")).toBeInTheDocument();
    expect(screen.getByText("Ada Quill")).toBeInTheDocument();
  });
});
