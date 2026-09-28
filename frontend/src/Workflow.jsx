// Live agent workflow. The analysis crew is drawn as its six agents; every node is clickable and
// opens a small popover with what the agent does and what it is doing right now.
// Edges always carry an ambient flow; each status change (and each crew step finishing) fires a
// glowing comet along the exact route the invoice took.
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

const VW = 1320;
const VH = 360;
const BIG = { w: 150, h: 62 };
const CHIP = { w: 134, h: 52 };

const NODES = {
  inbox: { x: 20, y: 149, icon: "📥", name: "Intake", tone: "green", ...BIG },
  // analysis crew (Agentforge-style six agents)
  invoice_parser: { x: 218, y: 154, icon: "🧠", name: "Invoice Parser", tone: "mint", crew: true, ...CHIP },
  po_matcher: { x: 385, y: 58, icon: "📑", name: "PO Matcher", tone: "green", crew: true, ...CHIP },
  duplicate_detector: { x: 385, y: 154, icon: "🧬", name: "Duplicates", tone: "green", crew: true, ...CHIP },
  fraud_signal: { x: 385, y: 250, icon: "🕵️", name: "Fraud Signal", tone: "mint", crew: true, ...CHIP },
  risk_scorer: { x: 552, y: 106, icon: "⚖️", name: "Risk Scorer", tone: "mint", crew: true, ...CHIP },
  approval_router: { x: 552, y: 202, icon: "🧭", name: "Router", tone: "green", crew: true, ...CHIP },
  // downstream
  payer: { x: 1150, y: 30, icon: "💳", name: "Brainbase Payer", tone: "green", ...BIG },
  owner: { x: 965, y: 149, icon: "👤", name: "Owner · Slack", tone: "amber", ...BIG },
  queue: { x: 760, y: 262, icon: "🚦", name: "Call Queue", tone: "amber", ...BIG },
  caller: { x: 965, y: 262, icon: "📞", name: "Vendor Caller", tone: "blue", ...BIG },
  blocked: { x: 1150, y: 262, icon: "🛡️", name: "Fraud Shield", tone: "red", ...BIG },
};

const CREW_IDS = ["invoice_parser", "po_matcher", "duplicate_detector", "fraud_signal", "risk_scorer", "approval_router"];
const GROUP = { x: 200, y: 18, w: 505, h: 318 };

// [from, fromPort, to, toPort, kind?]
const EDGES = [
  ["inbox", "r", "invoice_parser", "l"],
  ["invoice_parser", "r", "po_matcher", "l"],
  ["invoice_parser", "r", "duplicate_detector", "l"],
  ["invoice_parser", "r", "fraud_signal", "l"],
  ["po_matcher", "r", "risk_scorer", "l"],
  ["duplicate_detector", "r", "risk_scorer", "l"],
  ["fraud_signal", "r", "risk_scorer", "l"],
  ["risk_scorer", "b", "approval_router", "t"],
  ["approval_router", "r", "payer", "l"],
  ["approval_router", "r", "queue", "l"],
  ["queue", "r", "caller", "l"],
  ["caller", "r", "blocked", "l"],
  ["caller", "t", "owner", "b"],
  ["owner", "r", "payer", "b"],
  ["owner", "r", "blocked", "t"],
  ["caller", "b", "queue", "b", "retry"],
];

// status change -> the edges an invoice travels
const ROUTES = {
  "received>analyzing": [["inbox", "invoice_parser"]],
  "analyzing>settled": [["approval_router", "payer"]],
  "analyzing>paying": [["approval_router", "payer"]],
  "awaiting_approval>paying": [["owner", "payer"]],
  "analyzing>flagged": [["approval_router", "queue"]],
  "flagged>calling": [["queue", "caller"]],
  "calling>blocked": [["caller", "blocked"]],
  "calling>awaiting_approval": [["caller", "owner"]],
  "calling>escalated": [["caller", "owner"]],
  "calling>flagged": [["caller", "queue"]],
  "awaiting_approval>settled": [["owner", "payer"]],
  "awaiting_approval>blocked": [["owner", "blocked"]],
  "escalated>settled": [["owner", "payer"]],
  "escalated>blocked": [["owner", "blocked"]],
};

