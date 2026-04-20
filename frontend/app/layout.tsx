import type { Metadata } from "next";
import { AuthProvider } from "../lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Atlas Trade OS",
  description: "AI-Native Commodity Trading Operating System",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
