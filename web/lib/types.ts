/**
 * Shared domain types for OpenQuant.
 *
 * These mirror the data shapes produced by the engine service
 * (/home/user/openquant/engine) and the AI agent service. Keep them in sync
 * with the engine's schema; they are the contract the web app codes against.
 */

import { z } from "zod";

/* -------------------------------------------------------------------------- */
/* Billing / entitlements                                                     */
/* -------------------------------------------------------------------------- */

export const PricingTierSchema = z.object({
  id: z.string(),
  name: z.string(),
  priceMonthlyUsd: z.number(),
  priceId: z.string().nullable(),
  features: z.array(z.string()),
  computeHoursIncluded: z.number(),
  isCurrent: z.boolean().default(false),
  cta: z.string().default("Choose plan"),
});
export type PricingTier = z.infer<typeof PricingTierSchema>;

export const IsPaidSchema = z.object({
  isPaid: z.boolean(),
  tierId: z.string().nullable(),
  renewsAt: z.string().nullable(),
});
export type IsPaid = z.infer<typeof IsPaidSchema>;

export const UsageSchema = z.object({
  computeHoursUsed: z.number(),
  computeHoursLimit: z.number(),
  agentTokensUsed: z.number(),
  agentTokensLimit: z.number(),
  periodStart: z.string(),
  periodEnd: z.string(),
});
export type Usage = z.infer<typeof UsageSchema>;

/* -------------------------------------------------------------------------- */
/* Projects / compute                                                         */
/* -------------------------------------------------------------------------- */

export const MachineStatusSchema = z.enum([
  "stopped",
  "starting",
  "running",
  "stopping",
  "error",
]);
export type MachineStatus = z.infer<typeof MachineStatusSchema>;

export const MachineSchema = z.object({
  id: z.string(),
  projectId: z.string(),
  status: MachineStatusSchema,
  tier: z.string(),
  vcpu: z.number(),
  memoryGb: z.number(),
  startedAt: z.string().nullable(),
});
export type Machine = z.infer<typeof MachineSchema>;

export const ProjectSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().default(""),
  ownerId: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
  machine: MachineSchema.nullable(),
});
export type Project = z.infer<typeof ProjectSchema>;

/* -------------------------------------------------------------------------- */
/* Backtest results                                                           */
/* -------------------------------------------------------------------------- */

/** Engine's pass/fail judgement on a backtest run. */
export const VerdictSchema = z.object({
  status: z.enum(["pass", "fail", "warn", "pending"]),
  sharpe: z.number().nullable(),
  maxDrawdown: z.number().nullable(),
  cagr: z.number().nullable(),
  notes: z.array(z.string()).default([]),
});
export type Verdict = z.infer<typeof VerdictSchema>;

/** A single run target (a strategy / notebook entrypoint the engine executes). */
export const RunTargetSchema = z.object({
  id: z.string(),
  projectId: z.string(),
  label: z.string(),
  entrypoint: z.string(),
  lastRunAt: z.string().nullable(),
  lastVerdict: VerdictSchema.nullable(),
});
export type RunTarget = z.infer<typeof RunTargetSchema>;

/** A row of the engine's trade log. */
export const TradeLogRowSchema = z.object({
  timestamp: z.string(),
  symbol: z.string(),
  side: z.enum(["buy", "sell"]),
  quantity: z.number(),
  price: z.number(),
  pnl: z.number().nullable(),
});
export type TradeLogRow = z.infer<typeof TradeLogRowSchema>;

/* -------------------------------------------------------------------------- */
/* Quant Copilot (AI agent) + memory                                          */
/* -------------------------------------------------------------------------- */

export const MemoryScopeSchema = z.enum(["global", "project", "session"]);
export type MemoryScope = z.infer<typeof MemoryScopeSchema>;

export const MemorySchema = z.object({
  id: z.string(),
  scope: MemoryScopeSchema,
  content: z.string(),
  createdAt: z.string(),
  pinned: z.boolean().default(false),
});
export type Memory = z.infer<typeof MemorySchema>;

export const MemorySettingsSchema = z.object({
  enabled: z.boolean(),
  autoCapture: z.boolean(),
  maxItems: z.number(),
});
export type MemorySettings = z.infer<typeof MemorySettingsSchema>;

export const SlashCommandSchema = z.object({
  name: z.string(),
  description: z.string(),
  usage: z.string(),
  category: z.enum(["research", "data", "backtest", "admin"]),
});
export type SlashCommand = z.infer<typeof SlashCommandSchema>;

export const CopilotSessionSchema = z.object({
  id: z.string(),
  title: z.string(),
  projectId: z.string().nullable(),
  createdAt: z.string(),
  updatedAt: z.string(),
});
export type CopilotSession = z.infer<typeof CopilotSessionSchema>;

/* -------------------------------------------------------------------------- */
/* Social / notifications / users                                             */
/* -------------------------------------------------------------------------- */

export const SocialPostSchema = z.object({
  id: z.string(),
  authorHandle: z.string(),
  title: z.string(),
  body: z.string(),
  verdict: VerdictSchema.nullable(),
  likes: z.number().default(0),
  createdAt: z.string(),
});
export type SocialPost = z.infer<typeof SocialPostSchema>;

export const NotificationSchema = z.object({
  id: z.string(),
  kind: z.enum(["run_complete", "mention", "system", "billing"]),
  message: z.string(),
  read: z.boolean().default(false),
  createdAt: z.string(),
});
export type Notification = z.infer<typeof NotificationSchema>;

export const PublicProfileSchema = z.object({
  handle: z.string(),
  displayName: z.string(),
  bio: z.string().default(""),
  publicPosts: z.number().default(0),
});
export type PublicProfile = z.infer<typeof PublicProfileSchema>;

/* -------------------------------------------------------------------------- */
/* Admin                                                                      */
/* -------------------------------------------------------------------------- */

export const AdminStatusSchema = z.object({
  isAdmin: z.boolean(),
  roles: z.array(z.string()).default([]),
});
export type AdminStatus = z.infer<typeof AdminStatusSchema>;