// a crew step finishing sends a comet to the next agent(s)
const CREW_NEXT = {
  invoice_parser: ["po_matcher", "duplicate_detector", "fraud_signal"],
  po_matcher: ["risk_scorer"],
  duplicate_detector: ["risk_scorer"],
  fraud_signal: ["risk_scorer"],
  risk_scorer: ["approval_router"],
};

// where an invoice "sits" for each status (crew agents are counted from the live trace instead)
const AT = {
  received: "inbox",
  flagged: "queue",
  calling: "caller",
  awaiting_approval: "owner",
  escalated: "owner",
  paying: "payer",
  settled: "payer",
  blocked: "blocked",
};

const TONE = { green: "#16a34a", mint: "#10b981", amber: "#d97706", blue: "#0284c7", red: "#dc2626" };

const INFO = {
  inbox: { engine: "Backend", what: "Receives invoice PDFs from the board (and later email) and starts one analysis per file, all in parallel." },
  invoice_parser: { engine: "Claude · Opus 5", what: "Reads the PDF and extracts vendor, invoice number, total, due date, PO and the last 4 digits of the bank account. Falls back to text matching if Claude is unavailable." },
  po_matcher: { engine: "Rules", what: "Looks the PO up in our purchase orders and checks it belongs to this vendor with the same amount." },
  duplicate_detector: { engine: "Rules", what: "Checks payment history for the same vendor and invoice number, so nothing is paid twice." },
  fraud_signal: { engine: "Claude · Opus 5", what: "Reviews the invoice against the vendor file for impersonation signs: changed bank details, urgency, mismatched names, unusual amounts." },
  risk_scorer: { engine: "Rules + Claude", what: "Deterministic rules set LOW / HIGH / CRITICAL (these decide what happens to money). Claude writes the plain-English explanation." },
  approval_router: { engine: "Rules", what: "Low risk → pay automatically. High or critical → verify by phone. Unknown vendor → a human." },
  payer: { engine: "Brainbase agent · Link", what: "The PayCrew Payer agent pays the vendor's trusted checkout link with the Link wallet, after owner approval in Link. Marked paid only when its receipt matches exactly." },
  owner: { engine: "Slack + board", what: "The owner approves or rejects vendor-confirmed bank changes, and handles escalations." },
  queue: { engine: "Backend", what: "Runs verification calls one at a time. No answer → one retry, then escalate." },
  caller: { engine: "Vapi voice agent", what: "Calls the vendor on the number already on file (never the invoice's) and asks if they really changed bank details." },
  blocked: { engine: "Backend", what: "Payments stopped because the vendor denied the change or the owner rejected it." },
};

function port(id, side) {
  const n = NODES[id];
  if (side === "l") return [n.x, n.y + n.h / 2, -1, 0];
  if (side === "r") return [n.x + n.w, n.y + n.h / 2, 1, 0];
  if (side === "t") return [n.x + n.w / 2, n.y, 0, -1];
  return [n.x + n.w / 2, n.y + n.h, 0, 1];
}

function edgePath([a, pa, b, pb, kind]) {
  const [x1, y1, dx1, dy1] = port(a, pa);
  const [x2, y2, dx2, dy2] = port(b, pb);
  const k = kind === "retry" ? 46 : Math.max(34, Math.hypot(x2 - x1, y2 - y1) * 0.42);
  return `M ${x1} ${y1} C ${x1 + dx1 * k} ${y1 + dy1 * k}, ${x2 + dx2 * k} ${y2 + dy2 * k}, ${x2} ${y2}`;
}

const edgeId = (a, b) => `wf-${a}-${b}`;
const findEdge = (a, b) => EDGES.find((e) => e[0] === a && e[2] === b);
const usd = (c) => `$${((c ?? 0) / 100).toLocaleString("en-US")}`;

