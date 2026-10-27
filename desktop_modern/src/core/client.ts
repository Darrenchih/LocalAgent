import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen } from "@tauri-apps/api/event";

export interface Bridge {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>;
  listen?<T>(event: string, handler: (event: { payload: T }) => void): Promise<() => void>;
}

export interface CoreClient {
  request<T>(action: string, params?: Record<string, unknown>): Promise<T>;
  stream<T>(action: string, params: Record<string, unknown>, onDelta: (content: string) => void): Promise<T>;
  cancel(generationId: string): Promise<{ cancelled: boolean }>;
}

function readableError(error: unknown): Error {
  if (error instanceof Error) return error;
  if (typeof error === "string") return new Error(error);
  if (error && typeof error === "object") {
    const value = error as { message?: unknown; code?: unknown };
    if (typeof value.message === "string") return new Error(value.message);
    if (typeof value.code === "string") return new Error(value.code);
  }
  return new Error("core_unavailable");
}

const tauriBridge: Bridge = {
  invoke: (command, args) => tauriInvoke(command, args),
  listen: (event, handler) => tauriListen(event, handler),
};

export function createCoreClient(
  bridge: Bridge = tauriBridge,
  createId: () => string = () => crypto.randomUUID(),
): CoreClient {
  return {
    async request<T>(action: string, params: Record<string, unknown> = {}): Promise<T> {
      try {
        return await bridge.invoke<T>("core_request", {
          request: { version: "1.0", action, params },
        });
      } catch (error) {
        throw readableError(error);
      }
    },

    async stream<T>(action: string, params: Record<string, unknown>, onDelta: (content: string) => void): Promise<T> {
      const requestId = createId();
      const unlisten = bridge.listen
        ? await bridge.listen<{ requestId: string; content: string }>("core-delta", ({ payload }) => {
            if (payload.requestId === requestId) onDelta(payload.content);
          })
        : () => undefined;
      try {
        return await bridge.invoke<T>("core_stream", {
          request: { version: "1.0", requestId, action, params },
        });
      } catch (error) {
        throw readableError(error);
      } finally {
        unlisten();
      }
    },

    cancel(generationId: string) {
      return this.request("cancel_generation", { generation_id: generationId });
    },
  };
}

export const core = createCoreClient();
