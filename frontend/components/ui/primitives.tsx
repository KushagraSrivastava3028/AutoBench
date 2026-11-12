/**
 * Shared primitives.
 *
 * Everything dense, everything bordered, nothing rounded past 8px. The look is
 * carried by tonal layers and hairlines rather than shadows, so panels can sit
 * flush against one another without visual mush.
 */

import Link from "next/link";
import type { ReactNode } from "react";
import clsx from "clsx";

import { TONE_CLASS, type Tone } from "@/lib/format";
import type { Interval } from "@/lib/types";

// ---------------------------------------------------------------------------
// structure
// ---------------------------------------------------------------------------

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={clsx("panel flex min-h-0 flex-col", className)}>
      {(title || actions) && (
        <header className="panel-header">
          <div className="flex min-w-0 items-baseline gap-2">
            {typeof title === "string" ? <h2 className="text-[14px] font-semibold tracking-[-0.02em]">{title}</h2> : title}
            {subtitle && <span className="truncate text-[11px] text-ink-inert">{subtitle}</span>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Chip({
  tone = "neutral",
  children,
  pulse = false,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  pulse?: boolean;
  title?: string;
}) {
  return (
    <span className={clsx("chip", TONE_CLASS[tone])} title={title}>
      {pulse && <span className="pulse-dot size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={clsx("mono tnum", className)}>{children}</span>;
}

/**
 * A metric with its interval. Never renders a bare point estimate — that is the
 * whole editorial position of the product, enforced at the component level.
 */
export function StatTile({
  label,
  value,
  interval,
  sub,
  accent,
  hint,
}: {
  label: string;
  value: ReactNode;
  interval?: Interval | null;
  sub?: ReactNode;
  accent?: "default" | "warn" | "pass" | "fail";
  hint?: string;
}) {
  const accentClass =
    accent === "warn"
      ? "text-warn"
      : accent === "pass"
        ? "text-pass-text"
        : accent === "fail"
          ? "text-fail-agent"
          : "text-ink";

  return (
    <div className="panel flex flex-col justify-between gap-2 p-3" title={hint}>
      <div className="label">{label}</div>
      <div className={clsx("mono tnum text-2xl font-semibold leading-none", accentClass)}>
        {value}
      </div>
      <div className="mono tnum text-[11px] text-ink-muted">
        {interval ? (
          <span title={`95% ${interval.method} interval: ${(interval.low * 100).toFixed(1)}%–${(interval.high * 100).toFixed(1)}%`}>
            ±{(interval.margin * 100).toFixed(1)}
            <span className="ml-1.5 text-ink-inert">
              [{(interval.low * 100).toFixed(1)}–{(interval.high * 100).toFixed(1)}]
            </span>
          </span>
        ) : (
          (sub ?? " ")
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// states
// ---------------------------------------------------------------------------

/**
 * Empty states are designed, not defaulted. Each one says what is missing and
 * what to type to fix it — an empty screen with no next action is a dead end.
 */
export function EmptyState({
  title,
  body,
  command,
  action,
}: {
  title: string;
  body: string;
  command?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className="text-sm font-semibold text-ink">{title}</div>
      <p className="max-w-md text-[12px] leading-relaxed text-ink-muted">{body}</p>
      {command && (
        <code className="mono rounded-sm border border-border-muted bg-canvas px-2.5 py-1.5 text-[12px] text-accent">
          <span className="mr-1.5 text-ink-inert">$</span>
          {command}
        </code>
      )}
      {action}
    </div>
  );
}

export function ErrorState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="text-sm font-semibold text-fail-agent">{title}</div>
      <p className="mono max-w-lg text-[12px] leading-relaxed text-ink-muted">{detail}</p>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  badge,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <h1 className="text-[36px] font-semibold leading-tight tracking-[-0.05em] text-ink">{title}</h1>
          {badge}
        </div>
        {description && (
          <p className="mt-2 max-w-3xl text-[13px] leading-relaxed text-ink-muted">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// data display
// ---------------------------------------------------------------------------

/** A bare inline sparkline. No axes, no legend — it is a shape, not a chart. */
export function Sparkline({
  values,
  tone = "accent",
  width = 96,
  height = 24,
}: {
  values: number[];
  tone?: "accent" | "pass" | "fail";
  width?: number;
  height?: number;
}) {
  if (values.length < 2) {
    return <div style={{ width, height }} className="rounded-xs bg-border-subtle/40" />;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const path = values
    .map((value, index) => {
      const x = index * step;
      // Inset by 2px so the stroke is not clipped at the extremes.
      const y = height - 2 - ((value - min) / span) * (height - 4);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const stroke =
    tone === "pass" ? "var(--color-pass)" : tone === "fail" ? "var(--color-fail-agent)" : "var(--color-accent)";

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export function Meter({ value, tone = "accent" }: { value: number; tone?: "accent" | "warn" | "fail" }) {
  const colour =
    tone === "warn" ? "bg-warn" : tone === "fail" ? "bg-fail-agent" : "bg-accent";
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-border-subtle">
      <div
        className={clsx("h-full rounded-full transition-[width] duration-500", colour)}
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-[11px] text-ink-muted">{label}</span>
      <span className="mono tnum text-[12px] text-ink">{children}</span>
    </div>
  );
}

export function LinkCell({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="mono text-accent hover:text-accent-hover hover:underline">
      {children}
    </Link>
  );
}

/**
 * The legend for the trial matrix.
 *
 * Present on every screen that shows cells, because "purple" versus "rose" is
 * the difference between a model problem and an infrastructure problem and is
 * not guessable.
 */
export function MatrixLegend() {
  const entries: Array<[string, string]> = [
    ["bg-pass/85", "pass"],
    ["bg-warn/70", "partial"],
    ["bg-fail-agent/55", "fail"],
    ["bg-fail-infra/70", "infra"],
    ["bg-running/70", "running"],
    ["bg-border-muted", "queued"],
  ];
  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px] text-ink-muted">
      {entries.map(([cls, label]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className={clsx("size-2.5 rounded-xs", cls)} />
          {label}
        </span>
      ))}
    </div>
  );
}
