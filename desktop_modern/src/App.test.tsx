import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { version } from "../package.json";
import i18n from "./i18n";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

const { request, stream, cancel } = vi.hoisted(() => ({
  request: vi.fn(), stream: vi.fn(), cancel: vi.fn(),
}));
let didStream = false;

vi.mock("./core/client", () => ({ core: { request, stream, cancel } }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));

afterEach(cleanup);

beforeEach(async () => {
  window.history.replaceState({}, "", "/");
  localStorage.clear();
  await i18n.changeLanguage("zh-TW");
  request.mockReset();
  stream.mockReset();
  cancel.mockReset();
  vi.mocked(invoke).mockReset();
  vi.mocked(open).mockReset();
  vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_workspace_get" ? { conversation_id: (params as { conversationId?: string })?.conversationId ?? null, path: "D:/isolated", allowed_root: "D:/isolated", enabled: true, reason: null, revision: "1", permissions: ["read", "write"], templates: [], tools: [] } : { workspace: "D:/isolated", execution_mode: "approval_required", task: null });
  didStream = false;
  request.mockImplementation(async (action: string) => {
    if (action === "health") return { status: "ok", provider: "ollama", transport: "stdio" };
    if (action === "provider_status") return { name: "ollama", connected: true, models: ["qwen-test"], default_model: "qwen-test", error: null };
    if (action === "list_conversations" || action === "list_memories" || action === "list_plugins") return [];
    if (action === "create_conversation") return { id: "c1", title: "新對話", mode: "chat", model: "qwen-test", created_at: "", updated_at: "" };
    if (action === "read_history") return { conversation: { id: "c1", title: "新對話", mode: "chat", model: "qwen-test" }, messages: didStream ? [{ id: "m2", conversation_id: "c1", role: "assistant", content: "你好", created_at: "" }] : [] };
    return {};
  });
  stream.mockImplementation(async (_action, _params, onDelta) => {
    onDelta("你好");
    didStream = true;
    return { cancelled: false };
  });
});

