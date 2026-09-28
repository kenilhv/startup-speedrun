import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AnimatePresence,
  LayoutGroup,
  animate,
  motion,
  useMotionValue,
  useSpring,
  useTransform,
} from "framer-motion";
import { createClient, USING_MOCK } from "./api.js";
import { createMockClient } from "./mock.js";
import { CHAPTERS, DemoCancelled, runDemo } from "./demo.js";
import { Caption, ChapterBar, FlyIn, PhoneOverlay, SlackCard } from "./DemoOverlays.jsx";
import Workflow from "./Workflow.jsx";
import { bigWin, fraudBurst, paidBurst } from "./fx.js";

const COLUMNS = [
  ["received", "Received", "📥"],
  ["analyzing", "Analyzing", "🧠"],
  ["paying", "Paying", "💸"],
  ["settled", "Settled", "✅"],
  ["flagged", "Flagged", "🚩"],
  ["calling", "Calling", "📞"],
  ["awaiting_approval", "Awaiting approval", "⏳"],
  ["blocked", "Blocked", "🛡️"],
  ["escalated", "Escalated", "⚠️"],
];


const usd = (cents) =>
  cents == null ? "—" : `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;

// SQLite CURRENT_TIMESTAMP is UTC without a zone ("2026-09-28 13:02:11")
const toIso = (ts) => (ts && /^\d{4}-\d\d-\d\d \d/.test(ts) ? ts.replace(" ", "T") + "Z" : ts);

const spring = { type: "spring", stiffness: 380, damping: 30 };

/* ---------- small pieces ---------- */

function CountUp({ value, format = (v) => v }) {
  const [shown, setShown] = useState(value);
  const prev = useRef(value);
  useEffect(() => {
    const controls = animate(prev.current, value, {
      duration: 1.1,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setShown(v),
    });
    prev.current = value;
    return () => controls.stop();
  }, [value]);
  return <>{format(Math.round(shown))}</>;
}

function Bump({ value, children, className }) {
  return (
    <motion.span
      key={value}
      className={className}
      initial={{ scale: 1.6, opacity: 0.4 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 500, damping: 18 }}
    >
      {children}
    </motion.span>
  );
}

const STEP_ICON = { pass: "✓", warn: "!", fail: "✕", route: "→", info: "i" };

function CrewTrace({ steps }) {
  if (!steps?.length) return null;
  return (
    <ol className="crew">
      {steps.map((s, i) => (
        <motion.li
          key={s.id}
          className={`crew-step ${s.state} ${s.status ?? ""}`}
          initial={{ opacity: 0, x: 16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: i * 0.05 }}
        >
          <span className="crew-dot">{s.state === "running" ? <span className="crew-spin" /> : s.state === "done" ? STEP_ICON[s.status] ?? "•" : i + 1}</span>
          <div className="crew-body">
            <div className="crew-head">
              <b>{s.name}</b>
              <span className={`crew-engine ${String(s.engine).includes("Claude") ? "claude" : ""}`}>{s.engine}</span>
              {s.ms != null && s.state === "done" && <span className="crew-ms">{s.ms < 1000 ? `${s.ms}ms` : `${(s.ms / 1000).toFixed(1)}s`}</span>}
            </div>
            <div className="crew-finding">{s.state === "running" ? "Working…" : s.state === "pending" ? "Waiting" : s.finding}</div>
          </div>
        </motion.li>
      ))}
    </ol>
  );
}

function RiskBadge({ level }) {
  if (!level) return null;
  return (
    <motion.span
      className={`badge risk-${level}`}
      initial={{ scale: 0, rotate: -20 }}
      animate={{ scale: 1, rotate: 0 }}
      transition={{ type: "spring", stiffness: 600, damping: 15 }}
    >
      {level === "critical" && <span className="badge-dot" />}
      {level[0].toUpperCase() + level.slice(1)}
    </motion.span>
  );
}

function CallTimer({ since }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);
  const s = Math.max(0, Math.floor((now - since) / 1000));
  return <span className="timer">{`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`}</span>;
}

function Waveform() {
  return (
    <span className="wave" aria-hidden>
      {Array.from({ length: 7 }, (_, i) => (
        <i key={i} style={{ animationDelay: `${i * 0.09}s` }} />
      ))}
    </span>
  );
}

function ScanDoc() {
  return (
    <span className="scandoc" aria-hidden>
      <i /><i /><i /><i />
      <b />
    </span>
  );
}

/* ---------- card ---------- */

function Card({ inv, callStart, onOpen, ref }) {
  const name = inv.vendor?.name ?? inv.vendor_name_raw ?? inv.filename ?? "New invoice";
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const rx = useSpring(useTransform(my, [-0.5, 0.5], [10, -10]), { stiffness: 300, damping: 20 });
  const ry = useSpring(useTransform(mx, [-0.5, 0.5], [-12, 12]), { stiffness: 300, damping: 20 });
  const glareX = useTransform(mx, [-0.5, 0.5], ["0%", "100%"]);

  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - r.left) / r.width - 0.5);
    my.set((e.clientY - r.top) / r.height - 0.5);
  };
  const onLeave = () => {
    mx.set(0);
    my.set(0);
  };

  return (
    <motion.button
      ref={ref}
      id={`card-${inv.id}`}
      layout
      layoutId={`inv-${inv.id}`}
      initial={{ opacity: 0, y: -40, scale: 0.6, rotate: -6 }}
      animate={{ opacity: 1, y: 0, scale: 1, rotate: 0 }}
      exit={{ opacity: 0, scale: 0.6 }}
      transition={spring}
      className={`card-shell status-${inv.status}`}
      onClick={() => onOpen(inv.id)}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
    >
      <motion.div
        key={inv.status}
        className="card"
        style={{ rotateX: rx, rotateY: ry }}
        animate={
          inv.status === "blocked"
            ? { x: [0, -10, 10, -8, 8, -4, 4, 0] }
            : inv.status === "settled"
              ? { scale: [1, 1.08, 1] }
              : {}
        }
        transition={{ duration: 0.55 }}
      >
        <motion.span className="glare" style={{ left: glareX }} />
        <div className="card-top">
          <span className="vendor">{name}</span>
          <RiskBadge level={inv.risk_level} />
        </div>
        <div className="card-mid">
          <span className="num">{inv.invoice_number ?? "reading…"}</span>
          <span className="amount">{usd(inv.amount_cents)}</span>
        </div>

        {inv.status === "received" && <div className="note muted">Queued for analysis</div>}
        {inv.status === "analyzing" && (
          <div className="note scanning">
            <ScanDoc /> {(inv.analysis ?? []).filter((s) => s.state === "running").map((s) => s.name).join(" + ") || "Claude is reading…"}
            <span className="crew-mini">
              {(inv.analysis ?? []).map((s) => <i key={s.id} className={`${s.state} ${s.status ?? ""}`} />)}
            </span>
          </div>
        )}
        {inv.status === "flagged" && <div className="note warn">🚩 {inv.risk_reasons?.[0] ?? "Flagged"}</div>}
        {inv.status === "calling" && (
          <div className="note calling">
            <span className="radar" aria-hidden>
              <span className="phone">📞</span>
            </span>
            <Waveform />
            <CallTimer since={callStart ?? Date.now()} />
          </div>
        )}
        {inv.status === "settled" && (
          <div className="note ok">
            <motion.span
              className="check"
              initial={{ scale: 0, rotate: -90 }}
              animate={{ scale: 1, rotate: 0 }}
              transition={{ type: "spring", stiffness: 500, damping: 12, delay: 0.15 }}
            >
              ✓
            </motion.span>
            Paid via Brainbase · Link
          </div>
        )}
        {inv.status === "blocked" && (
          <>
            <div className="note bad">Fraud stopped: {usd(inv.amount_cents)} saved</div>
            <motion.div
              className="stamp"
              initial={{ scale: 3, opacity: 0, rotate: -30 }}
              animate={{ scale: 1, opacity: 1, rotate: -12 }}
              transition={{ type: "spring", stiffness: 400, damping: 14, delay: 0.1 }}
            >
              BLOCKED
            </motion.div>
          </>
        )}
        {inv.status === "awaiting_approval" && <div className="note gold">Vendor confirmed · tap to approve</div>}
        {inv.status === "paying" && (
          <div className="note paying">
            <span className="coin" aria-hidden>💸</span> Brainbase paying · awaiting Link
          </div>
        )}
        {inv.status === "escalated" && <div className="note warn">Needs a human</div>}
      </motion.div>
    </motion.button>
  );
}

/* ---------- drawer ---------- */

function Drawer({ inv, onClose, onApprove, onReject }) {
  const busy = useRef(false);
  const act = async (fn) => {
    if (busy.current) return;
    busy.current = true;
    try {
      await fn(inv.id);
    } finally {
      busy.current = false;
    }
  };
  const mismatch = inv.bank_last4_on_file && inv.bank_last4_claimed !== inv.bank_last4_on_file;
  const item = {
    hidden: { opacity: 0, x: 30 },
    show: { opacity: 1, x: 0, transition: spring },
  };

  return (
    <>
      <motion.div className="scrim" onClick={onClose} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
      <motion.aside
        className="drawer glass"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 320, damping: 34 }}
      >
        <motion.div
          className="drawer-inner"
          initial="hidden"
          animate="show"
          variants={{ show: { transition: { staggerChildren: 0.06, delayChildren: 0.1 } } }}
        >
          <motion.header variants={item} className="drawer-head">
            <div>
              <div className="drawer-vendor">{inv.vendor?.name ?? inv.vendor_name_raw}</div>
              <div className="muted">
                {inv.invoice_number} · due {inv.due_date ?? "n/a"}
              </div>
              <div className="drawer-amount">{usd(inv.amount_cents)}</div>
            </div>
            <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
          </motion.header>

          <motion.section variants={item}>
            <h3>Status</h3>
            <div className="row">
              <span className={`pill status-${inv.status}`}>
                {COLUMNS.find((c) => c[0] === inv.status)?.[2]} {COLUMNS.find((c) => c[0] === inv.status)?.[1]}
              </span>
              <RiskBadge level={inv.risk_level} />
            </div>
            {inv.risk_reasons?.length > 0 && (
              <ul className="reasons">
                {inv.risk_reasons.map((r) => <li key={r}>{r}</li>)}
              </ul>
            )}
          </motion.section>

          {inv.analysis?.length > 0 && (
            <motion.section variants={item}>
              <h3>Analysis crew · {inv.analysis.filter((s) => s.state === "done").length}/{inv.analysis.length} agents</h3>
              <CrewTrace steps={inv.analysis} />
            </motion.section>
          )}

          <motion.section variants={item}>
            <h3>Bank account check</h3>
            <div className={`bank ${mismatch ? "mismatch" : "match"}`}>
              <div>
                <div className="muted small">On invoice</div>
                <div className={`last4 ${mismatch ? "bad" : "good"}`}>••••{inv.bank_last4_claimed ?? "????"}</div>
              </div>
              <div className="vs">{mismatch ? "≠" : "="}</div>
              <div>
                <div className="muted small">On file</div>
                <div className="last4">••••{inv.bank_last4_on_file ?? "none"}</div>
              </div>
            </div>
          </motion.section>

          <motion.section variants={item}>
            <h3>Extracted by Claude</h3>
            <dl className="fields">
              <dt>Vendor</dt><dd>{inv.vendor_name_raw}</dd>
              <dt>Invoice #</dt><dd>{inv.invoice_number}</dd>
              <dt>Amount</dt><dd>{usd(inv.amount_cents)} {inv.currency?.toUpperCase()}</dd>
              <dt>Due</dt><dd>{inv.due_date ?? "n/a"}</dd>
              <dt>Phone on file</dt><dd>{inv.vendor?.phone_on_file ?? "n/a"}</dd>
            </dl>
          </motion.section>

          {inv.call && (
            <motion.section variants={item}>
              <h3>Verification call</h3>
              <div className="row">
                {inv.call.status === "in_progress" ? (
                  <span className="live"><Waveform /> Live · attempt {inv.call.attempt}</span>
                ) : (
                  <span className={`pill outcome-${inv.call.outcome}`}>
                    {inv.call.outcome ?? "pending"} · attempt {inv.call.attempt}
                  </span>
                )}
              </div>
              {inv.call.summary && <blockquote>{inv.call.summary}</blockquote>}
            </motion.section>
          )}

          {inv.payment && (
            <motion.section variants={item}>
              <h3>Payment</h3>
              <dl className="fields">
                <dt>Status</dt><dd className={`pay-${inv.payment.status}`}>{inv.payment.status}</dd>
                <dt>Paid by</dt><dd>{inv.payment.provider === "brainbase" ? "Brainbase payer agent (Link wallet)" : inv.payment.provider ?? "—"}</dd>
                {inv.payment.charged_cents != null && inv.payment.charged_cents !== inv.payment.amount_cents && (
                  <><dt>Demo charge</dt><dd className="muted">{usd(inv.payment.charged_cents)} actually charged for this demo</dd></>
                )}
                {inv.payment.task_id && (<><dt>Brainbase task</dt><dd className="link">{inv.payment.task_id}</dd></>)}
                {inv.payment.order_id && (<><dt>Order / receipt</dt><dd className="link">{inv.payment.order_id}</dd></>)}
                {inv.payment.stripe_transfer_id && (<><dt>Transfer</dt><dd className="link">{inv.payment.stripe_transfer_id}</dd></>)}
                {inv.payment.error_code && (<><dt>Note</dt><dd>{inv.payment.error_code}</dd></>)}
              </dl>
            </motion.section>
          )}

          {inv.status === "awaiting_approval" && (
            <motion.div variants={item} className="actions">
              <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} className="btn approve" onClick={() => act(onApprove)}>
                Approve and pay {usd(inv.amount_cents)}
              </motion.button>
              <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} className="btn reject" onClick={() => act(onReject)}>
                Reject
              </motion.button>
            </motion.div>
          )}
        </motion.div>
      </motion.aside>
    </>
  );
}

/* ---------- drop zone ---------- */

function DropZone({ onFiles }) {
  const [over, setOver] = useState(false);
  const input = useRef(null);
  const take = (list) => {
    const files = [...list].filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (files.length) onFiles(files);
  };
  return (
    <motion.div
      className={`drop ${over ? "over" : ""}`}
      animate={over ? { scale: 1.02 } : { scale: 1 }}
      whileHover={{ scale: 1.01 }}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files); }}
      onClick={() => input.current?.click()}
    >
      <input ref={input} type="file" accept="application/pdf" multiple hidden onChange={(e) => take(e.target.files)} />
      <motion.span className="drop-icon" animate={{ y: over ? -6 : [0, -5, 0] }} transition={over ? {} : { repeat: Infinity, duration: 2 }}>
        📄
      </motion.span>
      <div>
        <strong>{over ? "Let go. The crew takes it from here." : "Drop invoice PDFs here"}</strong>
        <div className="muted small">Several at once is fine. Claude reads, rules score, Brainbase calls and pays.</div>
      </div>
    </motion.div>
  );
}

/* ---------- splash + toasts ---------- */

function Splash({ splash }) {
  return (
    <AnimatePresence>
      {splash && (
        <motion.div
          key={splash.id}
          className={`splash ${splash.kind}`}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            className="splash-card"
            initial={{ scale: 0.3, rotate: -8, opacity: 0 }}
            animate={{ scale: 1, rotate: 0, opacity: 1 }}
            exit={{ scale: 1.4, opacity: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 16 }}
          >
            <div className="splash-icon">{splash.kind === "fraud" ? "🛡️" : "💸"}</div>
            <div className="splash-title">{splash.title}</div>
            <div className="splash-sub">{splash.sub}</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Toasts({ toasts }) {
  return (
    <div className="toasts">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            className={`toast ${t.kind}`}
            initial={{ opacity: 0, x: -60, scale: 0.8 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: -60, scale: 0.8 }}
            transition={spring}
          >
            {t.text}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

const FEED_ICON = [
  [/paid|paying|approved/i, "💸"],
  [/calling/i, "📞"],
  [/denied|blocked|rejected/i, "🛡️"],
  [/confirmed/i, "✅"],
  [/flagged/i, "🚩"],
  [/reading|claude/i, "🧠"],
  [/received/i, "📥"],
];
const feedIcon = (m = "") => FEED_ICON.find(([re]) => re.test(m))?.[1] ?? "•";

/* ---------- app ---------- */

export default function App() {
  const [invoices, setInvoices] = useState({});
  const [feed, setFeed] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const [splash, setSplash] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [flash, setFlash] = useState(null);
  const callStarts = useRef({});
  const lastStatus = useRef({});
  const client = useRef(null);
  const handlers = useRef(null);
  const demoToken = useRef(null);
  const [demo, setDemo] = useState(null); // null | "running" | "done"
  const [caption, setCaption] = useState(null);
  const [chapter, setChapter] = useState(-1);
  const [phone, setPhone] = useState(null);
  const [slack, setSlack] = useState(null);
  const [fly, setFly] = useState(null);
  const [flows, setFlows] = useState([]); // recent status changes, animated on the workflow

  const toast = useCallback((text, kind = "info") => {
    const id = Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);

  const celebrate = useCallback(
    (inv, prev) => {
      if (prev === inv.status) return;
      const el = () => document.getElementById(`card-${inv.id}`);
      if (inv.status === "settled") {
        setTimeout(() => paidBurst(el()), 350);
        toast(`💸 Paid ${inv.vendor_name_raw} ${usd(inv.amount_cents)}`, "ok");
        if (["high", "critical"].includes(inv.risk_level)) bigWin(); // an owner-approved payment
      } else if (inv.status === "blocked") {
        setTimeout(() => fraudBurst(el()), 350);
        setFlash({ id: Math.random(), kind: "red" });
        setSplash({
          id: Math.random(),
          kind: "fraud",
          title: `FRAUD BLOCKED`,
          sub: `${usd(inv.amount_cents)} to ••••${inv.bank_last4_claimed ?? "????"} stopped · ${inv.vendor_name_raw}`,
        });
        setTimeout(() => setSplash(null), 2600);
      } else if (inv.status === "paying") {
        toast(`💸 Brainbase agent is paying ${inv.vendor_name_raw} ${usd(inv.amount_cents)}`, "ok");
      } else if (inv.status === "calling") {
        toast(`📞 Calling ${inv.vendor_name_raw} on the number on file`, "call");
      } else if (inv.status === "flagged" && prev !== "calling") {
        toast(`🚩 ${inv.invoice_number}: ${inv.risk_reasons?.[0] ?? "flagged"}`, "warn");
      } else if (inv.status === "awaiting_approval") {
        toast(`✅ ${inv.vendor_name_raw} confirmed. Needs your approval`, "gold");
      }
    },
    [toast]
  );

  const upsert = useCallback(
    (inv) => {
      if (inv.status === "calling" && !callStarts.current[inv.id]) callStarts.current[inv.id] = Date.now();
      if (inv.status !== "calling") delete callStarts.current[inv.id];
      const prev = lastStatus.current[inv.id];
      lastStatus.current[inv.id] = inv.status;
      if (prev !== undefined) celebrate(inv, prev);
      if (prev !== inv.status && (prev !== undefined || inv.status === "received")) {
        const flow = { id: Math.random(), from: prev, to: inv.status };
        setFlows((f) => [...f.slice(-20), flow]);
        setTimeout(() => setFlows((f) => f.filter((x) => x !== flow)), 2400);
      }
      setInvoices((p) => ({ ...p, [inv.id]: inv }));
    },
    [celebrate]
  );

  useEffect(() => {
    handlers.current = {
      onInvoice: upsert,
      onActivity: (a) => setFeed((f) => [{ ...a, key: `${a.ts}-${Math.random()}` }, ...f].slice(0, 60)),
      onConnection: setConnected,
    };
    const c = createClient(handlers.current);
    client.current = c;
    c.loadInvoices()
      .then((list) => list.forEach(upsert))
      .catch((e) => setError(`Could not load invoices: ${e.message}`));
    c.loadEvents()
      .then((ev) => setFeed(ev.map((a) => ({ ...a, ts: toIso(a.ts ?? a.created_at), key: `e${a.id}` }))))
      .catch(() => {});
    return () => client.current?.close();
  }, [upsert]);

  const run = (fn) => (...args) =>
    fn(...args).then(() => setError(null)).catch((e) => setError(e.message));

  const upload = run((files) => client.current.upload(files).then((created) => created?.forEach?.(upsert)));
  const approve = run((id) => client.current.approve(id));
  const reject = run((id) => client.current.reject(id));
  const reset = run(async () => {
    if (demo) return exitDemo();
    await client.current.reset();
    clearBoard();
  });

  const clearBoard = () => {
    setInvoices({});
    setFeed([]);
    setOpenId(null);
    setSplash(null);
    callStarts.current = {};
    lastStatus.current = {};
  };

  const stopDemo = () => {
    const t = demoToken.current;
    if (t) {
      t.cancelled = true;
      t.timers.forEach(clearTimeout);
      t.rejects.forEach((r) => r(new DemoCancelled()));
    }
    demoToken.current = null;
    setCaption(null);
    setPhone(null);
    setSlack(null);
    setFly(null);
    setChapter(-1);
  };

  const startDemo = async () => {
    stopDemo();
    client.current?.close();
    clearBoard();
    const mock = createMockClient(handlers.current);
    client.current = mock;
    const token = { cancelled: false, timers: [], rejects: [] };
    demoToken.current = token;
    const wait = (ms) =>
      new Promise((resolve, reject) => {
        if (token.cancelled) return reject(new DemoCancelled());
        token.rejects.push(reject);
        token.timers.push(setTimeout(resolve, ms));
      });
    setDemo("running");
    try {
      await runDemo({
        sim: mock.sim,
        wait,
        ui: {
          caption: setCaption,
          chapter: setChapter,
          phone: setPhone,
          slack: setSlack,
          open: setOpenId,
          flyIn: (n) => setFly({ id: Math.random(), n }),
        },
      });
      if (demoToken.current === token) setDemo("done");
    } catch (e) {
      if (!(e instanceof DemoCancelled)) setError(e.message);
    }
  };

  // Leave the simulation and reconnect to the real backend (or a fresh mock board).
  const exitDemo = () => {
    stopDemo();
    setDemo(null);
    client.current?.close();
    clearBoard();
    const c = createClient(handlers.current);
    client.current = c;
    c.loadInvoices().then((l) => l.forEach(upsert)).catch(() => {});
  };

  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("demo")) {
      const t = setTimeout(startDemo, 900);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const list = Object.values(invoices);
  const stats = useMemo(() => {
    const paid = list.filter((i) => i.status === "settled");
    const blocked = list.filter((i) => i.status === "blocked");
    const done = list.filter((i) => ["settled", "blocked", "escalated"].includes(i.status)).length;
    return {
      paid: paid.length,
      paidCents: paid.reduce((s, i) => s + i.amount_cents, 0),
      fraudCents: blocked.reduce((s, i) => s + i.amount_cents, 0),
      progress: list.length ? done / list.length : 0,
      calling: list.some((i) => i.status === "calling"),
    };
  }, [list]);

  const open = openId != null ? invoices[openId] : null;

  return (
    <div className={`app ${stats.calling ? "is-calling" : ""}`}>
      <div className="bg" aria-hidden>
        <span className="blob b1" />
        <span className="blob b2" />
        <span className="blob b3" />
        <span className="grid" />
      </div>

      <AnimatePresence>
        {flash && (
          <motion.div
            key={flash.id}
            className="flash"
            initial={{ opacity: 0.55 }}
            animate={{ opacity: 0 }}
            transition={{ duration: 0.9 }}
            onAnimationComplete={() => setFlash(null)}
          />
        )}
      </AnimatePresence>

      <header className="top glass">
        <div className="brand">
          <motion.span className="logo" animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 8, ease: "linear" }}>
            ◆
          </motion.span>
          <div>
            <div className="brand-name">PayCrew</div>
            <div className="brand-sub">your autonomous finance team</div>
          </div>
          <span className={`dot ${connected ? "on" : ""}`} title={connected ? "Live" : "Reconnecting"} />
          {demo ? <span className="mock sim">simulation</span> : USING_MOCK && <span className="mock">mock data</span>}
        </div>

        <div className="counters">
          <div className="stat">
            <div className="stat-label">Invoices paid</div>
            <div className="stat-value green">
              <Bump value={stats.paid}>{stats.paid}</Bump>
              <span className="stat-sub"> · <CountUp value={stats.paidCents} format={usd} /></span>
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Fraud blocked</div>
            <div className="stat-value red">
              <CountUp value={stats.fraudCents} format={usd} />
            </div>
          </div>
        </div>

        <div className="top-actions">
          {demo === "running" ? (
            <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }} className="btn stop" onClick={() => { stopDemo(); setDemo("done"); }}>
              ■ Stop demo
            </motion.button>
          ) : (
            <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }} className="btn cinema" onClick={startDemo}>
              🎬 {demo === "done" ? "Replay" : "Simulated demo"}
            </motion.button>
          )}
          {!demo && USING_MOCK && (
            <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }} className="btn primary" onClick={() => client.current.playDemo()}>
              ▶ Play demo
            </motion.button>
          )}
          <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }} className="btn ghost" onClick={reset}>
            {demo ? "✕ Exit demo" : "↺ Reset"}
          </motion.button>
        </div>
        <motion.div className="progress" animate={{ scaleX: stats.progress }} transition={{ type: "spring", stiffness: 80, damping: 20 }} />
      </header>

      <AnimatePresence>
        {error && (
          <motion.div className="error" onClick={() => setError(null)} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="layout">
        <main>
          <Workflow invoices={list} flows={flows} />
          <DropZone onFiles={upload} />
          <LayoutGroup>
            <div className="board">
              {COLUMNS.map(([key, label, icon], ci) => {
                const cards = list.filter((i) => i.status === key).sort((a, b) => a.id - b.id);
                return (
                  <motion.div
                    key={key}
                    className={`col col-${key} ${cards.length ? "has" : ""}`}
                    initial={{ opacity: 0, y: 30 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ ...spring, delay: ci * 0.05 }}
                  >
                    <div className="col-head">
                      <span>
                        <span className="col-icon">{icon}</span> {label}
                      </span>
                      <Bump value={cards.length} className="count">{cards.length}</Bump>
                    </div>
                    <div className="col-body">
                      <AnimatePresence mode="popLayout">
                        {cards.map((inv) => (
                          <Card key={inv.id} inv={inv} callStart={callStarts.current[inv.id]} onOpen={setOpenId} />
                        ))}
                      </AnimatePresence>
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </LayoutGroup>
        </main>

        <aside className="feed glass">
          <h2>
            <span className="live-dot" /> Live activity
          </h2>
          {feed.length === 0 && <div className="muted small">Nothing yet. Drop some invoices or hit Play demo.</div>}
          <AnimatePresence initial={false}>
            {feed.map((a) => (
              <motion.div
                key={a.key}
                layout
                className="feed-line"
                initial={{ opacity: 0, x: 40, backgroundColor: "rgba(22,163,74,0.16)" }}
                animate={{ opacity: 1, x: 0, backgroundColor: "rgba(22,163,74,0)" }}
                transition={{ ...spring, backgroundColor: { duration: 1.5 } }}
                onClick={() => a.invoice_id && invoices[a.invoice_id] && setOpenId(a.invoice_id)}
              >
                <span className="feed-icon">{feedIcon(a.message)}</span>
                <span>
                  <span className="time">
                    {a.ts ? new Date(a.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                  </span>
                  {a.message}
                </span>
              </motion.div>
            ))}
          </AnimatePresence>
        </aside>
      </div>

      <AnimatePresence>
        {open && <Drawer key={open.id} inv={open} onClose={() => setOpenId(null)} onApprove={approve} onReject={reject} />}
      </AnimatePresence>

      <ChapterBar chapters={CHAPTERS} active={demo === "running" ? chapter : -1} />
      <Caption caption={caption} />
      <FlyIn fly={fly} />
      <PhoneOverlay phone={phone} />
      <SlackCard slack={slack} />
      <Splash splash={splash} />
      <Toasts toasts={toasts} />
    </div>
  );
}
