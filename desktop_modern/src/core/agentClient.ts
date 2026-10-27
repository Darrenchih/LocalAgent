import { invoke } from "@tauri-apps/api/core";
export type TaskState = "planning" | "awaiting_approval" | "running" | "completed" | "rejected" | "cancelled" | "failed" | "planning_failed" | "timeout" | "provider_error" | "execution_failed" | "unverified" | "no_operation";
export interface AgentPreview { path: string; before?: string | null; after?: string | null; source_sha256?: string | null; content_type: string; warnings: string[] }
export interface AgentPlanStep { id: string; title: string; dependencies: string[]; state: string; tool?: string; path?: string; result?: unknown; error?: string }
export interface AgentFile { path: string; read_sha256?: string | null; write_sha256?: string | null; content_type?: string; valid: boolean }
export interface AgentMetrics { schema_errors: number; retries: number; duplicate_calls: number; tool_successes: number; tool_failures: number; planning_requests?: number; planning_retries?: number; valid_plans?: number }
export interface AgentStep { proposal_id: string; tool: string; args: unknown; target: string; risk: string; state: string; output?: unknown; error?: string; plan_step_id?: string; preview?: AgentPreview }
export interface AgentTask { plan?: AgentPlanStep[]; files?: AgentFile[]; metrics?: AgentMetrics }
export interface WorkspaceStatus { conversation_id: string | null; path: string | null; allowed_root: string | null; enabled: boolean; reason: string | null; revision: string; permissions: string[]; templates: { name: string; enabled: boolean }[]; tools: { name: string; risk: string; enabled: boolean; source: string }[] }
export interface AgentTask { task_id: string; conversation_id: string; goal: string; state: TaskState; steps: AgentStep[]; workspace?: string; workspace_revision?: string; model_turns?: number; tool_calls?: number; limits?: { tool_calls?: number; model_turns?: number }; outcome?: "model_completed" | "completed_with_errors" | "no_tools" | "model_finished" | "tool_error" | "no_operation" | "unverified" | "planning_failed" | "timeout" | "provider_error" | "execution_failed" | "failed"; goal_validated?: boolean; completion_reason?: string; progress?: unknown; error?: string; final_response?: string }
// Old snapshots used completed for request termination, including zero operations.
// Apply the same conservative label to history headings and live task panels.
export const taskDisplayState = (task: AgentTask): TaskState => {
  if (task.state !== "completed" || task.goal_validated === true) return task.state;
  if (task.outcome === "tool_error" || task.outcome === "completed_with_errors" || task.steps.some(step => step.state === "failed")) return "execution_failed";
  return task.steps.length === 0 ? "no_operation" : "unverified";
};
export interface AgentSnapshot { workspace: string | null; execution_mode: "approval_required" | "disabled"; task: AgentTask | null; workspace_status?: WorkspaceStatus }
export const taskActive = (task?: AgentTask | null) => !!task && ["planning", "awaiting_approval", "running"].includes(task.state);
export function createAgentClient(call: typeof invoke = invoke) {
  return {
    start: (conversationId: string, goal: string, model?: string) => call<AgentSnapshot>("agent_task_start", { conversationId, goal, model }),
    status: (conversationId?: string) => call<AgentSnapshot>("agent_task_status", conversationId ? { conversationId } : {}),
    workspace: (conversationId?: string) => call<WorkspaceStatus>("agent_workspace_get", conversationId ? { conversationId } : {}),
    selectWorkspace: (conversationId: string, path: string) => call<WorkspaceStatus>("agent_workspace_select", { conversationId, path }),
    decide: (taskId: string, proposalId: string, approved: boolean) => call<AgentSnapshot>("agent_task_decide", { taskId, proposalId, approved }),
    cancel: (taskId: string) => call<AgentSnapshot>("agent_task_cancel", { taskId }),
  };
}
export const agentClient = createAgentClient();
