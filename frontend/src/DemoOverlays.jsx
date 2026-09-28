// Overlays for the scripted simulated demo: narration captions, chapter bar,
// flying PDFs, a live phone call with transcript, and a Slack message.
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

const usd = (c) => `$${(c / 100).toLocaleString("en-US")}`;
const spring = { type: "spring", stiffness: 300, damping: 28 };

export function Caption({ caption }) {
  return (
    <div className="caption-wrap">
      <AnimatePresence mode="wait">
        {caption && (
          <motion.div
            key={caption.title}
            className="caption"
            initial={{ opacity: 0, y: 30, filter: "blur(8px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -20, filter: "blur(8px)" }}
            transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="caption-title">
              {caption.title.split(" ").map((w, i) => (
                <motion.span
                  key={i}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.05, duration: 0.3 }}
                >
                  {w}{" "}
                </motion.span>
              ))}
            </div>
            {caption.sub && (
              <motion.div className="caption-sub" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 }}>
                {caption.sub}
              </motion.div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ChapterBar({ chapters, active }) {
  return (
    <AnimatePresence>
      {active >= 0 && (
        <motion.div className="chapters glass" initial={{ y: -60, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -60, opacity: 0 }} transition={spring}>
          {chapters.map((c, i) => (
            <div key={c} className={`chapter ${i < active ? "done" : ""} ${i === active ? "active" : ""}`}>
              <span className="chapter-dot">{i < active ? "✓" : i + 1}</span>
              <span className="chapter-name">{c}</span>
            </div>
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function FlyIn({ fly }) {
  const [target, setTarget] = useState(null);
  useEffect(() => {
    if (!fly) return;
    const r = document.querySelector(".drop")?.getBoundingClientRect();
    setTarget(r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : { x: window.innerWidth / 2, y: 200 });
  }, [fly]);
  if (!fly || !target) return null;
  return (
    <div className="fly-layer" key={fly.id}>
      {Array.from({ length: fly.n }, (_, i) => (
        <motion.div
          key={i}
          className="fly-pdf"
          initial={{ x: -120, y: window.innerHeight * (0.3 + i * 0.1), rotate: -40, opacity: 0, scale: 1.4 }}
          animate={{ x: target.x - 22 + (i - 2) * 14, y: target.y - 28, rotate: (i - 2) * 8, opacity: [0, 1, 1, 0], scale: [1.4, 1.1, 0.6] }}
          transition={{ duration: 1.2, delay: i * 0.1, ease: [0.3, 0.7, 0.4, 1] }}
        >
          <span>PDF</span>
        </motion.div>
      ))}
    </div>
  );
}

function Typed({ text }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
    const t = setInterval(() => setN((v) => (v >= text.length ? v : v + 1)), 22);
    return () => clearInterval(t);
  }, [text]);
  return <>{text.slice(0, n)}</>;
}

function Elapsed({ running }) {
  const start = useRef(Date.now());
  const [s, setS] = useState(0);
  useEffect(() => {
    if (!running) return;
    start.current = Date.now();
    const t = setInterval(() => setS(Math.floor((Date.now() - start.current) / 1000)), 250);
    return () => clearInterval(t);
  }, [running]);
  return <>{`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`}</>;
}

export function PhoneOverlay({ phone }) {
  const scroller = useRef(null);
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [phone?.lines?.length]);

  const live = phone?.state === "live";
  return (
    <AnimatePresence>
      {phone && (
        <motion.div
          className={`phone-overlay state-${phone.state}`}
          initial={{ x: 420, rotate: 8, opacity: 0 }}
          animate={phone.state === "ringing" ? { x: 0, rotate: [0, -2, 2, -2, 2, 0], opacity: 1 } : { x: 0, rotate: 0, opacity: 1 }}
          exit={{ x: 420, rotate: 8, opacity: 0 }}
          transition={phone.state === "ringing" ? { x: spring, rotate: { repeat: Infinity, duration: 0.6 } } : spring}
        >
          <div className="phone-notch" />
          <div className="phone-head">
            <div className="phone-avatar">{phone.who[0]}</div>
            <div className="phone-who">{phone.who}</div>
            <div className="phone-num">{phone.number ?? "number on file"}</div>
            <div className="phone-status">
              {phone.state === "ringing" && <span className="ringing">Ringing…</span>}
              {live && <span className="live-call"><span className="rec" /> Live · <Elapsed running /></span>}
              {phone.state === "denied" && <span className="verdict bad">Vendor denied the change</span>}
              {phone.state === "confirmed" && <span className="verdict ok">Vendor confirmed</span>}
              {phone.state === "ended" && <span className="verdict">Call ended</span>}
            </div>
          </div>
          <div className="transcript" ref={scroller}>
            <AnimatePresence initial={false}>
              {phone.lines.map((l, i) => (
                <motion.div
                  key={l.id}
                  className={`bubble ${l.from}`}
                  initial={{ opacity: 0, y: 16, scale: 0.9 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  transition={spring}
                >
                  <div className="bubble-from">{l.from === "agent" ? "PayCrew agent" : phone.who}</div>
                  {phone.typing && i === phone.lines.length - 1 && live ? <Typed text={l.text} /> : l.text}
                  {l.partial && <span className="caret" />}
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          <div className="phone-foot">
            <span className="wave big" aria-hidden>
              {Array.from({ length: 14 }, (_, i) => (
                <i key={i} style={{ animationDelay: `${(i % 7) * 0.08}s`, animationPlayState: live ? "running" : "paused" }} />
              ))}
            </span>
            <span className="powered">{phone.poweredBy ?? "PayCrew voice agent"}</span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function SlackCard({ slack }) {
  const inv = slack?.invoice;
  return (
    <AnimatePresence>
      {slack && (
        <motion.div
          className="slack"
          initial={{ y: -140, opacity: 0, scale: 0.9 }}
          animate={{ y: 0, opacity: 1, scale: 1 }}
          exit={{ y: -140, opacity: 0 }}
          transition={spring}
        >
          <div className="slack-bar">
            <span className="slack-logo">#</span> finance-alerts
          </div>
          <div className="slack-msg">
            <div className="slack-avatar">◆</div>
            <div className="slack-body">
              <div className="slack-name">
                PayCrew <span className="slack-app">APP</span> <span className="slack-time">now</span>
              </div>
              {slack.kind === "alert" ? (
                <>
                  <div className="slack-text">
                    🛡️ <b>Fraud blocked:</b> {inv.vendor_name_raw} denied changing bank details on <b>{inv.invoice_number}</b>.
                  </div>
                  <div className="slack-attach bad">
                    {usd(inv.amount_cents)} to ••••{inv.bank_last4_claimed} was <b>not paid</b>.
                  </div>
                </>
              ) : (
                <>
                  <div className="slack-text">
                    ✅ <b>{inv.vendor_name_raw}</b> confirmed new bank ••••{inv.bank_last4_claimed} by phone. Pay <b>{usd(inv.amount_cents)}</b> for {inv.invoice_number}?
                  </div>
                  <div className="slack-buttons">
                    <motion.span
                      className={`slack-btn primary ${slack.pressed ? "pressed" : ""}`}
                      animate={slack.pressed ? { scale: [1, 0.9, 1.05, 1] } : { boxShadow: ["0 0 0 0 rgba(34,197,94,.6)", "0 0 0 10px rgba(34,197,94,0)"] }}
                      transition={slack.pressed ? { duration: 0.4 } : { repeat: Infinity, duration: 1.2 }}
                    >
                      {slack.pressed ? "✓ Approved" : "Approve"}
                    </motion.span>
                    <span className="slack-btn">Reject</span>
                  </div>
                  {!slack.pressed && (
                    <motion.div
                      className="cursor"
                      initial={{ x: 220, y: 90, opacity: 0 }}
                      animate={{ x: 30, y: 8, opacity: 1 }}
                      transition={{ delay: 1.2, duration: 1.4, ease: [0.4, 0, 0.2, 1] }}
                    >
                      👆
                    </motion.div>
                  )}
                </>
              )}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
