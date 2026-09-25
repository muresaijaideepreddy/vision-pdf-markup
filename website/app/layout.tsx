import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MarkupDoc · CI Review Lab",
  description: "Review handwritten PDF corrections, apply supported edits to Word, and measure the experiment.",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
