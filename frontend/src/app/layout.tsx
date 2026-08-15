import type { Metadata } from "next";
import "./globals.css";
import { AppProviders } from "@/components/app-providers";
import { AppShell } from "@/components/app-shell";
export const metadata: Metadata = { title: "CallPulse", description: "Recover more value from the website traffic you already have." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body><AppProviders><AppShell>{children}</AppShell></AppProviders></body></html>; }
