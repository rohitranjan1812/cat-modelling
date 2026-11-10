/** WebSocket client: reconnects with backoff and dispatches server events into the store. */
import { apiKey, BASE, type Alert } from "../api/client";
import { useStore } from "./store";
import { fmtMoney } from "../lib/format";

let socket: WebSocket | null = null;
let retry = 0;

function url(): string {
  const base = BASE || window.location.origin;
  const u = new URL("/ws", base);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  const k = apiKey();
  if (k) u.searchParams.set("api_key", k);
  return u.toString();
}

type Msg = { topic: string; data: Record<string, unknown> };

function handle(msg: Msg): void {
  const s = useStore.getState();
  switch (msg.topic) {
    case "run.progress": {
      const d = msg.data as { id: string; progress: number; message: string };
      useStore.setState({ progress: { ...s.progress, [d.id]: { progress: d.progress, message: d.message } } });
      break;
    }
    case "run.status": {
      const d = msg.data as { id: string; status: string; name: string; error?: string };
      s.bump("runs");
      if (d.status === "done") s.toast({ tone: "good", title: "Analysis finished", body: d.name });
      if (d.status === "failed") s.toast({ tone: "critical", title: "Analysis failed", body: d.error?.split("\n")[0] });
      break;
    }
    case "portfolio.created":
      s.bump("portfolios");
      break;
    case "live.event":
    case "live.refresh":
      s.bump("live");
      break;
    case "alert": {
      const a = msg.data as unknown as Alert;
      useStore.setState({ alerts: [a, ...s.alerts].slice(0, 50) });
      s.bump("alerts");
      s.toast({
        tone: "critical",
        title: `Alert: ${a.rule_name}`,
        body: `${a.event_name} — ${a.metric} ${fmtMoney(a.value)} ≥ ${fmtMoney(a.threshold)}`,
      });
      break;
    }
  }
}

export function connect(): void {
  useStore.setState({ ws: "connecting" });
  socket = new WebSocket(url());
  socket.onopen = () => {
    retry = 0;
    useStore.setState({ ws: "open" });
  };
  socket.onmessage = (e) => {
    try {
      handle(JSON.parse(e.data as string) as Msg);
    } catch {
      /* ignore malformed */
    }
  };
  socket.onclose = () => {
    useStore.setState({ ws: "closed" });
    const delay = Math.min(30000, 1000 * 2 ** retry++);
    setTimeout(connect, delay);
  };
}
