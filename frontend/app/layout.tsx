import type { Metadata } from "next";
import type { ReactNode } from "react";
import { TopBar } from "@/components/shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "HHGOA fraud desk",
  description: "Agentic fraud investigation on TigerGraph",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <TopBar />
        <main>{children}</main>
      </body>
    </html>
  );
}
