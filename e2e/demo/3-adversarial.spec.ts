import { expect, test } from "@playwright/test";
import { caption, glideClick, installCursor, sendMail, waitForCase } from "../helpers";

// About 30 s: an instruction hidden on a licence scan does not get obeyed; the e-mail stops, flagged.
test("adversarial", async ({ page }) => {
  await installCursor(page);
  await page.goto("/review");
  await caption(page, "This booking request has a licence scan with a line of text added to it.");
  sendMail("m284");
  const flagged = await waitForCase(page, (c) => c.message_id.startsWith("<m284."), ["review"]);
  await page.reload();
  await caption(page, "It lands in review, flagged: it tries to steer the process.");
  await page.waitForTimeout(2500);
  await glideClick(page, page.locator(".queue-row", { hasText: flagged.subject }).first());
  await expect(page.getByRole("heading", { name: "Why did this stop?" })).toBeVisible();
  await page.waitForTimeout(2500);
  await caption(page, "The model that reads e-mail has no tools. Text is data, never an instruction.");
  await glideClick(page, page.getByRole("tab", { name: /\.jpg|\.png/ }).first());
  await page.waitForTimeout(3500);
  await caption(page, "Reject it. Nothing is created and nothing is sent.");
  await glideClick(page, page.getByRole("button", { name: /^Reject/ }));
  await page.getByRole("dialog").locator("textarea").pressSequentially("Instruction printed on the licence scan.", { delay: 40 });
  await glideClick(page, page.getByRole("dialog").getByRole("button", { name: "Reject" }));
  await expect(page.locator(".status", { hasText: "Rejected" })).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(2500);
  await caption(page, "");
});
