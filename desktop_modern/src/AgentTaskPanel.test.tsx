import i18n from "./i18n";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import AgentTaskPanel from "./AgentTaskPanel";
import { createAgentClient, type AgentSnapshot, type TaskState } from "./core/agentClient";
afterEach(cleanup);
beforeEach(async () => { await i18n.changeLanguage("en"); });
const snapshot: AgentSnapshot = { workspace: "D:\\isolated", execution_mode: "approval_required", task: { task_id: "t1", conversation_id: "c1", goal: "Write a note", state: "awaiting_approval", steps: [{ proposal_id: "p1", tool: "write_file", args: { content: "hello" }, target: "note.txt", risk: "write", state: "pending" }] } };
it.each([
  ["planning_failed", "Planning failed"], ["timeout", "Timed out"],
  ["provider_error", "Model provider error"], ["execution_failed", "Tool execution failed"],
  ["unverified", "Ended; goal unverified"],
])("renders %s as an explicit terminal state, never completed", (state, label) => {
  const task = { ...snapshot.task!, state: state as TaskState, steps: [], error: "Reason from the failed stage" };
  render(<AgentTaskPanel snapshot={{ ...snapshot, task }} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByRole("status")).toHaveTextContent(label);
  expect(screen.getByRole("alert")).toHaveTextContent("Reason from the failed stage");
  expect(screen.queryByRole("button", {name:"Approve call"})).not.toBeInTheDocument();
});
it("reads legacy completed/no-operation history without a false completion headline", () => {
  render(<AgentTaskPanel snapshot={{ ...snapshot, task: { ...snapshot.task!, state: "completed", outcome: "no_operation", steps: [] } }} readOnly busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByRole("status")).toHaveTextContent("No operations; incomplete");
});
it("shows approved-read original and proposed content, advisory dependencies and actual metrics", () => {
  const detailed = { ...snapshot, task: { ...snapshot.task!, plan: [{ id: "s1", title: "Read note", dependencies: [], state: "completed" }, { id: "s2", title: "Update note", dependencies: ["s1"], state: "ready" }], metrics: { schema_errors: 2, retries: 1, duplicate_calls: 0, tool_successes: 1, tool_failures: 0 }, steps: [{ ...snapshot.task!.steps[0], plan_step_id: "s2", preview: { path: "note.txt", before: "original line", after: "changed line", source_sha256: "abc", content_type: "text/plain", warnings: ["Preview is advisory"] } }] } };
  render(<AgentTaskPanel snapshot={detailed} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByLabelText("Original content (approved read)")).toHaveTextContent("original line");
  expect(screen.getByLabelText("Proposed content")).toHaveTextContent("changed line");
  expect(screen.getByText(/Advisory plan/)).toBeInTheDocument();
  expect(screen.getByText("Update note")).toBeInTheDocument();
  expect(screen.getByText(/Depends on: s1/)).toBeInTheDocument();
  expect(screen.getByText(/Schema errors: 2/)).toBeInTheDocument();
  expect(screen.getByText("Preview is advisory")).toBeInTheDocument();
});
it("collapses long goals and does not fetch missing original file content", async () => {
  const goal = "A long goal ".repeat(200);
  render(<AgentTaskPanel snapshot={{ ...snapshot, task: { ...snapshot.task!, goal } }} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByRole("button", { name: "Expand goal" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText("No approved original content is available for comparison.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Expand goal" }));
  expect(screen.getByRole("button", { name: "Collapse goal" })).toHaveAttribute("aria-expanded", "true");
});
it("formats JSON comparisons while keeping exact approval parameters visible", () => {
  const source = '{"a":1,"b":{"c":2}}';
  const next = { ...snapshot, task: { ...snapshot.task!, steps: [{ ...snapshot.task!.steps[0], args: { path: "data.json", content: source }, preview: { path: "data.json", before: source, after: '{"a":2}', content_type: "application/json", warnings: [] } }] } };
  render(<AgentTaskPanel snapshot={next} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByLabelText("Original content (approved read)").textContent).toBe(JSON.stringify(JSON.parse(source), null, 2));
  expect(screen.getByLabelText("Proposed content").textContent).toBe('{\n  "a": 2\n}');
  expect(screen.getByText(/\\"a\\":1/)).toBeInTheDocument();
});
it("offers a readable Markdown comparison without executing embedded HTML", async () => {
  render(<AgentTaskPanel snapshot={{ ...snapshot, task: { ...snapshot.task!, steps: [{ ...snapshot.task!.steps[0], preview: { path: "note.md", before: "# Original heading", after: "# Updated heading\n<script>bad()</script>", content_type: "text/markdown", warnings: [] } }] } }} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  await userEvent.click(screen.getByText("Rendered Markdown"));
  expect(screen.getByRole("heading", { name: "Original heading" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Updated heading" })).toBeInTheDocument();
  expect(document.querySelector(".agent-markdown script")).toBeNull();
});
it("shows exact call details and sends separate approval, rejection, and stop intents", async () => {
  const decide = vi.fn(), cancel = vi.fn();
  render(<AgentTaskPanel snapshot={snapshot} busy={false} onDecide={decide} onCancel={cancel}/>);
  expect(screen.getByText("note.txt")).toBeInTheDocument();
  expect(screen.getByText(/hello/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Approve call" }));
  expect(decide).toHaveBeenLastCalledWith("p1", true);
  await userEvent.click(screen.getByRole("button", { name: "Reject call" }));
  expect(decide).toHaveBeenLastCalledWith("p1", false);
  await userEvent.click(screen.getByRole("button", { name: "Stop task" }));
  expect(cancel).toHaveBeenCalledOnce();
});
it("never offers approval for completed calls and displays failure evidence", () => {
  const failed = { ...snapshot, task: { ...snapshot.task!, state: "failed" as const, error: "Execution denied", steps: [{ ...snapshot.task!.steps[0], state: "failed", output: "partial", error: "Access denied" }] } };
  render(<AgentTaskPanel snapshot={failed} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.queryByText("Approve call")).not.toBeInTheDocument();
  expect(screen.queryByText("Stop task")).not.toBeInTheDocument();
  expect(screen.getByText("Execution denied")).toBeInTheDocument();
  expect(screen.getByLabelText("Tool output")).toHaveTextContent("partial");
});
it("uses native task commands with camelCase arguments and propagates errors", async () => {
  const invoke = vi.fn().mockResolvedValue(snapshot);
  const client = createAgentClient(invoke);
  await client.start("c1", "goal", "model");
  expect(invoke).toHaveBeenLastCalledWith("agent_task_start", { conversationId: "c1", goal: "goal", model: "model" });
  await client.decide("t1", "p1", true);
  expect(invoke).toHaveBeenLastCalledWith("agent_task_decide", { taskId: "t1", proposalId: "p1", approved: true });
  await client.cancel("t1");
  expect(invoke).toHaveBeenLastCalledWith("agent_task_cancel", { taskId: "t1" });
  invoke.mockRejectedValueOnce(new Error("Unavailable"));
  await expect(client.status()).rejects.toThrow("Unavailable");
});
it.each([
  ["zh-TW", "批准操作", "停止任務", "每次操作均需批准"],
  ["zh-CN", "批准操作", "停止任务", "每次操作均需批准"],
  ["en", "Approve call", "Stop task", "Approval required for each call"],
])("localizes task approval and state controls in %s", async (locale, approve, stop, mode) => {
  await i18n.changeLanguage(locale);
  render(<AgentTaskPanel snapshot={snapshot} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByRole("button", { name: approve })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: stop })).toBeInTheDocument();
  expect(screen.getByText(new RegExp(mode))).toBeInTheDocument();
  expect(screen.queryByText("approval_required")).not.toBeInTheDocument();
});
it("prioritizes and focuses the newest approval while previous calls stay collapsed", async () => {
  const completed = [1, 2].map((id) => ({ ...snapshot.task!.steps[0], proposal_id: `old-${id}`, tool: `completed-tool-${id}`, state: "completed", output: "large prior output" }));
  const next = { ...snapshot, task: { ...snapshot.task!, steps: [...completed, { ...snapshot.task!.steps[0], proposal_id: "latest", tool: "latest-tool", state: "awaiting_approval" }] } };
  const decide = vi.fn();
  const { container, rerender } = render(<AgentTaskPanel snapshot={next} busy={false} onDecide={decide} onCancel={vi.fn()}/>);
  const current = screen.getByRole("article", { name: "Awaiting approval" });
  expect(current).toHaveFocus();
  expect(current).toHaveTextContent("latest-tool");
  const prior = container.querySelectorAll("details");
  expect(prior).toHaveLength(2);
  expect([...prior].every((entry) => !entry.open)).toBe(true);
  expect(container.querySelector(".agent-task-toolbar")?.contains(screen.getByRole("button", { name: "Stop task" }))).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Approve call" }));
  expect(decide).toHaveBeenCalledWith("latest", true);
  await userEvent.click(screen.getByText("completed-tool-1"));
  expect(prior[0].open).toBe(true);
  rerender(<AgentTaskPanel snapshot={{ ...next, task: { ...next.task, steps: [...next.task.steps, { ...next.task.steps[2], proposal_id: "newest", tool: "newest-tool" }] } }} busy={false} onDecide={decide} onCancel={vi.fn()}/>);
  expect(screen.getByRole("article", { name: "Awaiting approval" })).toHaveFocus();
  expect(screen.getByRole("article", { name: "Awaiting approval" })).toHaveTextContent("newest-tool");
});
it("keeps historical approvals read-only and blocks duplicate actions while a decision is pending", () => {
  const { rerender } = render(<AgentTaskPanel snapshot={snapshot} busy={true} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByRole("button", { name: "Approve call" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reject call" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Stop task" })).toBeDisabled();
  rerender(<AgentTaskPanel snapshot={snapshot} busy={false} readOnly onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.queryByRole("button", { name: "Approve call" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Stop task" })).not.toBeInTheDocument();
});
it("shows tool progress and distinguishes model completion from verified success", () => {
  render(<AgentTaskPanel snapshot={{ ...snapshot, task: { ...snapshot.task!, state: "completed", outcome: "model_completed", completion_reason: "Model stopped", tool_calls: 3 } }} busy={false} onDecide={vi.fn()} onCancel={vi.fn()}/>);
  expect(screen.getByText("Tool calls: 3 / 24")).toBeInTheDocument();
  expect(screen.getByText("Model finished; goal not independently verified")).toBeInTheDocument();
  expect(screen.getByText("Model stopped")).toBeInTheDocument();
});
it.each(["list_directory", "create_directory", "search_text"])("presents the exact target and risk before approving %s", async (tool) => {
  const decide = vi.fn();
  render(<AgentTaskPanel snapshot={{ ...snapshot, task: { ...snapshot.task!, steps: [{ ...snapshot.task!.steps[0], tool, target: "notes/subfolder", risk: tool === "create_directory" ? "write" : "read", args: { path: "notes/subfolder", query: "needle" } }] } }} busy={false} onDecide={decide} onCancel={vi.fn()}/>);
  expect(screen.getByRole("article", { name: "Awaiting approval" })).toHaveTextContent(tool);
  expect(screen.getByText("notes/subfolder")).toBeInTheDocument();
  expect(screen.getByText(tool === "create_directory" ? "write" : "read")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Approve call" }));
  expect(decide).toHaveBeenCalledWith("p1", true);
});
