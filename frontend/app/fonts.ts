import localFont from "next/font/local";

// One type system for the whole app: Inter for UI and headings, JetBrains Mono for
// ids, scores and timestamps. Files are self-hosted so the build needs no network.
export const inter = localFont({
  src: [
    { path: "../public/fonts/Inter-400.woff2", weight: "400", style: "normal" },
    { path: "../public/fonts/Inter-500.woff2", weight: "500", style: "normal" },
    { path: "../public/fonts/Inter-600.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-inter",
  display: "swap",
});

export const mono = localFont({
  src: [
    { path: "../public/fonts/JetBrainsMono-400.woff2", weight: "400", style: "normal" },
    { path: "../public/fonts/JetBrainsMono-500.woff2", weight: "500", style: "normal" },
    { path: "../public/fonts/JetBrainsMono-700.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-jbmono",
  display: "swap",
});
