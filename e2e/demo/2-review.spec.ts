import { expect, test } from "@playwright/test";
import { caption, caseForLabel, glideClick, installCursor, typeSlowly } from "../helpers";

// About 60 s: an e-mail stops because the licence name was read uncertainly; the person sees
// the evidence on the licence, fixes one value and approves. A cut of this is the README GIF.
test("review", async ({ page }) => {
  await installCursor(page);
  const target = await caseForLabel(page, "m007");
  expect(target?.status).toBe("review");
  await page.goto("/review");
  await caption(page, "E-mails that need a person wait here, soonest pick-up first.");
  await page.waitForTimeout(2500);
  await glideClick(page, page.locator(".queue-row", { hasText: target!.from_address }).first());
  await expect(page.getByRole("heading", { name: "Why did this stop?" })).toBeVisible();
  await caption(page, "It says why it stopped: two readings of the licence disagree on the name.");
  await page.waitForTimeout(3000);

  const holder = page.locator(".field", { hasText: "Holder" });
  await caption(page, "Click the field to see where it was read.");
  await glideClick(page, holder);
  await page.waitForTimeout(3000);
  await caption(page, "The scan says MARROWBY. Fix the value.");
  await holder.dblclick();
  await page.keyboard.press("Control+A");
  await typeSlowly(page, "Elsie Marrowby");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Every check passes with your corrections" })).toBeVisible();
  await caption(page, "The rules run again at once: every check passes.");
  await page.waitForTimeout(2500);

  await caption(page, "Preview the contract before approving.");
  await glideClick(page, page.getByRole("tab", { name: "Contract preview" }));
  await page.locator(".preview .page-image").waitFor();
  await page.locator(".preview .page-image").scrollIntoViewIfNeeded();
  await page.waitForTimeout(2500);

  await caption(page, "Approve: the booking is created and the documents issued.");
  await glideClick(page, page.getByRole("button", { name: /^Approve/ }));
  await expect(page.locator(".status", { hasText: "Reply ready" })).toBeVisible({ timeout: 60_000 });
  await page.locator(".docs").scrollIntoViewIfNeeded();
  await page.waitForTimeout(3000);
  await caption(page, "The correction is stored, so the dashboard shows which fields need help.");
  await page.waitForTimeout(2500);
  await caption(page, "");
});
