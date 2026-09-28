// Mock backend: the 5 demo invoices from MASTER.md section 6, walked through the real status flow
// on a timer so the board animates without Zubair's API. Same interface as the HTTP client.

const VENDORS = {
  OfficeSupplyCo: { id: 1, bank: "0011" },
  "PaperWorks Ltd": { id: 2, bank: "0022" },
  "TechSoftware Inc": { id: 3, bank: "0033" },
  "CleanVendor Inc": { id: 4, bank: "0042" },
  "FastConsult LLC": { id: 5, bank: "0055" },
};

export const DEMO = [
  ["OfficeSupplyCo", "INV-441", 80000, "0011", "2026-10-12"],
  ["PaperWorks Ltd", "INV-400", 35000, "0022", "2026-10-15"],
  ["TechSoftware Inc", "INV-9921", 120000, "0033", "2026-10-20"],
  ["CleanVendor Inc", "INV-910", 48000, "9921", "2026-10-05"],
  ["FastConsult LLC", "INV-F42", 450000, "7788", "2026-10-30"],
];

const usd = (c) => `$${(c / 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
const now = () => new Date().toISOString();

function makeInvoice(id, [vendor, number, cents, claimed, due]) {
  const v = VENDORS[vendor];
  return {
    id,
    invoice_number: number,
    vendor: v ? { id: v.id, name: vendor, phone_on_file: "+1 (415) 555-01" + String(v.id).padStart(2, "0") } : null,
    vendor_name_raw: vendor,
    amount_cents: cents,
    currency: "usd",
    due_date: due,
    status: "received",
    risk_level: null,
    risk_reasons: [],
    bank_last4_claimed: claimed,
    bank_last4_on_file: v?.bank ?? null,
    call: null,
    payment: null,
    created_at: now(),
  };
}

export function createMockClient({ onInvoice, onActivity, onConnection }) {
  let invoices = [];
  let nextId = 1;
  let timers = [];
  let callQueue = Promise.resolve();

  const later = (ms, fn) => timers.push(setTimeout(fn, ms));
  const wait = (ms) => new Promise((r) => later(ms, r));

  function update(id, patch, message) {
    invoices = invoices.map((inv) => (inv.id === id ? { ...inv, ...patch } : inv));
    const inv = invoices.find((i) => i.id === id);
    onInvoice(inv);
    if (message) onActivity({ type: "activity", invoice_id: id, message, ts: now() });
    return inv;
  }

  function pay(inv) {
    const transfer = "tr_test_" + Math.random().toString(36).slice(2, 12);
    update(
      inv.id,
      { status: "settled", payment: { stripe_transfer_id: transfer, amount_cents: inv.amount_cents, status: "paid" } },
      `Stripe paid ${inv.vendor_name_raw} ${usd(inv.amount_cents)}`
    );
  }

  function runCall(inv) {
    // one call at a time, like Zubair's queue
    callQueue = callQueue.then(async () => {
      update(inv.id, { status: "calling", call: { status: "in_progress", outcome: null, summary: null, attempt: 1 } },
        `Calling ${inv.vendor_name_raw} on file number`);
      await wait(6000);
      const denies = inv.vendor_name_raw === "CleanVendor Inc";
      if (denies) {
        update(inv.id, {
          status: "blocked",
          call: { status: "done", outcome: "denied", attempt: 1,
            summary: "Vendor says they did not change bank details and will alert their finance team." },
        }, `${inv.vendor_name_raw} denied the change. Payment blocked, ${usd(inv.amount_cents)} saved`);
      } else {
        update(inv.id, {
          status: "awaiting_approval",
          call: { status: "done", outcome: "confirmed", attempt: 1,
            summary: `Vendor confirmed they moved banks and the new account ending ${inv.bank_last4_claimed} is correct.` },
        }, `${inv.vendor_name_raw} confirmed. Waiting for owner approval in Slack`);
      }
    });
  }

  function process(inv, delay) {
    later(delay, () => update(inv.id, { status: "analyzing" }, `Claude is reading ${inv.invoice_number}`));
    later(delay + 2200, () => {
      const reasons = [];
      let level = "low";
      if (!inv.vendor) {
        level = "high";
        reasons.push("Unknown vendor");
      } else if (inv.bank_last4_claimed !== inv.bank_last4_on_file) {
        level = "critical";
        reasons.push(`Bank details changed: invoice says ••••${inv.bank_last4_claimed}, file says ••••${inv.bank_last4_on_file}`);
      }
      if (level === "low") {
        update(inv.id, { risk_level: level, risk_reasons: [] });
        pay(invoices.find((i) => i.id === inv.id));
      } else {
        const flagged = update(inv.id, { status: "flagged", risk_level: level, risk_reasons: reasons },
          `Flagged ${inv.invoice_number}: ${reasons[0]}`);
        runCall(flagged);
      }
    });
  }

  function addInvoices(rows, auto = true) {
    const created = rows.map((row) => makeInvoice(nextId++, row));
    invoices = [...invoices, ...created];
    created.forEach((inv, i) => {
      onInvoice(inv);
      onActivity({ type: "activity", invoice_id: inv.id, message: `Received ${inv.invoice_number} from ${inv.vendor_name_raw}`, ts: now() });
      if (auto) process(inv, 600 + i * 450);
    });
    return created;
  }

  function start() {
    timers.forEach(clearTimeout);
    timers = [];
    callQueue = Promise.resolve();
    invoices = [];
    nextId = 1;
  }

  start();
  later(0, () => onConnection(true));

  return {
    loadInvoices: async () => invoices,
    loadEvents: async () => [],
    async upload(files) {
      // Mock mode: match dropped files to demo invoices by name, otherwise replay the full demo set.
      const picked = DEMO.filter((d) => files.some((f) => f.name.includes(d[1])));
      return addInvoices(picked.length ? picked : DEMO);
    },
    async approve(id) {
      const inv = invoices.find((i) => i.id === id);
      update(id, {}, `Owner approved ${inv.invoice_number}`);
      pay(inv);
    },
    async reject(id) {
      const inv = invoices.find((i) => i.id === id);
      update(id, { status: "blocked" }, `Owner rejected ${inv.invoice_number}`);
    },
    async reset() {
      start();
      return { ok: true };
    },
    playDemo: () => addInvoices(DEMO),
    // Manual controls for the scripted simulated demo (demo.js drives every step itself).
    sim: {
      add: (rows) => addInvoices(rows, false),
      set: (id, patch, message) => update(id, patch, message),
      pay: (id) => pay(invoices.find((i) => i.id === id)),
      get: (id) => invoices.find((i) => i.id === id),
    },
    close() {
      timers.forEach(clearTimeout);
    },
  };
}
