import { expect, test } from "@playwright/test";
import { caption, glideClick, installCursor, sendMail, waitForCase } from "../helpers";

// About 60 s: a booking request with a scanned licence goes through without a person,
// and the reply goes out after one click.
test("straight through", async ({ page }) => {
  await installCursor(page);
  await page.goto("/ready");
  await caption(page, "A customer e-mails a booking request with a scanned driving licence.");
  sendMail("m008");
  await page.waitForTimeout(2500);
  await caption(page, "The inbox reads it, checks it against the price list, the fleet and the licence…");
  const ready = await waitForCase(page, (c) => c.message_id.startsWith("<m008."), ["ready"]);
  await page.reload();
  await expect(page.locator(".queue-row").first()).toBeVisible();
  await page.waitForTimeout(1200);
  await caption(page, "…and books it. Every field was sure enough and every rule passed.");
  await glideClick(page, page.locator(".queue-row", { hasText: ready.subject }).first());
  await expect(page.locator(".status", { hasText: "Reply ready" })).toBeVisible();
  await page.waitForTimeout(1500);

  await caption(page, "Each value comes with the words it was read from.");
  await glideClick(page, page.locator(".field", { hasText: "Pick-up date" }));
  await page.waitForTimeout(1600);
  await glideClick(page, page.locator(".field", { hasText: "Car" }).first());
  await page.waitForTimeout(1400);
  await caption(page, "Licence fields point to the place on the scan.");
  await glideClick(page, page.locator(".field", { hasText: "Valid until" }));
  await page.waitForTimeout(2200);

  await caption(page, "Contract and invoice are issued; numbers, totals and dates are computed by code.");
  await glideClick(page, page.getByRole("tab", { name: "Invoice" }));
  await page.locator(".preview .page-image").waitFor();
  await page.locator(".preview .page-image").scrollIntoViewIfNeeded();
  await page.waitForTimeout(3000);
  await glideClick(page, page.getByRole("tab", { name: "Reply" }));
  await page.locator(".preview textarea").scrollIntoViewIfNeeded();
  await caption(page, "The reply is drafted in the company's tone. Nothing is sent without a person.");
  await page.waitForTimeout(3000);

  await caption(page, "One click sends it, with the documents attached.");
  await glideClick(page, page.getByRole("button", { name: /Send reply/ }));
  await expect(page.locator(".status", { hasText: "Done" })).toBeVisible({ timeout: 60_000 });
  await caption(page, "Sent. The booking, the documents and every step are in the audit trail.");
  await page.locator('[aria-label="History"]').scrollIntoViewIfNeeded();
  await page.waitForTimeout(3500);
  await caption(page, "");
});
