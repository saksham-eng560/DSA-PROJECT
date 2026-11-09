"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api-client";

export interface ServerEvent {
  type: string;
  data: Record<string, unknown>;
}

function wsUrl(): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  if (configured) return configured;
  const { protocol, hostname, port } = window.location;
  const scheme = protocol === "https:" ? "wss" : "ws";
  // Local dev: API on :8000 next to the dashboard on :3000. Behind a reverse proxy: same host.
  const host = port === "3000" ? `${hostname}:8000` : window.location.host;
  return `${scheme}://${host}/api/v1/ws`;
}

/**
 * Real-time events from the agent (notifications, application status changes, run updates).
 * Reconnects with exponential backoff; callers should also poll as a fallback.
 */
export function useWebSocket(onEvent: (event: ServerEvent) => void) {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ping: ReturnType<typeof setInterval> | undefined;

    const connect = async () => {
      if (closed) return;
      try {
        const { token } = await api<{ token: string }>("/auth/ws-token");
        if (closed) return; // unmounted while the token was on its way (React dev mode mounts twice): no second socket
        socket = new WebSocket(`${wsUrl()}?token=${encodeURIComponent(token)}`);
      } catch {
        schedule();
        return;
      }
      socket.onopen = () => {
        attempt = 0;
        setConnected(true);
        ping = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("ping"), 25000);
      };
      socket.onmessage = (msg) => {
        try {
          handler.current(JSON.parse(msg.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (ping) clearInterval(ping);
        schedule();
      };
      socket.onerror = () => socket?.close();
    };

    const schedule = () => {
      if (closed) return;
      attempt += 1;
      timer = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(attempt, 5)));
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      if (ping) clearInterval(ping);
      socket?.close();
    };
  }, []);

  return connected;
}