describe("modern desktop", () => {
  it("persists the conversation model separately from the default and restores the default for a new chat", async () => {
    localStorage.setItem("agent.locale", "en");
    let a = { id: "a", title: "Conversation A", mode: "chat", model: "old" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => {
      if (action === "provider_status") return { connected: true, models: ["old", "new", "default"], default_model: "default" };
      if (action === "list_conversations") return [a];
      if (action === "read_history") return { conversation: a, messages: [] };
      if (action === "set_conversation_settings") { a = { ...a, ...params }; return a; }
      return fallback(action, params);
    });
    render(<App/>);
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("old"));
    await userEvent.selectOptions(screen.getByRole("combobox"), "new");
    await waitFor(() => expect(request).toHaveBeenCalledWith("set_conversation_settings", { conversation_id: "a", mode: "chat", model: "new" }));
    expect(request.mock.calls.some(([action]) => action === "set_default_model")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.getByRole("combobox")).toHaveValue("default");
    await userEvent.click(screen.getByRole("button", { name: "Conversation A new" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("new"));
  });
  it("ignores a delayed model save after selecting another conversation", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "chat", model: "old" };
    const b = { ...a, id: "b", title: "Conversation B", model: "other" };
    let resolveSave!: (value: unknown) => void;
    const save = new Promise((resolve) => { resolveSave = resolve; });
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "provider_status" ? { connected: true, models: ["old", "new", "other"], default_model: "old" } : action === "list_conversations" ? [a, b] : action === "read_history" ? { conversation: params.conversation_id === "a" ? a : b, messages: [] } : action === "set_conversation_settings" ? save : fallback(action, params));
    render(<App/>);
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("old"));
    await userEvent.selectOptions(screen.getByRole("combobox"), "new");
    await waitFor(() => expect(request).toHaveBeenCalledWith("set_conversation_settings", { conversation_id: "a", mode: "chat", model: "new" }));
    await userEvent.click(screen.getByRole("button", { name: "Conversation B other" }));
    await act(async () => { resolveSave({ ...a, model: "new" }); await save; });
    expect(screen.getByRole("combobox")).toHaveValue("other");
  });
  it("locks the running conversation model and preserves it across a terminal history refresh", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "old" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "provider_status" ? { connected: true, models: ["old", "new"], default_model: "old" } : action === "list_conversations" ? [a] : action === "read_history" ? { conversation: a, messages: [] } : fallback(action, params));
    let resolveTask!: (value: unknown) => void;
    const taskResult = new Promise((resolve) => { resolveTask = resolve; });
    const nativeFallback = vi.mocked(invoke).getMockImplementation()!;
    let terminal: unknown;
    vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_task_start" ? taskResult : command === "agent_task_status" && terminal ? terminal : nativeFallback(command, params));
    render(<App/>);
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("old"));
    await userEvent.selectOptions(screen.getByRole("combobox"), "new");
    await waitFor(() => expect(screen.getByRole("combobox")).toBeEnabled());
    await userEvent.type(screen.getByPlaceholderText("Message…"), "goal");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByRole("combobox")).toBeDisabled();
    const before = request.mock.calls.filter(([action]) => action === "read_history").length;
    await act(async () => { terminal = { workspace: "D:/isolated", execution_mode: "approval_required", task: { task_id: "done", conversation_id: "a", goal: "goal", state: "completed", steps: [] } }; resolveTask(terminal); await taskResult; });
    await waitFor(() => expect(request.mock.calls.filter(([action]) => action === "read_history").length).toBeGreaterThan(before));
    expect(screen.getByRole("combobox")).toHaveValue("new");
  });
  it("reports a failed model save and keeps the previous conversation model", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "chat", model: "old" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => {
      if (action === "provider_status") return { connected: true, models: ["old", "new"], default_model: "old" };
      if (action === "list_conversations") return [a];
      if (action === "read_history") return { conversation: a, messages: [] };
      if (action === "set_conversation_settings") throw new Error("Model save failed");
      return fallback(action, params);
    });
    render(<App/>);
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("old"));
    await userEvent.selectOptions(screen.getByRole("combobox"), "new");
    expect(await screen.findByText("Error: Model save failed")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveValue("old");
  });
  it("keeps Stop available in the companion for its current Agent task", async () => {
    localStorage.setItem("agent.locale", "en");
    window.history.replaceState({}, "", "/?companion=1");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "list_conversations" ? [a] : action === "read_history" ? { conversation: a, messages: [] } : fallback(action, params));
    const task = { task_id: "active", conversation_id: "a", goal: "Active goal", state: "awaiting_approval", steps: [] };
    vi.mocked(invoke).mockResolvedValue({ workspace: "D:/isolated", execution_mode: "approval_required", task });
    render(<App/>);
    await waitFor(() => expect(request).toHaveBeenCalledWith("agent_history", { conversation_id: "a" }));
    await userEvent.click(await screen.findByRole("button", { name: "Stop task" }));
    expect(invoke).toHaveBeenCalledWith("agent_task_cancel", { taskId: "active" });
  });
  it("does not restore Agent mode from a terminal history refresh after switching to Chat", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const fallback = request.getMockImplementation()!;
    let historyReads = 0;
    let resolveHistory!: (value: unknown) => void;
    const lateHistory = new Promise((resolve) => { resolveHistory = resolve; });
    request.mockImplementation(async (action, params) => {
      if (action === "list_conversations") return [a];
      if (action === "read_history") return ++historyReads === 1 ? { conversation: a, messages: [] } : lateHistory;
      return fallback(action, params);
    });
    const task = { task_id: "active", conversation_id: "a", goal: "Active goal", state: "awaiting_approval", steps: [] };
    const nativeFallback = vi.mocked(invoke).getMockImplementation()!;
    let statusReads = 0;
    let resolveStatus!: (value: unknown) => void;
    const lateStatus = new Promise((resolve) => { resolveStatus = resolve; });
    vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_workspace_get" ? nativeFallback(command, params) : ++statusReads === 1 ? { workspace: "D:/isolated", execution_mode: "approval_required", task } : lateStatus);
    render(<App/>);
    await screen.findByRole("button", { name: "Chat" });
    await act(async () => { resolveStatus({ workspace: "D:/isolated", execution_mode: "approval_required", task: { ...task, state: "completed" } }); await lateStatus; });
    await waitFor(() => expect(historyReads).toBeGreaterThan(1));
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Chat" })).toHaveClass("selected"));
    await act(async () => { resolveHistory({ conversation: a, messages: [] }); await lateHistory; });
    expect(screen.getByRole("button", { name: "Chat" })).toHaveClass("selected");
    expect(screen.queryByRole("region", { name: "Agent task" })).not.toBeInTheDocument();
  });
  it("merges a native live workspace invalidation for the current conversation", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "list_conversations" ? [a] : action === "read_history" ? { conversation: a, messages: [] } : fallback(action, params));
    const nativeFallback = vi.mocked(invoke).getMockImplementation()!;
    const task = { task_id: "active", conversation_id: "a", goal: "Active goal", state: "awaiting_approval", steps: [{ proposal_id: "p", tool: "read_file", target: "note.txt", risk: "read", args: {}, state: "pending" }] };
    let statusReads = 0;
    let resolveStatus!: (value: unknown) => void;
    const lateStatus = new Promise((resolve) => { resolveStatus = resolve; });
    vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_workspace_get" ? nativeFallback(command, params) : ++statusReads === 1 ? { workspace: "D:/isolated", execution_mode: "approval_required", task } : lateStatus);
    render(<App/>);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve call" })).toBeEnabled());
    await act(async () => { resolveStatus({ workspace: "D:/isolated", execution_mode: "disabled", task, workspace_status: { conversation_id: "a", path: "D:/isolated", allowed_root: "D:/isolated", enabled: false, reason: "WORKSPACE_IDENTITY_CHANGED", revision: "2", permissions: [], templates: [], tools: [] } }); await lateStatus; });
    expect(await screen.findByText("The workspace folder was replaced or changed. Select it again to confirm the current folder.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve call" })).toBeDisabled();
  });
  it("does not merge a late global task workspace into a newly selected conversation", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const b = { ...a, id: "b", title: "Conversation B" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "list_conversations" ? [a, b] : action === "read_history" ? { conversation: params.conversation_id === "a" ? a : b, messages: [] } : fallback(action, params));
    const scope = (id: string) => ({ conversation_id: id, path: `D:/isolated/${id}`, allowed_root: "D:/isolated", enabled: true, reason: null, revision: "1", permissions: ["read"], templates: [], tools: [] });
    const task = { task_id: "active", conversation_id: "a", goal: "Active goal", state: "awaiting_approval", steps: [] };
    let statusReads = 0;
    let resolveStatus!: (value: unknown) => void;
    const lateStatus = new Promise((resolve) => { resolveStatus = resolve; });
    vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_workspace_get" ? scope((params as { conversationId?: string })?.conversationId || "") : ++statusReads === 1 ? { workspace: "D:/isolated/a", execution_mode: "approval_required", task } : lateStatus);
    render(<App/>);
    await screen.findByText("D:/isolated/a");
    await waitFor(() => expect(statusReads).toBeGreaterThan(1));
    await userEvent.click(screen.getByRole("button", { name: "Conversation B Agent" }));
    await screen.findByText("D:/isolated/b");
    await act(async () => { resolveStatus({ workspace: "D:/isolated/a", execution_mode: "disabled", task, workspace_status: { ...scope("a"), enabled: false, reason: "WORKSPACE_IDENTITY_CHANGED" } }); await lateStatus; });
    expect(screen.getByText("D:/isolated/b")).toBeInTheDocument();
    expect(screen.queryByText(/The workspace folder was replaced/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop task" })).toBeInTheDocument();
  });
  it("creates an Agent conversation for explicit workspace selection and shows disabled scope", async () => {
    localStorage.setItem("agent.locale", "en");
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "read_history" ? { conversation: { id: "c1", title: "New conversation", mode: "agent", model: "qwen-test" }, messages: [] } : fallback(action, params));
    const workspace = { conversation_id: "c1", path: "D:/outside", allowed_root: "D:/isolated", enabled: false, reason: "outside_allowed_root", revision: "2", permissions: [], templates: [], tools: [] };
    let selected = false;
    vi.mocked(open).mockResolvedValue("D:/outside");
    vi.mocked(invoke).mockImplementation(async (command) => { if (command === "agent_workspace_select") { selected = true; return workspace; } return command === "agent_workspace_get" ? { ...workspace, path: selected ? workspace.path : null } : { workspace: "D:/isolated", execution_mode: "approval_required", task: null }; });
    render(<App/>);
    await userEvent.click(await screen.findByRole("button", { name: "Agent" }));
    await userEvent.click(screen.getByRole("button", { name: "Choose workspace" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("create_conversation", expect.objectContaining({ mode: "agent" })));
    expect(await screen.findByText("D:/outside")).toBeInTheDocument();
    expect(screen.getByText("outside_allowed_root")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Message…"), "goal");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(invoke).toHaveBeenCalledWith("agent_workspace_select", { conversationId: "c1", path: "D:/outside" });
  });
  it("keeps a global Stop and return action when browsing away from an active task", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const b = { ...a, id: "b", title: "Conversation B" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "list_conversations" ? [a, b] : action === "read_history" ? { conversation: params.conversation_id === "a" ? a : b, messages: [] } : fallback(action, params));
    const task = { task_id: "active", conversation_id: "a", goal: "Active goal", state: "awaiting_approval", steps: [] };
    vi.mocked(invoke).mockImplementation(async (command) => ({ workspace: "D:/isolated", execution_mode: "approval_required", task: command === "agent_task_cancel" ? { ...task, state: "cancelled" } : task }));
    render(<App/>);
    await userEvent.click(await screen.findByRole("button", { name: "Conversation B Agent" }));
    expect(await screen.findByRole("button", { name: "Return to task" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop task" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("agent_task_cancel", { taskId: "active" }));
  });
  it("awaits cancellation before persisting Chat mode and removes the pending approval", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "list_conversations" ? [a] : action === "read_history" ? { conversation: a, messages: [] } : fallback(action, params));
    const task = { task_id: "active", conversation_id: "a", goal: "Active goal", state: "awaiting_approval", steps: [{ proposal_id: "p", tool: "create_directory", target: "notes", risk: "write", args: { path: "notes" }, state: "pending" }] };
    const nativeFallback = vi.mocked(invoke).getMockImplementation()!;
    let resolveCancel!: (value: unknown) => void;
    const pending = new Promise((resolve) => { resolveCancel = resolve; });
    let cancelled = false;
    vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_workspace_get" ? nativeFallback(command, params) : command === "agent_task_cancel" ? pending : { workspace: "D:/isolated", execution_mode: "approval_required", task: cancelled ? { ...task, state: "cancelled" } : task });
    render(<App/>);
    await screen.findByRole("button", { name: "Approve call" });
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    expect(request.mock.calls.filter(([action]) => action === "set_conversation_settings")).toHaveLength(0);
    await act(async () => { cancelled = true; resolveCancel({ workspace: "D:/isolated", execution_mode: "approval_required", task: { ...task, state: "cancelled" } }); await pending; });
    await waitFor(() => expect(request).toHaveBeenCalledWith("set_conversation_settings", { conversation_id: "a", mode: "chat", model: "qwen-test" }));
    expect(screen.queryByRole("button", { name: "Approve call" })).not.toBeInTheDocument();
  });
  it("shows persisted scope for the selected conversation and ignores a late workspace response", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const b = { ...a, id: "b", title: "Conversation B" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "list_conversations" ? [a, b] : action === "read_history" ? { conversation: params.conversation_id === "a" ? a : b, messages: [] } : fallback(action, params));
    const scope = (id: string) => ({ conversation_id: id, path: `D:/isolated/${id}`, allowed_root: "D:/isolated", enabled: true, reason: null, revision: "1", permissions: ["read"], templates: [{ name: "test_json", enabled: true }], tools: [] });
    let resolveLate!: (value: unknown) => void;
    const late = new Promise((resolve) => { resolveLate = resolve; });
    vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_workspace_get" ? (params as { conversationId?: string })?.conversationId === "a" ? late : scope((params as { conversationId?: string })?.conversationId || "") : { workspace: "D:/isolated", execution_mode: "approval_required", task: null });
    render(<App/>);
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("agent_workspace_get", { conversationId: "a" }));
    await userEvent.click(screen.getByRole("button", { name: "Conversation B Agent" }));
    expect(await screen.findByText("D:/isolated/b")).toBeInTheDocument();
    await act(async () => { resolveLate(scope("a")); await late; });
    expect(screen.getByText("D:/isolated/b")).toBeInTheDocument();
    expect(screen.queryByText("D:/isolated/a")).not.toBeInTheDocument();
    expect(invoke).not.toHaveBeenCalledWith("agent_workspace_select", expect.anything());
  });
  it("does not apply a delayed workspace selection to another conversation", async () => {
    localStorage.setItem("agent.locale", "en");
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test" };
    const b = { ...a, id: "b", title: "Conversation B" };
    const fallback = request.getMockImplementation()!;
    request.mockImplementation(async (action, params) => action === "list_conversations" ? [a, b] : action === "read_history" ? { conversation: params.conversation_id === "a" ? a : b, messages: [] } : fallback(action, params));
    const scope = (id: string) => ({ conversation_id: id, path: `D:/isolated/${id}`, allowed_root: "D:/isolated", enabled: true, reason: null, revision: "1", permissions: ["read"], templates: [], tools: [] });
    let resolveLate!: (value: unknown) => void;
    const late = new Promise((resolve) => { resolveLate = resolve; });
    vi.mocked(open).mockResolvedValue("D:/isolated/new-a");
    vi.mocked(invoke).mockImplementation(async (command, params) => command === "agent_workspace_get" ? scope((params as { conversationId?: string })?.conversationId || "") : command === "agent_workspace_select" ? late : { workspace: "D:/isolated", execution_mode: "approval_required", task: null });
    render(<App/>);
    await screen.findByText("D:/isolated/a");
    await userEvent.click(screen.getByRole("button", { name: "Choose workspace" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("agent_workspace_select", { conversationId: "a", path: "D:/isolated/new-a" }));
    await userEvent.click(screen.getByRole("button", { name: "Conversation B Agent" }));
    await act(async () => { resolveLate({ ...scope("a"), path: "D:/isolated/new-a" }); await late; });
    expect(await screen.findByText("D:/isolated/b")).toBeInTheDocument();
    expect(screen.queryByText("D:/isolated/new-a")).not.toBeInTheDocument();
    expect(invoke).not.toHaveBeenCalledWith("agent_workspace_select", { conversationId: "b", path: "D:/isolated/new-a" });
  });
  it.each([
    ["en", "Settings", "About", "Version", "Ollama and models are not bundled. Connect your existing compatible model service.", "Agent tools require approval for each call and operate in an isolated workspace. Protected management does not enable tools."],
    ["zh-TW", "設定", "關於", "版本", "未隨附 Ollama 或模型。請連接您現有的相容模型服務。", "Agent 工具限於隔離工作資料夾，每次操作均需批准。受保護管理不會啟用工具。"],
    ["zh-CN", "设置", "关于", "版本", "未附带 Ollama 或模型。请连接您现有的兼容模型服务。", "Agent 工具限于隔离工作文件夹，每次操作均需批准。受保护管理不会启用工具。"],
  ])("shows the build version and service and execution boundaries in %s", async (locale, settings, about, versionLabel, serviceNotice, safetyNotice) => {
    localStorage.setItem("agent.locale", locale);
    await i18n.changeLanguage(locale);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: settings }));
    await userEvent.click(screen.getByRole("button", { name: about }));
    expect(screen.getByText(`${versionLabel}: ${version}`)).toBeInTheDocument();
    expect(screen.getByText(serviceNotice)).toBeInTheDocument();
    expect(screen.getByText(safetyNotice)).toBeInTheDocument();
    expect(request.mock.calls.map(([action]) => action)).not.toContain("start_protected_runtime");
  });

  it("loads the Core and presents the Traditional Chinese shell", async () => {
    render(<App />);
    expect(await screen.findByText("本機 AI 助理")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新增對話" })).toBeInTheDocument();
    await waitFor(() => expect(request).toHaveBeenCalledWith("health"));
  });

  it("routes Agent goals through native tasks rather than chat streaming", async () => {
    render(<App />);
    await screen.findByText("本機 AI 助理");
    await userEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(await screen.findByText("工作資料夾: D:/isolated")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("輸入訊息…"), "create note");
    await userEvent.click(screen.getByRole("button", { name: "傳送" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("agent_task_start", { conversationId: "c1", goal: "create note", model: "qwen-test" }));
    expect(stream).not.toHaveBeenCalled();
  });

  it("polls an active native task and refreshes conversation history at completion", async () => {
    const task = { task_id: "t1", conversation_id: "c1", goal: "goal", state: "planning", steps: [] };
    let started = false;
    const nativeFallback = vi.mocked(invoke).getMockImplementation()!;
    vi.mocked(invoke).mockImplementation(async (command, params) => {
      if (command === "agent_workspace_get") return nativeFallback(command, params);
      if (command === "agent_task_start") { started = true; return { workspace: "D:/isolated", execution_mode: "approval_required", task }; }
      return { workspace: "D:/isolated", execution_mode: "approval_required", task: started ? { ...task, state: "completed", final_response: "Done" } : null };
    });
    render(<App />);
    await screen.findByText("本機 AI 助理");
    await userEvent.click(screen.getByRole("button", { name: "Agent" }));
    await userEvent.type(screen.getByPlaceholderText("輸入訊息…"), "goal");
    await userEvent.click(screen.getByRole("button", { name: "傳送" }));
    await waitFor(() => expect(request.mock.calls.filter(([action]) => action === "read_history").length).toBeGreaterThanOrEqual(2));
    expect(vi.mocked(invoke).mock.calls.filter(([command]) => command === "agent_task_status").length).toBeGreaterThanOrEqual(2);
    expect(request).toHaveBeenCalledWith("agent_history", { conversation_id: "c1" });
  });

  it.each(["read_history", "agent_history"])("ignores a late %s response after another conversation is selected", async (delayedAction) => {
    const fallback = request.getMockImplementation()!;
    const a = { id: "a", title: "Conversation A", mode: "agent", model: "qwen-test", created_at: "", updated_at: "" };
    const b = { ...a, id: "b", title: "Conversation B" };
    const bundle = (conversation: typeof a) => ({ conversation, messages: [{ id: `${conversation.id}-message`, conversation_id: conversation.id, role: "assistant", content: `${conversation.id}-only-message`, created_at: "" }] });
    const task = { task_id: "old-a", conversation_id: "a", goal: "A private task", state: "completed", steps: [] };
    let resolveLate!: (value: unknown) => void;
    const late = new Promise((resolve) => { resolveLate = resolve; });
    request.mockImplementation(async (action, params) => {
      if (action === "list_conversations") return [a, b];
      if (params?.conversation_id === "a" && action === delayedAction) return late;
      if (action === "read_history") return bundle(params?.conversation_id === "b" ? b : a);
      if (action === "agent_history") return [{ event: task }]; // wrong-conversation rows must also be filtered
      return fallback(action, params);
    });
    render(<App />);
    await waitFor(() => expect(request).toHaveBeenCalledWith(delayedAction, { conversation_id: "a" }));
    await userEvent.click(screen.getByRole("button", { name: "Conversation B Agent" }));
    expect(await screen.findByText("b-only-message")).toBeInTheDocument();
    await act(async () => { resolveLate(delayedAction === "read_history" ? bundle(a) : [{ event: task }]); await late; });
    expect(screen.getByText("b-only-message")).toBeInTheDocument();
    expect(screen.queryByText("a-only-message")).not.toBeInTheDocument();
    expect(screen.queryByText(/A private task/)).not.toBeInTheDocument();
  });

  it("creates a conversation and streams a real Core request", async () => {
    render(<App />);
    const composer = await screen.findByPlaceholderText("輸入訊息…");
    await userEvent.type(composer, "哈囉");
    await userEvent.click(screen.getByRole("button", { name: "傳送" }));
    await waitFor(() => expect(stream).toHaveBeenCalled());
    expect(await screen.findByText("你好")).toBeInTheDocument();
  });

  it("persists the selected language", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "設定" }));
    await userEvent.click(screen.getByRole("button", { name: "語言" }));
    await userEvent.selectOptions(screen.getByLabelText("語言"), "en");
    expect(localStorage.getItem("agent.locale")).toBe("en");
    expect((await screen.findAllByText("Settings")).length).toBeGreaterThan(0);
  });

  it("renders a focused companion instead of the full navigation shell", async () => {
    window.history.replaceState({}, "", "/?companion=1");
    render(<App />);
    expect(await screen.findByRole("main", { name: "快速聊天" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "開啟完整視窗" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "設定" })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("輸入訊息…")).toBeInTheDocument();
  });
});

