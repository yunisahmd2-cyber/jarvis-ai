export type MessageHandler = (msg: Record<string, unknown>) => void;
export type StatusHandler = (status: "connecting" | "connected" | "disconnected") => void;

export interface JarvisSocket {
  send(data: string): void;
  sendJson(data: Record<string, unknown>): void;
  onMessage(handler: MessageHandler): void;
  onStatusChange(handler: StatusHandler): void;
  close(): void;
  isConnected(): boolean;
}

export function createSocket(url: string): JarvisSocket {
  let ws: WebSocket | null = null;
  let handlers: MessageHandler[] = [];
  let statusHandlers: StatusHandler[] = [];
  let reconnectDelay = 1000;
  let closed = false;
  let connected = false;

  function emitStatus(status: "connecting" | "connected" | "disconnected") {
    for (const handler of statusHandlers) handler(status);
  }

  function connect() {
    if (closed) return;

    emitStatus("connecting");
    ws = new WebSocket(url);

    ws.onopen = () => {
      connected = true;
      reconnectDelay = 1000;
      emitStatus("connected");
      console.log("[ws] connected");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        for (const h of handlers) h(msg);
      } catch {
        console.warn("[ws] bad message", event.data);
      }
    };

    ws.onclose = () => {
      connected = false;
      emitStatus("disconnected");
      if (!closed) {
        console.log(`[ws] reconnecting in ${reconnectDelay}ms`);
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
      }
    };

    ws.onerror = (err) => {
      console.error("[ws] error", err);
      ws?.close();
    };
  }

  connect();

  return {
    send(data: string) {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    },
    sendJson(data) {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
      }
    },
    onMessage(handler) {
      handlers.push(handler);
    },
    onStatusChange(handler) {
      statusHandlers.push(handler);
    },
    close() {
      closed = true;
      ws?.close();
    },
    isConnected() {
      return connected;
    },
  };
}
