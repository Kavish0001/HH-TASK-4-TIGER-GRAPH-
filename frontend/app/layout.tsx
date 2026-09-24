import type { Metadata } from "next";
import type { ReactNode } from "react";
import { TopBar } from "@/components/shell";
import { Backdrop } from "@/components/backdrop/backdrop";
import { inter, mono } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "HHGOA fraud desk",
  description: "Agentic fraud investigation on TigerGraph",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.variable + " " + mono.variable}>
      <body>
        <Backdrop />
        <div className="relative z-10">
          <TopBar />
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
