/**
 * Typed fetch wrapper for the external data API / backtest engine.
 *
 * The engine (owned by /home/user/openquant/engine) is reached over HTTP at
 * DATA_API_URL. This client centralizes base URL, auth header, error handling,
 * and zod validation of responses.
 */

import { z, type ZodType } from "zod";

import {
  MachineSchema,
  ProjectSchema,
  RunTargetSchema,
  TradeLogRowSchema,
  VerdictSchema,
  type Machine,
  type Project,
  type RunTarget,
  type TradeLogRow,
  type Verdict,
} from "@/lib/types";

export class DataApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "DataApiError";
  }
}

function baseUrl(): string {
  // SEAM: engine/data API base URL.
  return process.env.DATA_API_URL ?? "http://localhost:8001";
}

async function dataFetch<T>(
  path: string,
  schema: ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const token = process.env.DATA_API_TOKEN;
  const res = await fetch(`${baseUrl()}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      // SEAM: engine service auth.
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
    // Engine data is request-scoped; never cache by default.
    cache: "no-store",
  });

  if (!res.ok) {
    throw new DataApiError(
      `Data API ${path} failed: ${res.status}`,
      res.status,
    );
  }

  const json = (await res.json()) as unknown;
  return schema.parse(json);
}

/* --------------------------------- Projects ------------------------------- */

export function listProjects(): Promise<Project[]> {
  return dataFetch("/projects", z.array(ProjectSchema));
}

export function getProject(projectId: string): Promise<Project> {
  return dataFetch(`/projects/${projectId}`, ProjectSchema);
}

/* --------------------------------- Machines ------------------------------- */

export function getMachine(projectId: string): Promise<Machine> {
  return dataFetch(`/projects/${projectId}/machine`, MachineSchema);
}

export function setMachineState(
  projectId: string,
  desired: "running" | "stopped",
): Promise<Machine> {
  return dataFetch(`/projects/${projectId}/machine`, MachineSchema, {
    method: "POST",
    body: JSON.stringify({ desired }),
  });
}

/* ------------------------------- Run targets ------------------------------ */

export function listRunTargets(projectId: string): Promise<RunTarget[]> {
  return dataFetch(
    `/projects/${projectId}/run-targets`,
    z.array(RunTargetSchema),
  );
}

export function triggerRunTarget(
  projectId: string,
  runTargetId: string,
): Promise<Verdict> {
  return dataFetch(`/projects/${projectId}/run-target`, VerdictSchema, {
    method: "POST",
    body: JSON.stringify({ runTargetId }),
  });
}

/* ------------------------------- Trade logs ------------------------------- */

export function getTradeLog(
  projectId: string,
  runTargetId: string,
): Promise<TradeLogRow[]> {
  return dataFetch(
    `/projects/${projectId}/run-targets/${runTargetId}/trades`,
    z.array(TradeLogRowSchema),
  );
}