/* A glowing comet (head + fading tail) that follows one path once. */
function Comet({ d, delay, color }) {
  const path = useRef(null);
  const dots = useRef([]);
  useEffect(() => {
    const el = path.current;
    const len = el.getTotalLength();
    const t0 = performance.now() + delay;
    const dur = 850;
    let raf;
    const ease = (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
    const tick = (now) => {
      const t = (now - t0) / dur;
      dots.current.forEach((c, i) => {
        if (!c) return;
        const k = Math.min(1, Math.max(0, t - i * 0.045));
        const pt = el.getPointAtLength(ease(k) * len);
        c.setAttribute("cx", pt.x);
        c.setAttribute("cy", pt.y);
        const alive = t - i * 0.045 > 0 && k < 1;
        c.setAttribute("opacity", alive ? (1 - i / 7) * (k > 0.88 ? (1 - k) / 0.12 : 1) : 0);
      });
      if (t < 1.4) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [d, delay]);
  return (
    <g filter="url(#wf-glow)" pointerEvents="none">
      <path ref={path} d={d} fill="none" stroke="none" />
      {Array.from({ length: 7 }, (_, i) => (
        <circle key={i} ref={(el) => (dots.current[i] = el)} r={i === 0 ? 6 : 5 - i * 0.55} fill={color} opacity="0" />
      ))}
    </g>
  );
}

function Node({ id, n, count, hot, live, selected, onSelect }) {
  const compact = n.crew;
  return (
    <foreignObject x={n.x - 14} y={n.y - 14} width={n.w + 28} height={n.h + 28} style={{ overflow: "visible" }}>
      <div className="wfn-wrap">
        <motion.button
          type="button"
          className={`wfn tone-${n.tone} ${compact ? "chip" : ""} ${count > 0 ? "active" : ""} ${hot ? "hot" : ""} ${selected ? "selected" : ""}`}
          style={{ width: n.w, height: n.h }}
          onClick={(e) => { e.stopPropagation(); onSelect(id); }}
          animate={hot ? { scale: [1, 1.08, 1], y: [0, -3, 0] } : { scale: 1, y: 0 }}
          whileHover={{ y: -2 }}
          transition={{ duration: 0.5, ease: [0.34, 1.56, 0.64, 1] }}
        >
          <AnimatePresence>
            {hot && (
              <motion.span key="ripple" className="wfn-ripple" initial={{ opacity: 0.55, scale: 1 }}
                animate={{ opacity: 0, scale: 1.35 }} exit={{ opacity: 0 }} transition={{ duration: 0.9, ease: "easeOut" }} />
            )}
          </AnimatePresence>
          <div className="wfn-icon">
            <span>{n.icon}</span>
            {count > 0 && (id === "invoice_parser" || id === "fraud_signal" || id === "risk_scorer") && <span className="wfn-orbit" />}
            {id === "caller" && count > 0 && <span className="wfn-radar" />}
          </div>
          <div className="wfn-text">
            <div className="wfn-name">{n.name}</div>
            <div className="wfn-live">
              <span className="wfn-dot" />
              {live}
            </div>
          </div>
          <AnimatePresence>
            {count > 0 && (
              <motion.span key={count} className="wfn-count" initial={{ scale: 0, rotate: -30 }} animate={{ scale: 1, rotate: 0 }}
                exit={{ scale: 0 }} transition={{ type: "spring", stiffness: 600, damping: 15 }}>
                {count}
              </motion.span>
            )}
          </AnimatePresence>
        </motion.button>
      </div>
    </foreignObject>
  );
}

function Popover({ id, stats, onClose }) {
  const n = NODES[id];
  const info = INFO[id];
  const leftPct = ((n.x + n.w / 2) / VW) * 100;
  const below = n.y + n.h < VH * 0.62;
  const topPct = ((below ? n.y + n.h + 8 : n.y - 8) / VH) * 100;
  const s = stats[id] ?? {};
  return (
    <motion.div
      className={`wf-pop tone-${n.tone} ${below ? "below" : "above"}`}
      style={{ left: `clamp(140px, ${leftPct}%, calc(100% - 140px))`, top: `${topPct}%` }}
      initial={{ opacity: 0, scale: 0.92, y: below ? -6 : 6 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.92 }}
      transition={{ type: "spring", stiffness: 500, damping: 32 }}
      onClick={(e) => e.stopPropagation()}
      role="dialog"
      aria-label={`${n.name} details`}
    >
      <div className="wf-pop-head">
        <span className="wf-pop-icon">{n.icon}</span>
        <div>
          <div className="wf-pop-name">{n.name}</div>
          <span className={`wf-pop-engine ${info.engine.includes("Claude") ? "claude" : ""}`}>{info.engine}</span>
        </div>
        <button className="wf-pop-x" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <p className="wf-pop-what">{info.what}</p>
      <div className="wf-pop-stats">
        {s.now && <div><span>Now</span><b>{s.now}</b></div>}
        {s.handled != null && <div><span>Handled</span><b>{s.handled}</b></div>}
        {s.flagged != null && <div><span>Flagged</span><b className={s.flagged ? "bad" : ""}>{s.flagged}</b></div>}
        {s.avg != null && <div><span>Avg time</span><b>{s.avg}</b></div>}
        {s.extra && <div><span>{s.extra[0]}</span><b>{s.extra[1]}</b></div>}
      </div>
      {s.last && (
        <div className="wf-pop-last">
          <span>Latest{s.lastInv ? ` · ${s.lastInv}` : ""}</span>
          <p>{s.last}</p>
        </div>
      )}
    </motion.div>
  );
}

export default function Workflow({ invoices, flows }) {
  const [selected, setSelected] = useState(null);
  const [crewFlows, setCrewFlows] = useState([]);
  const prevSteps = useRef({});

  // counts: downstream from invoice status, crew agents from the live trace
  const counts = {};
  const sample = {};
  for (const inv of invoices) {
    const at = AT[inv.status];
    if (at) {
      counts[at] = (counts[at] ?? 0) + 1;
      if (!sample[at] || inv.status === "paying") sample[at] = inv;
    }
    if (inv.status === "analyzing") {
      for (const s of inv.analysis ?? []) {
        if (s.state === "running") {
          counts[s.id] = (counts[s.id] ?? 0) + 1;
          sample[s.id] = inv;
        }
      }
    }
  }

  // crew step finished -> comet to the next agent(s)
  useEffect(() => {
    const fresh = [];
    for (const inv of invoices) {
      for (const s of inv.analysis ?? []) {
        const key = `${inv.id}:${s.id}`;
        if (s.state === "done" && prevSteps.current[key] && prevSteps.current[key] !== "done") {
          for (const to of CREW_NEXT[s.id] ?? []) fresh.push({ id: Math.random(), from: s.id, to });
        }
        prevSteps.current[key] = s.state;
      }
    }
    if (fresh.length) {
      setCrewFlows((f) => [...f.slice(-24), ...fresh]);
      const ids = new Set(fresh.map((x) => x.id));
      setTimeout(() => setCrewFlows((f) => f.filter((x) => !ids.has(x.id))), 1800);
    }
  }, [invoices]);

  useEffect(() => {
    const close = (e) => e.key === "Escape" && setSelected(null);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  const hotEdges = new Set();
  const hotNodes = new Set();
  for (const f of flows) {
    if (f.from === undefined && f.to === "received") hotNodes.add("inbox");
    for (const [a, b] of ROUTES[`${f.from}>${f.to}`] ?? []) {
      hotEdges.add(edgeId(a, b));
      hotNodes.add(b);
    }
    if (AT[f.to]) hotNodes.add(AT[f.to]);
  }
  for (const f of crewFlows) {
    hotEdges.add(edgeId(f.from, f.to));
    hotNodes.add(f.to);
  }

  const sum = (s) => invoices.filter((i) => i.status === s).reduce((t, i) => t + i.amount_cents, 0);
  const paid = sum("settled");
  const saved = sum("blocked");
  const inFlight = invoices.filter((i) => !["settled", "blocked"].includes(i.status)).length;
  const crewBusy = invoices.filter((i) => i.status === "analyzing").length;
  const busy = inFlight > 0 || flows.length > 0 || crewFlows.length > 0;

  const running = (id) => (sample[id] ? `On ${sample[id].invoice_number ?? sample[id].filename ?? "invoice"}` : null);
  const live = {
    inbox: counts.inbox ? `${counts.inbox} waiting` : "Listening",
    invoice_parser: running("invoice_parser") ?? "Ready",
    po_matcher: running("po_matcher") ?? "Ready",
    duplicate_detector: running("duplicate_detector") ?? "Ready",
    fraud_signal: running("fraud_signal") ?? "Watching",
    risk_scorer: running("risk_scorer") ?? "Ready",
    approval_router: running("approval_router") ?? "Ready",
    payer: sample.payer?.status === "paying" ? `Paying ${sample.payer.invoice_number}` : paid ? `${usd(paid)} paid` : "Ready",
    owner: counts.owner ? `${counts.owner} need a decision` : "All clear",
    queue: counts.queue ? `${counts.queue} in line` : "Empty",
    caller: sample.caller ? `On call · ${sample.caller.vendor?.name ?? sample.caller.vendor_name_raw}` : "Ready",
    blocked: saved ? `${usd(saved)} saved` : "Watching",
  };

  // per-node stats for the popover
  const stats = useMemo(() => {
    const out = {};
    for (const id of CREW_IDS) {
      const done = [];
      for (const inv of invoices) {
        const s = (inv.analysis ?? []).find((x) => x.id === id && x.state === "done");
        if (s) done.push({ ...s, inv });
      }
      done.sort((a, b) => b.inv.id - a.inv.id);
      const ms = done.map((d) => d.ms).filter((m) => m != null);
      const avg = ms.length ? ms.reduce((a, b) => a + b, 0) / ms.length : null;
      out[id] = {
        now: sample[id] ? `${sample[id].invoice_number ?? "invoice"}${counts[id] > 1 ? ` +${counts[id] - 1}` : ""}` : null,
        handled: done.length,
        flagged: done.filter((d) => d.status === "fail" || d.status === "warn").length,
        avg: avg == null ? null : avg < 1000 ? `${Math.round(avg)}ms` : `${(avg / 1000).toFixed(1)}s`,
        last: done[0]?.finding,
        lastInv: done[0]?.inv.invoice_number,
      };
    }
    const latestIn = (statuses) => invoices.filter((i) => statuses.includes(i.status)).sort((a, b) => b.id - a.id)[0];
    const calls = invoices.filter((i) => i.call);
    const lastCall = calls.sort((a, b) => b.id - a.id)[0];
    out.inbox = { handled: invoices.length, now: counts.inbox ? `${counts.inbox} waiting` : null };
    out.payer = {
      now: sample.payer?.status === "paying" ? `${sample.payer.invoice_number} (awaiting Link)` : null,
      handled: invoices.filter((i) => i.status === "settled").length,
      extra: ["Paid", usd(paid)],
      last: latestIn(["settled"]) && `${latestIn(["settled"]).vendor_name_raw} · ${usd(latestIn(["settled"]).amount_cents)}${latestIn(["settled"]).payment?.order_id ? ` · ${latestIn(["settled"]).payment.order_id}` : ""}`,
      lastInv: latestIn(["settled"])?.invoice_number,
    };
    out.owner = {
      now: counts.owner ? `${counts.owner} waiting on you` : null,
      extra: ["Escalated", invoices.filter((i) => i.status === "escalated").length],
      last: latestIn(["awaiting_approval", "escalated"])?.risk_reasons?.[0],
      lastInv: latestIn(["awaiting_approval", "escalated"])?.invoice_number,
    };
    out.queue = { now: counts.queue ? `${counts.queue} in line` : null, handled: calls.length };
    out.caller = {
      now: sample.caller ? `${sample.caller.vendor?.name ?? sample.caller.vendor_name_raw}` : null,
      handled: calls.filter((i) => i.call?.outcome).length,
      flagged: calls.filter((i) => i.call?.outcome === "denied").length,
      last: lastCall?.call?.summary,
      lastInv: lastCall?.invoice_number,
    };
    out.blocked = {
      handled: invoices.filter((i) => i.status === "blocked").length,
      extra: ["Saved", usd(saved)],
      last: latestIn(["blocked"]) && `${latestIn(["blocked"]).vendor_name_raw}: ${latestIn(["blocked"]).call?.summary ?? "rejected by owner"}`,
      lastInv: latestIn(["blocked"])?.invoice_number,
    };
    return out;
  }, [invoices]); // eslint-disable-line react-hooks/exhaustive-deps

  const allFlows = [
    ...flows.flatMap((f) => (ROUTES[`${f.from}>${f.to}`] ?? []).map(([a, b], i) => ({ key: `${f.id}-${i}`, a, b, delay: i * 600 }))),
    ...crewFlows.map((f) => ({ key: `c${f.id}`, a: f.from, b: f.to, delay: 0 })),
  ];

  return (
    <section className={`workflow ${busy ? "busy" : ""}`} onClick={() => setSelected(null)}>
      <div className="workflow-head">
        <div className="wf-title">
          <span className="wf-live-badge">
            <span className="pulse" /> LIVE
          </span>
          <h2>Agent workflow</h2>
          <span className="muted small">Tap any agent for details</span>
        </div>
        <div className="wf-chips">
          <span className="wf-chip"><b>{inFlight}</b> in flight</span>
          <span className="wf-chip green"><b>{invoices.filter((i) => i.status === "settled").length}</b> paid</span>
          <span className="wf-chip amber"><b>{counts.owner ?? 0}</b> awaiting</span>
          <span className="wf-chip red"><b>{counts.blocked ?? 0}</b> blocked</span>
        </div>
      </div>

      <div className="workflow-scroll">
        <div className="workflow-stage">
          <svg className="workflow-svg" viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="xMidYMid meet">
            <defs>
              <filter id="wf-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="3.5" result="b" />
                <feMerge>
                  <feMergeNode in="b" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              {Object.entries(TONE).map(([k, c]) => (
                <marker key={k} id={`wf-arrow-${k}`} viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 1 L 9 5 L 0 9 z" fill={c} opacity=".75" />
                </marker>
              ))}
            </defs>

            {/* the crew's group box */}
            <g className={`wf-group ${crewBusy ? "busy" : ""}`}>
              <rect x={GROUP.x} y={GROUP.y} width={GROUP.w} height={GROUP.h} rx="22" />
              <text x={GROUP.x + 18} y={GROUP.y + 22}>ANALYSIS CREW · 6 AGENTS{crewBusy ? ` · ${crewBusy} INVOICE${crewBusy > 1 ? "S" : ""}` : ""}</text>
            </g>

            {EDGES.map((e) => {
              const [a, , b, , kind] = e;
              const id = edgeId(a, b);
              const tone = NODES[b].tone;
              const hot = hotEdges.has(id);
              const d = edgePath(e);
              return (
                <g key={id} className={`wf-edge tone-${tone} ${hot ? "hot" : ""} ${kind ?? ""}`}>
                  <path id={id} d={d} className="rail" />
                  <path d={d} className="current" markerEnd={`url(#wf-arrow-${tone})`} />
                  {hot && <path d={d} className="glow" filter="url(#wf-glow)" />}
                  {[0, 1].map((i) => (
                    <circle key={i} r="2.4" className="ambient" fill={TONE[tone]}>
                      <animateMotion dur={kind === "retry" ? "5s" : "3s"} begin={`${i * 1.5 + (a.length % 3) * 0.4}s`} repeatCount="indefinite">
                        <mpath href={`#${id}`} />
                      </animateMotion>
                    </circle>
                  ))}
                </g>
              );
            })}

            {allFlows.map((f) => (
              <Comet key={f.key} d={edgePath(findEdge(f.a, f.b))} delay={f.delay} color={TONE[NODES[f.b].tone]} />
            ))}

            {Object.entries(NODES).map(([id, n]) => (
              <Node key={id} id={id} n={n} count={counts[id] ?? 0} hot={hotNodes.has(id)} live={live[id]}
                selected={selected === id} onSelect={(x) => setSelected((cur) => (cur === x ? null : x))} />
            ))}
          </svg>

          <AnimatePresence>
            {selected && <Popover key={selected} id={selected} stats={stats} onClose={() => setSelected(null)} />}
          </AnimatePresence>
        </div>
      </div>
    </section>
  );
}
