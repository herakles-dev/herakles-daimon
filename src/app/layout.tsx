import type { Metadata, Viewport } from "next";
import "./globals.css";
import InstallBanner from "@/components/InstallBanner";
import OfflineIndicator from "@/components/OfflineIndicator";

export const metadata: Metadata = {
  title: "Herakles Play",
  description: "AI-curated mood-responsive video and music",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Play",
  },
};

export const viewport: Viewport = {
  themeColor: "#0a0a0a",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-surface text-white antialiased">
        <InstallBanner />
        <OfflineIndicator />
        {children}
      </body>
    </html>
  );
}
