"use client";
import { useState, type ReactNode } from "react";
import { LaunchDialog } from "./LaunchDialog";
import { Icon } from "@/components/ui/Icon";
import { useAccess } from "@/lib/access";
export function NewRunButton({ children = "New run" }: { children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const access = useAccess("run");
  return <><button className="btn btn-primary" disabled={!access.allowed} title={access.reason} onClick={() => setOpen(true)}><Icon name="plus" size={16} />{children}</button>{open && <LaunchDialog onClose={() => setOpen(false)} />}</>;
}
