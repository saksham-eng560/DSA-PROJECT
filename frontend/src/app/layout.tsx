import type { Metadata, Viewport } from "next";
import { Dela_Gothic_One, JetBrains_Mono, Space_Grotesk } from "next/font/google";
import { MotionProvider } from "@/components/motion-provider";
import { ThemeProvider } from "@/components/theme-provider";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import "./globals.css";

const display = Dela_Gothic_One({ weight: "400", subsets: ["latin"], variable: "--font-display", display: "swap" });
const sans = Space_Grotesk({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: { default: "AutoApply AI", template: "%s · AutoApply AI" },
  description: "Swipe right on internships — your agent tailors, fills and applies.",
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }, { url: "/favicon.ico", sizes: "any" }],
    apple: "/apple-touch-icon.png",
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3eee9" },
    { media: "(prefers-color-scheme: dark)", color: "#1d1b1b" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={cn(display.variable, sans.variable, mono.variable)}>
      <body className="font-sans">
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} storageKey="theme" disableTransitionOnChange>
          <TooltipProvider delayDuration={200}>
            <ToastProvider><MotionProvider>{children}</MotionProvider></ToastProvider>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
