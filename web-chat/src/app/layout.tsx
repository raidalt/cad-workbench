import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "cad chat",
  description: "Chat with the model that edits model.py",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "dark" as const,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
