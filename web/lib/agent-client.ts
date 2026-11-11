/**
 * Client for the Python AI research agent service ("Quant Copilot").
 *
 * The agent service is reached over HTTP at AGENT_SERVICE_URL and owns the
 * LLM research loop, slash commands, and agent memory. This client wraps its
 * surface with typed, validated calls.
 */

import { z, type ZodType } from "zod";

import {
  CopilotSessionSchema,
  MemorySchema,
  MemorySettingsSchema,
  SlashCommandSchema,
  type CopilotSession,
  type Memory,
  type MemorySettings,
  type SlashCommand,
} from "@/lib/types";

export class AgentServiceError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AgentServiceError";
  }
}

function baseUrl(): string {
  // SEAM: Python agent service base URL.
  return process.env.AGENT_SERVICE_URL ?? "http://localhost:8002";
}

async function agentFetch<T>(
  path: string,
  schema: ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const token = process.env.AGENT_SERVICE_TOKEN;
  const res = await fetch(`${baseUrl()}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      // SEAM: agent service auth.
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!res.ok) {
    throw new AgentServiceError(
      `Agent service ${path} failed: ${res.status}`,
      res.status,
    );
  }

  const json = (await res.json()) as unknown;
  return schema.parse(json);
}

/* -------------------------------- Sessions -------------------------------- */

export function createCopilotSession(params: {
  projectId: string | null;
  title?: string;
}): Promise<CopilotSession> {
  return agentFetch("/sessions", CopilotSessionSchema, {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function getCopilotSession(sessionId: string): Promise<CopilotSession> {
  return agentFetch(`/sessions/${sessionId}`, CopilotSessionSchema);
}

/* ----------------------------- Slash commands ----------------------------- */

export function listSlashCommands(): Promise<SlashCommand[]> {
  return agentFetch("/slash-commands", z.array(SlashCommandSchema));
}

/* --------------------------------- Memory --------------------------------- */

export function listMemory(scope?: string): Promise<Memory[]> {
  const q = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  return agentFetch(`/memory${q}`, z.array(MemorySchema));
}

export function createMemory(params: {
  scope: string;
  content: string;
}): Promise<Memory> {
  return agentFetch("/memory", MemorySchema, {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function deleteMemory(id: string): Promise<{ id: string }> {
  return agentFetch(`/memory/${id}`, z.object({ id: z.string() }), {
    method: "DELETE",
  });
}

export function getMemorySettings(): Promise<MemorySettings> {
  return agentFetch("/memory/settings", MemorySettingsSchema);
}

export function updateMemorySettings(
  settings: Partial<MemorySettings>,
): Promise<MemorySettings> {
  return agentFetch("/memory/settings", MemorySettingsSchema, {
    method: "PATCH",
    body: JSON.stringify(settings),
  });
}
