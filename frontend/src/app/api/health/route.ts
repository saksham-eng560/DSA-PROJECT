import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/** BFF health check: reports the dashboard and the backend it proxies to. */
export async function GET() {
  const backend = process.env.BACKEND_URL || "http://localhost:8000";
  try {
    const res = await fetch(`${backend}/health/ready`, { cache: "no-store", signal: AbortSignal.timeout(5000) });
    const body = await res.json();
    return NextResponse.json({ status: res.ok ? "ok" : "degraded", frontend: "ok", backend: body }, { status: res.ok ? 200 : 503 });
  } catch (err) {
    return NextResponse.json({ status: "degraded", frontend: "ok", backend: { error: String(err) } }, { status: 503 });
  }
}
