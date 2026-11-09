import { PageTransition } from "@/components/motion";

/** Re-mounted on every dashboard navigation: each page fades up while a red bar sweeps the top. */
export default function DashboardTemplate({ children }: { children: React.ReactNode }) {
  return <PageTransition>{children}</PageTransition>;
}
