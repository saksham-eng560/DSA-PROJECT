"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { ArrowUpRight } from "lucide-react";
import { BrushHeadline, Logo, TunnelGrid } from "@/components/brand";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, fetcher, post } from "@/lib/api-client";
import { cn } from "@/lib/utils";

const ERRORS: Record<string, string> = {
  google_oauth_failed: "Google sign-in failed. Please try again.",
  registration_disabled: "Registration is disabled on this server.",
  session_expired: "Your session expired — sign in again.",
};

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/dashboard";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { data: config } = useSWR<{ google_enabled: boolean; registration_enabled: boolean }>("/auth/config", fetcher);

  useEffect(() => {
    const e = params.get("error");
    if (e) setError(ERRORS[e] || e);
  }, [params]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      if (mode === "login") await post("/auth/login", { email, password });
      else await post("/auth/register", { email, password, full_name: fullName });
      router.replace(mode === "register" ? "/dashboard?welcome=1" : next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="noise grid min-h-screen bg-background lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-line/60 p-10 lg:flex">
        <TunnelGrid className="absolute inset-0 opacity-60" />
        <Logo className="relative" />
        <div className="relative">
          <BrushHeadline lines={mode === "login" ? ["Welcome", "back.", "Your deck", "is waiting"] : ["Build your", "agent.", "Then just", "swipe"]}
            className="text-[clamp(2.4rem,4.4vw,4.2rem)]" />
          <p className="mt-8 max-w-md text-foreground/75">
            Thousands of internships, one swipe each. Keep the ones you like — AutoApply tailors, fills and applies.
          </p>
        </div>
        <p className="label-caps relative text-muted-foreground">Nothing is sent for a job you didn&apos;t keep</p>
      </section>
      <section className="flex flex-col">
        <header className="flex h-20 items-center justify-between border-b border-line/60 px-6 sm:px-10">
          <Logo className="lg:invisible" />
          <Link href={mode === "login" ? "/register" : "/login"} className={buttonVariants({ variant: "outline", size: "sm" })}>
            {mode === "login" ? "Create account" : "Sign in"}
          </Link>
        </header>
        <div className="flex flex-1 items-center justify-center p-6 sm:p-10">
          <div className="w-full max-w-sm">
            <p className="label-caps text-primary">{mode === "login" ? "Sign in" : "Get started"}</p>
            <h1 className="display mt-3 text-3xl">{mode === "login" ? "Welcome back" : "Create your account"}</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {mode === "login" ? "Sign in to your AutoApply dashboard." : "Set up your internship agent in two minutes."}
            </p>
            <form onSubmit={submit} className="mt-8 space-y-5">
              {mode === "register" && (
                <div className="space-y-2">
                  <Label htmlFor="name" className="label-caps">Full name</Label>
                  <Input id="name" value={fullName} onChange={(e) => setFullName(e.target.value)} required autoComplete="name" />
                </div>
              )}
              <div className="space-y-2">
                <Label htmlFor="email" className="label-caps">Email</Label>
                <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password" className="label-caps">Password</Label>
                <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                  minLength={mode === "register" ? 8 : undefined} autoComplete={mode === "login" ? "current-password" : "new-password"} />
              </div>
              {error && <p role="alert" className="border border-primary/60 bg-primary/10 p-3 text-sm text-primary">{error}</p>}
              <Button type="submit" size="lg" className="w-full" loading={loading}>
                {mode === "login" ? "Sign in" : "Create account"} <ArrowUpRight />
              </Button>
            </form>
            {config?.google_enabled && (
              <>
                <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
                  <div className="h-px flex-1 bg-border" /> or <div className="h-px flex-1 bg-border" />
                </div>
                <a href={`/api/v1/auth/google/login?next=${encodeURIComponent(next)}`} className={cn(buttonVariants({ variant: "outline", size: "lg" }), "w-full")}>
                  <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden><path fill="currentColor" d="M12 10.2v3.9h5.5c-.2 1.3-1.6 3.9-5.5 3.9-3.3 0-6-2.7-6-6.1s2.7-6.1 6-6.1c1.9 0 3.1.8 3.8 1.5l2.6-2.5C16.8 3.3 14.6 2.4 12 2.4 6.7 2.4 2.4 6.7 2.4 12s4.3 9.6 9.6 9.6c5.5 0 9.2-3.9 9.2-9.4 0-.6-.1-1.1-.2-1.6H12z"/></svg>
                  Continue with Google
                </a>
              </>
            )}
            <p className="mt-6 text-sm text-muted-foreground">
              {mode === "login" ? (
                <>No account? <Link href="/register" className="font-semibold text-foreground underline-offset-4 hover:text-primary hover:underline">Create one</Link></>
              ) : (
                <>Already registered? <Link href="/login" className="font-semibold text-foreground underline-offset-4 hover:text-primary hover:underline">Sign in</Link></>
              )}
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
