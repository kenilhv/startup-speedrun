// Visual effects for big demo moments: confetti bursts and red "fraud" sparks.
import confetti from "canvas-confetti";

const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

function originOf(el) {
  if (!el) return { x: 0.5, y: 0.4 };
  const r = el.getBoundingClientRect();
  return { x: (r.left + r.width / 2) / window.innerWidth, y: (r.top + r.height / 2) / window.innerHeight };
}

export function paidBurst(el) {
  if (reduced()) return;
  const origin = originOf(el);
  confetti({
    particleCount: 70,
    spread: 75,
    startVelocity: 32,
    scalar: 0.9,
    ticks: 140,
    origin,
    colors: ["#16a34a", "#22c55e", "#86efac", "#10b981", "#facc15"],
  });
}

export function fraudBurst(el) {
  if (reduced()) return;
  const origin = originOf(el);
  confetti({
    particleCount: 90,
    spread: 360,
    startVelocity: 26,
    gravity: 0.6,
    scalar: 0.8,
    ticks: 120,
    shapes: ["square"],
    origin,
    colors: ["#ef4444", "#f87171", "#7f1d1d", "#fca5a5"],
  });
}

export function bigWin() {
  if (reduced()) return;
  const end = Date.now() + 900;
  (function frame() {
    confetti({ particleCount: 6, angle: 60, spread: 60, origin: { x: 0, y: 0.8 }, colors: ["#16a34a", "#22c55e", "#84cc16"] });
    confetti({ particleCount: 6, angle: 120, spread: 60, origin: { x: 1, y: 0.8 }, colors: ["#16a34a", "#22c55e", "#84cc16"] });
    if (Date.now() < end) requestAnimationFrame(frame);
  })();
}
