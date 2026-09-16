import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

/* Money, counts and timers are set in a monospace face so a live ticker never
   reflows as its digits change, and a column of rupee figures scans cleanly
   down the page. */
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono-numeric",
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "FactoryManager AI",
    template: "%s · FactoryManager AI",
  },
  description:
    "The AI Plant Manager for MSME manufacturing. Every loss visible in rupees the moment it happens — and the one action that recovers the most.",
  // Installable on the bench tablet: full screen, its own icon, and it opens
  // even with no connection (public/sw.js keeps the shell).
  manifest: "/manifest.webmanifest",
  applicationName: "FactoryManager AI",
  appleWebApp: { capable: true, title: "FactoryManager", statusBarStyle: "default" },
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/icons/icon-192.png", sizes: "192x192" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Shared tablets live on a bench. Pinch-zoom stays available for
  // accessibility, but the layout is built so nobody needs it.
  maximumScale: 5,
  // Matches the manifest's theme_color and the white canvas (--bg).
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
