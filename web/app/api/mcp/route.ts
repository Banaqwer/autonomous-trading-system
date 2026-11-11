import { z } from "zod";

import { handle, jsonError, jsonOk } from "@/lib/http";

/**
 * /api/mcp — stub Model Context Protocol endpoint.
 *
 * This exposes OpenQuant tools (data queries, backtests, memory) to external
 * MCP clients. It authenticates with a bearer token (AGENT_SERVICE_TOKEN /
 * a dedicated MCP token) rather than a Clerk session, so it is listed as a
 * public route in middleware and checks the token itself.
 *
 * The real implementation speaks JSON-RPC 2.0 and proxies tool calls to the
 * engine and agent services. This stub advertises the tool catalog and echoes
 * a not-implemented result for calls, so clients can discover the surface.
 */

function checkToken(req: Request): boolean {
  // SEAM: verify the MCP bearer token.
  const expected = process.env.MCP_TOKEN ?? process.env.AGENT_SERVICE_TOKEN;
  if (!expected) return true; // dev: allow when unset
  const header = req.headers.get("authorization") ?? "";
  return header === `Bearer ${expected}`;
}

const TOOLS = [
  {
    name: "query_data",
    description: "Query a market data series from the engine.",
    inputSchema: {
      type: "object",
      properties: {
        symbol: { type: "string" },
        range: { type: "string" },
      },
      required: ["symbol"],
    },
  },
  {
    name: "run_backtest",
    description: "Run a project's backtest run target and return the verdict.",
    inputSchema: {
      type: "object",
      properties: {
        projectId: { type: "string" },
        runTargetId: { type: "string" },
      },
      required: ["projectId", "runTargetId"],
    },
  },
] as const;

/** GET advertises a minimal manifest for discovery. */
export async function GET(req: Request) {
  return handle(async () => {
    if (!checkToken(req)) return jsonError("Unauthorized", 401);
    return jsonOk({
      protocol: "mcp",
      version: "2024-11-05",
      server: { name: "openquant", version: "0.1.0" },
      tools: TOOLS,
    });
  });
}

const JsonRpcRequest = z.object({
  jsonrpc: z.literal("2.0"),
  id: z.union([z.string(), z.number()]).nullable().optional(),
  method: z.string(),
  params: z.unknown().optional(),
});

/** POST handles JSON-RPC: `tools/list` is implemented; `tools/call` is a stub. */
export async function POST(req: Request) {
  return handle(async () => {
    if (!checkToken(req)) return jsonError("Unauthorized", 401);

    const parsed = JsonRpcRequest.safeParse(await req.json());
    if (!parsed.success) {
      return jsonError("Invalid JSON-RPC request", 400);
    }
    const { id = null, method } = parsed.data;

    if (method === "tools/list") {
      return jsonOk({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
    }

    if (method === "tools/call") {
      // SEAM: dispatch to lib/data-client / lib/agent-client based on tool name.
      return jsonOk({
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: "tools/call not implemented in skeleton" },
      });
    }

    return jsonOk({
      jsonrpc: "2.0",
      id,
      error: { code: -32601, message: `Unknown method: ${method}` },
    });
  });
}
