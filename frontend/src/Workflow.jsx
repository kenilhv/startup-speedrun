// Live agent workflow. One node per agent/step, lit from the invoices' current statuses.
// Every edge always carries a slow ambient flow; each status change the backend reports
// fires a glowing comet along the exact route the invoice took.
import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";

const VW = 1200;
const VH = 350;
const NW = 170;
const NH = 62;

const NODES = {
  inbox: { x: 20, y: 129, icon: "📥", name: "Intake", tone: "green" },
  claude: { x: 225, y: 129, icon: "🧠", name: "Analysis Crew", tone: "mint" },
  rules: { x: 430, y: 129, icon: "⚖️", name: "Risk Rules", tone: "green" },
  payer: { x: 1010, y: 30, icon: "💳", name: "Brainbase Payer", tone: "green" },
  owner: { x: 845, y: 129, icon: "👤", name: "Owner · Slack", tone: "amber" },
  queue: { x: 640, y: 229, icon: "🚦", name: "Call Queue", tone: "amber" },
  caller: { x: 845, y: 229, icon: "📞", name: "Brainbase Caller", tone: "blue" },
  blocked: { x: 1010, y: 229, icon: "🛡️", name: "Fraud Shield", tone: "red" },
};

// [from, fromPort, to, toPort, kind?]
const EDGES = [
  ["inbox", "r", "claude", "l"],
  ["claude", "r", "rules", "l"],
  ["rules", "r", "payer", "l"],
  ["rules", "b", "queue", "l"],
  ["queue", "r", "caller", "l"],
  ["caller", "r", "blocked", "l"],
  ["caller", "t", "owner", "b"],
  ["owner", "r", "payer", "b"],
  ["owner", "r", "blocked", "t"],
  ["caller", "b", "queue", "b", "retry"],
];

