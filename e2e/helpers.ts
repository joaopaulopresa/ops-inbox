import { execFileSync } from "node:child_process";
import { expect, type Locator, type Page } from "@playwright/test";

const ROOT = new URL("..", import.meta.url).pathname;

/** Deliver labelled e-mails to the local mail server, as a customer would. */
export function sendMail(...ids: string[]) {
  execFileSync("docker", ["compose", "exec", "-T", "api", "python", "-m", "opsinbox.seedgen.demo", "send", "--ids", ids.join(",")], {
    cwd: ROOT,
    stdio: "pipe",
  });
}

export async function api<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(path);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as T;
}

interface Summary {
  id: string;
  message_id: string;
  subject: string;
  from_address: string;
  status: string;
  reasons: { code: string }[];
}

/** Wait until the case of an e-mail (found by subject and sender) reaches one of the statuses. */
export async function waitForCase(page: Page, match: (c: Summary) => boolean, statuses: string[], timeout = 90_000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const all = await api<Summary[]>(page, "/api/cases?status=all");
    const found = all.find(match);
    if (found && statuses.includes(found.status)) return found;
    await page.waitForTimeout(1000);
  }
  throw new Error(`no case reached ${statuses.join("/")} in time`);
}

/** The case of a labelled e-mail: its Message-ID starts with the label (for example m007). */
export async function caseForLabel(page: Page, label: string): Promise<Summary | undefined> {
  const all = await api<Summary[]>(page, "/api/cases?status=all");
  return all.find((c) => c.message_id.startsWith(`<${label}.`));
}

// ---------------------------------------------------------------------------- demo helpers

/** A visible cursor, since recorded videos do not show the system pointer. */
export async function installCursor(page: Page) {
  await page.addInitScript(() => {
    const draw = () => {
      if (document.getElementById("demo-cursor")) return;
      const cursor = document.createElement("div");
      cursor.id = "demo-cursor";
      cursor.style.cssText =
        "position:fixed;left:-40px;top:-40px;width:18px;height:18px;border-radius:50%;" +
        "background:rgba(31,77,63,.85);border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.25);" +
        "z-index:2147483647;pointer-events:none;transform:translate(-50%,-50%);transition:transform 80ms";
      document.body.appendChild(cursor);
      window.addEventListener("mousemove", (e) => {
        cursor.style.left = `${e.clientX}px`;
        cursor.style.top = `${e.clientY}px`;
      });
      window.addEventListener("mousedown", () => (cursor.style.transform = "translate(-50%,-50%) scale(.75)"));
      window.addEventListener("mouseup", () => (cursor.style.transform = "translate(-50%,-50%)"));
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", draw);
    else draw();
  });
}

/** A short caption at the bottom of the frame. Empty text hides it. */
export async function caption(page: Page, text: string) {
  await page.evaluate((value) => {
    let box = document.getElementById("demo-caption");
    if (!box) {
      box = document.createElement("div");
      box.id = "demo-caption";
      box.style.cssText =
        "position:fixed;left:50%;bottom:84px;transform:translateX(-50%);max-width:880px;padding:10px 18px;" +
        "background:rgba(27,42,36,.92);color:#fff;border-radius:8px;font:600 18px 'Atkinson Hyperlegible',system-ui;" +
        "z-index:2147483646;pointer-events:none;text-align:center;transition:opacity 200ms";
      document.body.appendChild(box);
    }
    box.textContent = value;
    box.style.opacity = value ? "1" : "0";
  }, text);
}

let last = { x: 640, y: 360 };

/** Move the cursor to an element slowly, then click it. */
export async function glideClick(page: Page, target: Locator, pause = 350) {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (!box) throw new Error("target not visible");
  const to = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  const distance = Math.hypot(to.x - last.x, to.y - last.y);
  await page.mouse.move(to.x, to.y, { steps: Math.max(12, Math.round(distance / 14)) });
  last = to;
  await page.waitForTimeout(pause);
  await page.mouse.down();
  await page.mouse.up();
}

/** Type like a person, without mistakes. */
export async function typeSlowly(page: Page, text: string) {
  await page.keyboard.type(text, { delay: 70 });
}
