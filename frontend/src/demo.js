// Scripted, narrated simulation of the full PayCrew demo (MASTER.md section 8).
// Drives the mock backend step by step and tells the UI which overlays to show.
import { DEMO } from "./mock.js";

export class DemoCancelled extends Error {}

const usd = (c) => `$${(c / 100).toLocaleString("en-US")}`;

export const CHAPTERS = ["Problem", "Upload", "Analyze", "Pay", "Call #1", "Call #2", "Approve", "Close"];

/**
 * sim:  { add(rows), set(id, patch, msg), pay(id), get(id) }  from the mock client
 * ui:   { caption(c|null), chapter(i), phone(p|null), slack(s|null), open(id|null), flyIn(n) }
 * wait: (ms) => Promise that rejects with DemoCancelled when the demo is stopped
 */
export async function runDemo({ sim, ui, wait }) {
  const say = async (title, sub, ms) => {
    ui.caption({ title, sub });
    await wait(ms);
  };

  // 1. Problem
  ui.chapter(0);
  await say("Fraudsters pose as your vendors.", "They send a real-looking invoice with new bank details.", 3200);
  await say("$3.046B lost to business email compromise in 2025.", "The fix is a callback nobody has time to make.", 3400);
  await say("Meet PayCrew.", "An autonomous finance team: Claude reads, Brainbase agents call and pay.", 3000);

  // 2. Upload
  ui.chapter(1);
  ui.caption({ title: "Five invoices land.", sub: "Dropped on the board, or forwarded from the inbox." });
  ui.flyIn(DEMO.length);
  await wait(1300);
  const created = sim.add(DEMO);
  const byNum = Object.fromEntries(created.map((i) => [i.invoice_number, i]));
  await wait(1400);

  // 3. Analyze
  ui.chapter(2);
  ui.caption({ title: "A six-agent crew analyzes every PDF.", sub: "Claude parses and hunts for fraud; PO, duplicate and risk agents check our records." });
  for (const inv of created) {
    sim.set(inv.id, { status: "analyzing", analysis: sim.trace(inv.id, 0) }, `Analysis crew started on ${inv.invoice_number}`);
    for (let k = 1; k <= 6; k++) setTimeout(() => sim.set(inv.id, { analysis: sim.trace(inv.id, k) }), k * 300);
    await wait(350);
  }
  await wait(2200);

  ui.caption({ title: "Rules check each one against the vendor file.", sub: "Same bank account on file? Known vendor? Duplicate?" });
  const clean = ["INV-441", "INV-400", "INV-9921"];
  for (const num of clean) {
    sim.set(byNum[num].id, { risk_level: "low", risk_reasons: [] });
  }
  for (const num of ["INV-910", "INV-F42"]) {
    const inv = sim.get(byNum[num].id);
    const reason = `Bank details changed: invoice says ••••${inv.bank_last4_claimed}, file says ••••${inv.bank_last4_on_file}`;
    sim.set(inv.id, { status: "flagged", risk_level: "critical", risk_reasons: [reason] }, `Flagged ${num}: bank details changed`);
    await wait(500);
  }
  await wait(1200);

  // 4. Pay
  ui.chapter(3);
  ui.caption({ title: "Clean invoices get paid in seconds.", sub: "A Brainbase agent pays each vendor with the Link wallet." });
  for (const num of clean) {
    sim.pay(byNum[num].id);
    await wait(1100);
  }
  await wait(1400);

  // 5. Call #1: CleanVendor denies
  ui.chapter(4);
  const cv = sim.get(byNum["INV-910"].id);
  await say("Suspicious ones? PayCrew picks up the phone.", "It calls the number already on file, never the one on the invoice.", 2800);
  await call(cv, {
    who: "CleanVendor Inc",
    number: cv.vendor?.phone_on_file,
    lines: [
      ["agent", `Hi, this is PayCrew calling for Acme Supplies about invoice ${cv.invoice_number} for ${usd(cv.amount_cents)}.`],
      ["agent", `It asks us to pay a new account ending in ${cv.bank_last4_claimed}. Did your team change your bank details?`],
      ["vendor", "No. We haven't changed anything. That's not us."],
      ["agent", "Thanks for confirming. We'll hold this payment and flag it."],
      ["vendor", "Please do. I'm alerting our finance team now."],
    ],
    outcome: "denied",
  });
  sim.set(cv.id, {
    status: "blocked",
    call: { status: "done", outcome: "denied", attempt: 1, summary: "Vendor says they did not change bank details and will alert their finance team." },
  }, `CleanVendor Inc denied the change. Payment blocked, ${usd(cv.amount_cents)} saved`);
  await wait(2800); // let the FRAUD BLOCKED splash play first
  ui.caption({ title: `${usd(cv.amount_cents)} of fraud stopped.`, sub: "No human had to notice. Slack gets an alert." });
  ui.slack({ kind: "alert", invoice: sim.get(cv.id) });
  await wait(3800);
  ui.slack(null);

  // 6. Call #2: FastConsult confirms
  ui.chapter(5);
  const fc = sim.get(byNum["INV-F42"].id);
  ui.caption({ title: "Calls run one at a time.", sub: "Next up: FastConsult LLC, $4,500." });
  await wait(1800);
  await call(fc, {
    who: "FastConsult LLC",
    number: fc.vendor?.phone_on_file,
    lines: [
      ["agent", `Hi, PayCrew here for Acme Supplies, about invoice ${fc.invoice_number} for ${usd(fc.amount_cents)}.`],
      ["agent", `The invoice lists a new account ending in ${fc.bank_last4_claimed}. Can you confirm that's correct?`],
      ["vendor", "Yes, that's right. We switched banks last month."],
      ["agent", "Perfect, thank you. I'll pass that to the owner for approval."],
    ],
    outcome: "confirmed",
  });
  sim.set(fc.id, {
    status: "awaiting_approval",
    call: { status: "done", outcome: "confirmed", attempt: 1, summary: `Vendor confirmed they moved banks and the new account ending ${fc.bank_last4_claimed} is correct.` },
  }, "FastConsult LLC confirmed. Waiting for owner approval in Slack");

  // 7. Approve in Slack
  ui.chapter(6);
  ui.caption({ title: "Legit change? The owner decides with one tap.", sub: "Approve or reject right in Slack." });
  ui.slack({ kind: "approve", invoice: sim.get(fc.id), pressed: false });
  await wait(3200);
  ui.slack({ kind: "approve", invoice: sim.get(fc.id), pressed: true });
  await wait(700);
  ui.slack(null);
  sim.set(fc.id, {}, `Owner approved ${fc.invoice_number} in Slack`);
  sim.pay(fc.id);
  await wait(2600);

  // 8. Close
  ui.chapter(7);
  await say("4 invoices paid. $480 of fraud blocked. Zero humans on the phone.", "", 3400);
  await say("PayCrew", "The finance department a small business can't afford to hire.", 4200);
  ui.caption(null);

  async function call(inv, { who, number, lines, outcome }) {
    sim.set(inv.id, { status: "calling", call: { status: "in_progress", outcome: null, summary: null, attempt: 1 } },
      `Calling ${who} on file number`);
    ui.caption(null);
    const shown = [];
    ui.phone({ who, number, lines: shown, state: "ringing" });
    await wait(1800);
    ui.phone({ who, number, lines: shown, state: "live" });
    for (const line of lines) {
      shown.push({ from: line[0], text: line[1], id: shown.length });
      ui.phone({ who, number, lines: [...shown], state: "live", typing: line[0] });
      await wait(900 + line[1].length * 32);
    }
    ui.phone({ who, number, lines: [...shown], state: outcome });
    await wait(1500);
    ui.phone(null);
  }
}
