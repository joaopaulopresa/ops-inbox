import { expect, test } from "@playwright/test";
import { api, caseForLabel, sendMail, waitForCase } from "../helpers";

// Preconditions: `make up && make seed`, and the worker has processed the demo mailbox.

test.describe.configure({ mode: "serial" });

test("the review queue explains in plain words why each e-mail stopped", async ({ page }) => {
  await page.goto("/review");
  await expect(page.getByRole("heading", { name: "To review" })).toBeVisible();
  await expect(page.getByText("Licence expires").first()).toBeVisible();
  await expect(page.getByText("Tries to steer the process").first()).toBeVisible();
  await expect(page.getByText("Car already booked").first()).toBeVisible();
});

test("clicking a field shows its evidence in the e-mail and on the scan", async ({ page }) => {
  const target = await caseForLabel(page, "m146"); // licence expires before the return
  expect(target).toBeDefined();
  await page.goto(`/cases/${target!.id}`);
  await expect(page.getByRole("heading", { name: "Why did this stop?" })).toBeVisible();

  await page.locator(".field", { hasText: "Pick-up date" }).click();
  await expect(page.locator(".mail-text mark")).toBeVisible();

  await page.locator(".field", { hasText: "Valid until" }).click();
  await expect(page.locator(".scan .box")).toBeVisible();
});

test("a person fixes a misread value, approves, and the booking, documents and reply exist", async ({ page }) => {
  const target = await caseForLabel(page, "m007"); // the licence holder's name was read short
  expect(target?.status).toBe("review");
  await page.goto(`/cases/${target!.id}`);
  await expect(page.getByText(/Not sure about the licence holder name/)).toBeVisible();

  const holder = page.locator(".field", { hasText: "Holder" });
  await holder.click();
  await expect(page.locator(".scan .box")).toBeVisible();
  await holder.dblclick();
  const input = holder.locator("input");
  await input.fill("Elsie Marrowby");
  await input.press("Enter");
  await expect(page.getByRole("heading", { name: "Every check passes with your corrections" })).toBeVisible();

  await page.getByRole("button", { name: /^Approve/ }).click();
  await expect(page.locator(".status", { hasText: "Reply ready" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("link", { name: /Contract LCH-C-/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Invoice LCH-INV-/ })).toBeVisible();
  const detail = await api<{ corrections: { field: string; new: string }[]; booking: { reference: string } }>(page, `/api/cases/${target!.id}`);
  expect(detail.corrections).toEqual([expect.objectContaining({ field: "licence.holder_name", new: "Elsie Marrowby" })]);
  expect(detail.booking.reference).toMatch(/^LCH-B-2027-/);

  await page.getByRole("button", { name: /Send reply/ }).click();
  await expect(page.locator(".status", { hasText: "Done" })).toBeVisible({ timeout: 60_000 });
});

test("an e-mail that tries to instruct the system lands in review, flagged", async ({ page }) => {
  sendMail("m284"); // an instruction printed on the licence scan
  const flagged = await waitForCase(page, (c) => c.message_id.startsWith("<m284."), ["review"]);
  expect(flagged.reasons.map((r) => r.code)).toContain("suspicious_content");
  await page.goto(`/cases/${flagged.id}`);
  await expect(page.getByText(/An attachment tries to bypass the process|Gives orders to the system/).first()).toBeVisible();
  await page.getByRole("button", { name: /^Reject/ }).click();
  await page.getByRole("dialog").locator("textarea").fill("Instruction printed on the licence.");
  await page.getByRole("dialog").getByRole("button", { name: "Reject" }).click();
  await expect(page.locator(".status", { hasText: "Rejected" })).toBeVisible({ timeout: 60_000 });
});

test("the owner sees the measured curve and can test a rule on past e-mails", async ({ page }) => {
  await page.goto("/owner/thresholds");
  await expect(page.getByText("Share of all e-mails handled without a person")).toBeVisible();
  await expect(page.getByText("Held-out set (never tuned on)")).toBeVisible();

  await page.goto("/owner/rules");
  await page.getByRole("button", { name: /Quoted price matches the price list/ }).click();
  await page.getByRole("button", { name: "Test on past e-mails" }).click();
  await expect(page.getByText(/Checked \d+ past e-mails/)).toBeVisible();
});
