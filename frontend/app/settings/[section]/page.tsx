import { notFound } from "next/navigation";
import { SettingsConsole } from "@/components/settings/SettingsConsole";
export default async function SettingsPage({ params }: { params: Promise<{ section: string }> }) { const { section } = await params; if (!["providers", "budgets", "keys", "workers", "audit", "members"].includes(section)) notFound(); return <SettingsConsole section={section} />; }
