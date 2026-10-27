import { describe, expect, it, vi } from "vitest";
import { createCoreClient, type Bridge } from "./client";

describe("Core client", () => {
  it("sends the versioned action and returns its result", async () => {
    const invoke = vi.fn(async () => ({ status: "ok" }));
    const client = createCoreClient({ invoke } as Bridge);
    await expect(client.request("health", {})).resolves.toEqual({ status: "ok" });
    expect(invoke).toHaveBeenCalledWith("core_request", {
      request: { version: "1.0", action: "health", params: {} },
    });
  });

  it("maps bridge errors without exposing raw objects", async () => {
    const client = createCoreClient({ invoke: async () => Promise.reject({ code: "core_error" }) } as Bridge);
    await expect(client.request("health", {})).rejects.toThrow("core_error");
  });

  it("delivers deltas only for its stream request", async () => {
    const handlers = new Map<string, (event: { payload: { requestId: string; content: string } }) => void>();
    const bridge: Bridge = {
      invoke: (async (_command: string, args?: Record<string, unknown>) => {
        const request = args?.request as { requestId?: string };
        handlers.get("core-delta")?.({ payload: { requestId: "other", content: "wrong" } });
        handlers.get("core-delta")?.({ payload: { requestId: request.requestId!, content: "right" } });
        return { cancelled: false };
      }) as Bridge["invoke"],
      listen: (async (event: string, handler: (event: { payload: unknown }) => void) => {
        handlers.set(event, handler);
        return () => handlers.delete(event);
      }) as Bridge["listen"],
    };
    const chunks: string[] = [];
    const client = createCoreClient(bridge, () => "stream-1");
    await client.stream("stream_message", { content: "hi" }, chunks.push.bind(chunks));
    expect(chunks).toEqual(["right"]);
  });
});
