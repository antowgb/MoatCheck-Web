import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import PageTransition from "@/components/PageTransition";
import Sidebar, { MobileTopBar } from "@/components/Sidebar";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "MoatCheck",
  description: "Long-term quantitative screener",
};

// Applies the stored/system theme before first paint to avoid a light/dark flash.
const THEME_INIT_SCRIPT = `
(function() {
  try {
    var stored = localStorage.getItem('moatcheck_theme');
    var dark = stored ? stored === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (dark) document.documentElement.classList.add('dark');
  } catch (e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="font-sans">
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex-1 min-w-0 flex flex-col">
            <MobileTopBar />
            <main className="flex-1 px-4 py-6 md:px-8 md:py-8 max-w-6xl mx-auto w-full">
              <PageTransition>{children}</PageTransition>
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
