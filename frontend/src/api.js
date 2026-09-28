// Backend client. Talks to Zubair's FastAPI through the Cloudflare Tunnel (MASTER.md 4.2 / 4.3).
// With no VITE_API_BASE set, falls back to the mock backend so the board runs standalone.
import { createMockClient } from "./mock.js";

const API = import.meta.env.VITE_API_BASE; // Cloudflare Tunnel URL from Zubair
const WS = import.meta.env.VITE_WS_URL; // same host, wss://.../ws

export const USING_MOCK = !API;

async function json(res) {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const text = await res.text(); // approve/reject/reset may return an empty body
  return text ? JSON.parse(text) : null;
}

function createHttpClient({ onInvoice, onActivity, onConnection }) {
  let socket;
  let closed = false;
  let retry;

  function connect() {
    socket = new WebSocket(WS);
    socket.onopen = () => onConnection(true);
    socket.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "invoice.updated") onInvoice(msg.invoice);
      else if (msg.type === "activity") onActivity(msg);
    };
    socket.onclose = () => {
      onConnection(false);
      if (!closed) retry = setTimeout(connect, 2000); // reconnect every 2s if it drops
    };
    socket.onerror = () => socket.close();
  }
  connect();

  return {
    loadInvoices: () => fetch(`${API}/invoices`).then(json),
    loadEvents: () => fetch(`${API}/events`).then(json).catch(() => []),
    upload(files) {
      const body = new FormData();
      for (const f of files) body.append("files", f);
      return fetch(`${API}/invoices/upload`, { method: "POST", body }).then(json);
    },
    approve: (id) => fetch(`${API}/invoices/${id}/approve`, { method: "POST" }).then(json),
    reject: (id) => fetch(`${API}/invoices/${id}/reject`, { method: "POST" }).then(json),
    reset: () => fetch(`${API}/demo/reset`, { method: "POST" }).then(json),
    close() {
      closed = true;
      clearTimeout(retry);
      socket?.close();
    },
  };
}

export function createClient(handlers) {
  return USING_MOCK ? createMockClient(handlers) : createHttpClient(handlers);
}
