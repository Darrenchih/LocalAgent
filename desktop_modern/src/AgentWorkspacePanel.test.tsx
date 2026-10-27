import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { core } from "./core/client";
import i18n from "./i18n";
import AgentWorkspacePanel from "./AgentWorkspacePanel";
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("./core/client", () => ({ core: { request: vi.fn() } }));
afterEach(cleanup);
it.each([
  ["en", "Choose a workspace folder to enable tools."],
  ["zh-TW", "請選擇工作資料夾以啟用工具。"],
  ["zh-CN", "请选择工作文件夹以启用工具。"],
])("explains a native disabled-workspace reason in %s", async (locale, message) => {
  await i18n.changeLanguage(locale);
  const { rerender } = render(<AgentWorkspacePanel status={{ conversation_id: null, path: null, allowed_root: "D:/isolated", enabled: false, reason: "WORKSPACE_NOT_SELECTED", revision: "1", permissions: [], templates: [], tools: [] }} busy={false} onChoose={vi.fn()}/>);
  expect(screen.getByRole("status")).toHaveTextContent(message);
  expect(screen.getByRole("status")).toHaveAttribute("title", "WORKSPACE_NOT_SELECTED");
  rerender(<AgentWorkspacePanel status={{ conversation_id: null, path: null, allowed_root: "D:/isolated", enabled: false, reason: "FUTURE_NATIVE_REASON", revision: "1", permissions: [], templates: [], tools: [] }} busy={false} onChoose={vi.fn()}/>);
  expect(screen.getByRole("status")).toHaveTextContent("FUTURE_NATIVE_REASON");
});
it("reads cached MCP descriptors into inert capabilities without connecting servers", async () => {
  await i18n.changeLanguage("en");
  vi.mocked(invoke).mockResolvedValue([{ tools: [{ name: "third_party", description: "Cached tool", inputSchema: { type: "object" } }] }]);
  vi.mocked(core.request).mockResolvedValue({ builtin_tools: [{ type: "function", function: { name: "search_text", description: "Search files", parameters: { type: "object" } } }], powershell_templates: [], mcp_tools: [{ name: "third_party", description: "Cached tool", source: "mcp", executable: false }], limits: { tool_calls: 24, model_turns: 20 } });
  render(<AgentWorkspacePanel status={{ conversation_id: "c", path: "D:/isolated", allowed_root: "D:/isolated", enabled: true, reason: null, revision: "1", permissions: ["read"], templates: [{ name: "test_json", enabled: true }, { name: "new_template", enabled: false }], tools: [] }} busy={false} onChoose={vi.fn()}/>);
  await userEvent.click(screen.getByText("Permissions and available tools"));
  expect(await screen.findByText("search_text")).toBeInTheDocument();
  expect(screen.getByText("third_party")).toBeInTheDocument();
  expect(screen.getByText(/MCP discovery is read-only/)).toBeInTheDocument();
  await waitFor(() => expect(core.request).toHaveBeenCalledWith("agent_capabilities", { mcp_descriptors: [{ name: "third_party", description: "Cached tool" }] }));
  expect(invoke).toHaveBeenCalledExactlyOnceWith("discovery_request", { action: "list_servers", params: {} });
  expect(screen.queryByRole("button", { name: /connect|execute|enable/i })).not.toBeInTheDocument();
});
