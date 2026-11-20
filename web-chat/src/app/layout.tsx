import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "CAD Workspace",
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
      <head>
        <link rel="stylesheet" href="/static/style.css" />
        <script type="importmap" dangerouslySetInnerHTML={{ __html: JSON.stringify({
          imports: { three: "/static/vendor/three.module.min.js", "three/addons/": "/static/vendor/",
            "three/webgpu": "/static/vendor/three.webgpu.min.js" },
        }) }} />
      </head>
      <body className="cad-native-ui">{children}</body>
    </html>
  );
}
