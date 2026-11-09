import { RootFade } from "@/components/motion-provider";

export default function RootTemplate({ children }: { children: React.ReactNode }) {
  return <RootFade>{children}</RootFade>;
}
