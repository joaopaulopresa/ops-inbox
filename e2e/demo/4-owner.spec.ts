import { expect, test } from "@playwright/test";
import { caption, glideClick, installCursor, typeSlowly } from "../helpers";

// About 60 s: the owner's view: measured automation, a rule tested on past e-mails, a template.
test("owner", async ({ page }) => {
  await installCursor(page);
  await page.goto("/owner/dashboard");
  await caption(page, "The owner sees throughput, how much runs without a person, and why the rest stopped.");
  await page.waitForTimeout(4000);

  await glideClick(page, page.getByRole("link", { name: "Automation level" }));
  await expect(page.getByText("Share of all e-mails handled without a person")).toBeVisible();
  await caption(page, "How sure every field must be, measured on 300 labelled e-mails: 200 to tune, 100 held out.");
  await page.waitForTimeout(3500);
  const slider = page.locator("#threshold");
  await slider.scrollIntoViewIfNeeded();
  await caption(page, "Lower the bar and more runs alone, but wrong automatic e-mails appear.");
  for (const value of ["0.35", "0.25", "0.15"]) {
    await slider.fill(value);
    await page.waitForTimeout(900);
  }
  await page.locator(".charts").hover();
  await page.waitForTimeout(2500);
  await slider.fill("0.45");
  await page.waitForTimeout(800);

  await glideClick(page, page.getByRole("link", { name: "Rules" }));
  await glideClick(page, page.getByRole("button", { name: /Quoted price matches the price list/ }));
  await caption(page, "Rules are data the owner edits. Test a change on past e-mails before saving it.");
  const check = page.locator(".rule-editor textarea").first();
  await check.click();
  await page.keyboard.press("Control+A");
  await typeSlowly(page, "abs(quoted_total - tariff_total) <= decimal('0.10')");
  await glideClick(page, page.getByRole("button", { name: "Test on past e-mails" }));
  await expect(page.getByText(/Checked \d+ past e-mails/)).toBeVisible();
  await page.waitForTimeout(3500);

  await glideClick(page, page.getByRole("link", { name: "Templates" }));
  await caption(page, "Templates are HTML; the preview uses the latest real booking.");
  await page.locator(".template-layout .page-image").waitFor();
  const editor = page.locator("textarea.code");
  const source = await editor.inputValue();
  await editor.fill(source.replace("<h1>Invoice</h1>", "<h1>Invoice: thank you for choosing Larkspur</h1>"));
  await glideClick(page, page.getByRole("button", { name: "Preview" }));
  await page.waitForTimeout(3500);
  await caption(page, "");
});