// status change -> the edges an invoice travels
const ROUTES = {
  "received>analyzing": [["inbox", "claude"]],
  "analyzing>settled": [["claude", "rules"], ["rules", "payer"]],
  "analyzing>paying": [["claude", "rules"], ["rules", "payer"]],
  "awaiting_approval>paying": [["owner", "payer"]],
  "analyzing>flagged": [["claude", "rules"], ["rules", "queue"]],
  "analyzing>awaiting_approval": [["claude", "rules"]],
  "analyzing>escalated": [["claude", "rules"]],
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

// where an invoice "sits" for each status
const AT = {
  received: "inbox",
  analyzing: "claude",
  flagged: "queue",
  calling: "caller",
  awaiting_approval: "owner",
  escalated: "owner",
  paying: "payer",
  settled: "payer",
  blocked: "blocked",
};

const TONE = { green: "#16a34a", mint: "#10b981", amber: "#d97706", blue: "#0284c7", red: "#dc2626" };

function port(id, side) {
  const n = NODES[id];
  if (side === "l") return [n.x, n.y + NH / 2, -1, 0];
  if (side === "r") return [n.x + NW, n.y + NH / 2, 1, 0];
  if (side === "t") return [n.x + NW / 2, n.y, 0, -1];
  return [n.x + NW / 2, n.y + NH, 0, 1];
}

function edgePath([a, pa, b, pb, kind]) {
  const [x1, y1, dx1, dy1] = port(a, pa);
  const [x2, y2, dx2, dy2] = port(b, pb);
  const k = kind === "retry" ? 46 : Math.max(50, Math.hypot(x2 - x1, y2 - y1) * 0.42);
  return `M ${x1} ${y1} C ${x1 + dx1 * k} ${y1 + dy1 * k}, ${x2 + dx2 * k} ${y2 + dy2 * k}, ${x2} ${y2}`;
}

const edgeId = (a, b) => `wf-${a}-${b}`;
const findEdge = (a, b) => EDGES.find((e) => e[0] === a && e[2] === b);

/* A glowing comet (head + fading tail) that follows one path once. */
function Comet({ d, delay, color }) {
  const path = useRef(null);
  const dots = useRef([]);
  useEffect(() => {
    const el = path.current;
    const len = el.getTotalLength();
    const t0 = performance.now() + delay;
    const dur = 950;
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
    <g filter="url(#wf-glow)">
      <path ref={path} d={d} fill="none" stroke="none" />
      {Array.from({ length: 7 }, (_, i) => (
        <circle key={i} ref={(el) => (dots.current[i] = el)} r={i === 0 ? 6.5 : 5.5 - i * 0.6} fill={color} opacity="0" />
      ))}
    </g>
  );
}

function Node({ id, n, count, hot, live, sub }) {
  return (
    <foreignObject x={n.x - 14} y={n.y - 14} width={NW + 28} height={NH + 28} style={{ overflow: "visible" }}>
      <div className="wfn-wrap">
        <motion.div
          className={`wfn tone-${n.tone} ${count > 0 ? "active" : ""} ${hot ? "hot" : ""}`}
          animate={hot ? { scale: [1, 1.08, 1], y: [0, -3, 0] } : { scale: 1, y: 0 }}
          transition={{ duration: 0.55, ease: [0.34, 1.56, 0.64, 1] }}
        >
          <AnimatePresence>
            {hot && (
              <motion.span
                key="ripple"
                className="wfn-ripple"
                initial={{ opacity: 0.55, scale: 1 }}
                animate={{ opacity: 0, scale: 1.35 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.9, ease: "easeOut" }}
              />
            )}
          </AnimatePresence>
          <div className="wfn-icon">
            <span>{n.icon}</span>
            {id === "claude" && count > 0 && <span className="wfn-orbit" />}
            {id === "caller" && count > 0 && <span className="wfn-radar" />}
          </div>
          <div className="wfn-text">
            <div className="wfn-name">{n.name}</div>
            <div className="wfn-live">
              <span className="wfn-dot" />
              {live}
            </div>
            {sub && <div className="wfn-sub">{sub}</div>}
          </div>
          <AnimatePresence>
            {count > 0 && (
              <motion.span
                key={count}
                className="wfn-count"
                initial={{ scale: 0, rotate: -30 }}
                animate={{ scale: 1, rotate: 0 }}
                exit={{ scale: 0 }}
                transition={{ type: "spring", stiffness: 600, damping: 15 }}
              >
                {count}
              </motion.span>
            )}
          </AnimatePresence>
        </motion.div>
      </div>
    </foreignObject>
  );
}

export default function Workflow({ invoices, flows }) {
  const counts = {};
  const sample = {};
  for (const inv of invoices) {
    const at = AT[inv.status];
    if (!at) continue;
    counts[at] = (counts[at] ?? 0) + 1;
    if (!sample[at] || inv.status === "paying") sample[at] = inv;
  }

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

  const usd = (c) => `$${(c / 100).toLocaleString("en-US")}`;
  const sum = (s) => invoices.filter((i) => i.status === s).reduce((t, i) => t + i.amount_cents, 0);
  const paid = sum("settled");
  const saved = sum("blocked");
  const inFlight = invoices.filter((i) => !["settled", "blocked"].includes(i.status)).length;
  const busy = inFlight > 0 || flows.length > 0;

  const live = {
    inbox: counts.inbox ? `${counts.inbox} waiting` : "Listening",
    claude: sample.claude
      ? (sample.claude.analysis ?? []).find((s) => s.state === "running")?.name ?? `Reading ${sample.claude.invoice_number ?? "invoice"}`
      : "Ready",
    rules: hotNodes.has("rules") ? "Scoring risk…" : "Ready",
    payer: sample.payer?.status === "paying" ? `Paying ${sample.payer.invoice_number} via Link` : paid ? `${usd(paid)} paid` : "Ready",
    owner: counts.owner ? `${counts.owner} need a decision` : "All clear",
    queue: counts.queue ? `${counts.queue} in line` : "Empty",
    caller: sample.caller ? `On call · ${sample.caller.vendor?.name ?? sample.caller.vendor_name_raw}` : "Ready",
    blocked: saved ? `${usd(saved)} saved` : "Watching",
  };
  const sub = {
    claude: "6 agents · Claude",
    rules: "checks vendor file",
    caller: "calls number on file",
    queue: "one call at a time",
    payer: "pays via Link wallet",
  };

  return (
    <section className={`workflow ${busy ? "busy" : ""}`}>
      <div className="workflow-head">
        <div className="wf-title">
          <span className="wf-live-badge">
            <span className="pulse" /> LIVE
          </span>
          <h2>Agent workflow</h2>
        </div>
        <div className="wf-chips">
          <span className="wf-chip">
            <b>{inFlight}</b> in flight
          </span>
          <span className="wf-chip green">
            <b>{invoices.filter((i) => i.status === "settled").length}</b> paid
          </span>
          <span className="wf-chip amber">
            <b>{counts.owner ?? 0}</b> awaiting
          </span>
          <span className="wf-chip red">
            <b>{counts.blocked ?? 0}</b> blocked
          </span>
        </div>
      </div>

      <div className="workflow-scroll">
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

          {/* edges: soft rail + always-flowing current + hot glow */}
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
                {/* ambient particles, always moving so the graph never looks dead */}
                {[0, 1].map((i) => (
                  <circle key={i} r="2.6" className="ambient" fill={TONE[tone]}>
                    <animateMotion dur={kind === "retry" ? "5s" : "3.2s"} begin={`${i * 1.6 + (a.length % 3) * 0.4}s`} repeatCount="indefinite" rotate="auto">
                      <mpath href={`#${id}`} />
                    </animateMotion>
                  </circle>
                ))}
              </g>
            );
          })}

          {flows.flatMap((f) =>
            (ROUTES[`${f.from}>${f.to}`] ?? []).map(([a, b], i) => (
              <Comet key={`${f.id}-${i}`} d={edgePath(findEdge(a, b))} delay={i * 650} color={TONE[NODES[b].tone]} />
            ))
          )}

          {Object.entries(NODES).map(([id, n]) => (
            <Node key={id} id={id} n={n} count={counts[id] ?? 0} hot={hotNodes.has(id)} live={live[id]} sub={sub[id]} />
          ))}
        </svg>
      </div>
    </section>
  );
}
