import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentSnapshot, AgentStep } from "./core/agentClient";
import { taskActive, taskDisplayState } from "./core/agentClient";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
const previewText = (content: string, contentType: string) => {
  if (contentType.includes("json")) {
    try { return JSON.stringify(JSON.parse(content), null, 2); } catch { /* Preserve invalid content exactly for review. */ }
  }
  return content;
};
export default function AgentTaskPanel({ snapshot, busy, onDecide, onCancel, readOnly = false, approvalEnabled = true }: { approvalEnabled?: boolean; readOnly?: boolean; snapshot: AgentSnapshot | null; busy: boolean; onDecide: (proposalId: string, approved: boolean) => void; onCancel: () => void }) {
  const { t } = useTranslation();
  const task = snapshot?.task;
  const [expandedGoal, setExpandedGoal] = useState(false);
  useEffect(() => { setExpandedGoal(false); }, [task?.task_id]);
  const pending = !readOnly && task?.state === "awaiting_approval" ? [...task.steps].reverse().find((step) => ["pending", "proposed", "awaiting_approval"].includes(step.state)) : undefined;
  const current = pending || task?.steps.at(-1);
  const previous = task?.steps.filter((step) => step.proposal_id !== current?.proposal_id) || [];
  const approval = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!pending) return;
    approval.current?.scrollIntoView?.({ block: "start" });
    approval.current?.focus({ preventScroll: true });
  }, [pending?.proposal_id]);
  const stepDetails = (step: AgentStep) => <>
    <dl><dt>{t("agentTask.target")}</dt><dd>{step.target}</dd><dt>{t("agentTask.risk")}</dt><dd>{step.risk}</dd><dt>{t("agentTask.state")}</dt><dd>{t(`agentTask.${step.state}`, { defaultValue: step.state })}</dd><dt>{t("agentTask.parameters")}</dt><dd><pre>{JSON.stringify(step.args, null, 2)}</pre></dd></dl>
    {step.output !== undefined && <pre aria-label={t("agentTask.output")}>{typeof step.output === "string" ? step.output : JSON.stringify(step.output, null, 2)}</pre>}{step.error && <p role="alert">{step.error}</p>}
    {step.plan_step_id && <p>{t("agentTask.planStep")}: {step.plan_step_id}</p>}
    {step.preview && <section className="agent-diff" aria-label={t("agentTask.diff")}>
      <h4>{t("agentTask.diff")}: {step.preview.path}</h4><p>{step.preview.content_type}</p>
      <div className="agent-diff-columns"><div><strong>{t("agentTask.before")}</strong>{typeof step.preview.before === "string" ? <pre aria-label={t("agentTask.before")}>{previewText(step.preview.before, step.preview.content_type)}</pre> : <p>{t("agentTask.noOriginal")}</p>}</div><div><strong>{t("agentTask.after")}</strong><pre aria-label={t("agentTask.after")}>{previewText(step.preview.after ?? "", step.preview.content_type)}</pre></div></div>
      {step.preview.content_type.includes("markdown") && <details><summary>{t("agentTask.markdownView")}</summary><div className="agent-diff-columns agent-markdown">{[step.preview.before, step.preview.after].map((text, index) => <div key={index}><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt}</span>, a: ({ children }) => <span>{children}</span> }}>{text ?? ""}</ReactMarkdown></div>)}</div></details>}
      {step.preview.source_sha256 && <p className="agent-source-hash">SHA-256: {step.preview.source_sha256}</p>}
      {step.preview.warnings?.map((warning, index) => <p className="notice" key={index}>{warning}</p>)}<p className="notice">{t("agentTask.previewNote")}</p>
    </section>}
    {step.tool === "write_file" && !step.preview && <p className="notice">{t("agentTask.noOriginal")}</p>}
  </>;
  return <section className="agent-panel agent-task-panel" aria-label={t("agentTask.title")}>
    <header className="agent-task-toolbar"><strong>{t("agentTask.title")}</strong>{task && <span role="status">{t(`agentTask.${taskDisplayState(task) === "no_operation" ? "no_operation_status" : taskDisplayState(task)}`)}</span>}{!readOnly && taskActive(task) && <button disabled={busy} onClick={onCancel}>{t("agentTask.stop")}</button>}</header>
    <div className="agent-content"><p className="agent-workspace">{t("agentTask.workspace")}: {snapshot?.workspace || t("agentTask.unavailable")}</p><p>{t("agentTask.execution")}: {t(`agentTask.${snapshot?.execution_mode || "unavailable"}`)}</p>
      {!task && <p>{t("agentTask.empty")}</p>}
      {task && <><h3 className={expandedGoal ? "agent-goal expanded" : "agent-goal"}>{expandedGoal || task.goal.length <= 180 ? task.goal : `${task.goal.slice(0, 180)}…`}</h3>{task.goal.length > 180 && <button aria-expanded={expandedGoal} onClick={() => setExpandedGoal(!expandedGoal)}>{t(expandedGoal ? "agentTask.collapseGoal" : "agentTask.expandGoal")}</button>}
        <p>{t("agentTask.progress", { count: task.tool_calls ?? task.steps.length, limit: task.limits?.tool_calls ?? 24 })}</p>
        {task.workspace && <p>{t("agentTask.taskWorkspace")}: {task.workspace}</p>}
        {(task.outcome || task.state === "completed") && <p className="notice">{t(`agentTask.${task.outcome || "model_completed"}`)}</p>}
        {task.completion_reason && <p>{task.completion_reason}</p>}
        {current && <article ref={approval} tabIndex={-1} className={`approval-card ${pending ? "current-approval" : ""}`} aria-label={pending ? t("agentTask.awaiting_approval") : t("agentTask.currentStep")}><strong>{current.tool}</strong>{stepDetails(current)}
          {pending && <div className="agent-task-decisions"><button disabled={busy || !approvalEnabled} onClick={() => onDecide(pending.proposal_id, false)}>{t("agentTask.reject")}</button><button disabled={busy || !approvalEnabled} onClick={() => onDecide(pending.proposal_id, true)}>{t("agentTask.approve")}</button></div>}
        </article>}
        {!!task.plan?.length && <section className="agent-plan"><h4>{t("agentTask.advisoryPlan")}</h4><p className="notice">{t("agentTask.planNote")}</p><ol>{task.plan.map((entry) => <li key={entry.id} aria-current={current?.plan_step_id === entry.id ? "step" : undefined}><strong>{entry.title}</strong> <span>{t(`agentTask.${entry.state}`, { defaultValue: entry.state })}</span>{entry.dependencies.length > 0 && <p>{t("agentTask.dependencies")}: {entry.dependencies.join(", ")}</p>}{entry.path && <p>{entry.path}</p>}{entry.result !== undefined && <pre>{typeof entry.result === "string" ? entry.result : JSON.stringify(entry.result, null, 2)}</pre>}{entry.error && <p role="alert">{entry.error}</p>}</li>)}</ol></section>}
        {task.metrics && <p className="agent-metrics">{t("agentTask.metrics", { ...task.metrics })}</p>}
        {task.metrics?.planning_requests !== undefined && <p>{t("agentTask.planningMetrics", { ...task.metrics })}</p>}
        {!!task.files?.length && <details><summary>{t("agentTask.fileVersions")}</summary>{task.files.map((file) => <div key={file.path}><strong>{file.path}</strong><p>{file.content_type} · {t(file.valid ? "agentTask.versionValid" : "agentTask.versionInvalid")}</p><pre>{JSON.stringify({ read_sha256: file.read_sha256, write_sha256: file.write_sha256 }, null, 2)}</pre></div>)}</details>}
        {previous.length > 0 && <div className="agent-previous-steps"><h4>{t("agentTask.previousSteps")}</h4>{previous.map((step) => <details key={step.proposal_id} className="agent-step-history"><summary><strong>{step.tool}</strong><span>{step.target}</span><small>{t(`agentTask.${step.state}`, { defaultValue: step.state })}</small></summary>{stepDetails(step)}</details>)}</div>}
        {task.error && <p role="alert">{task.error}</p>}{task.final_response && <p>{task.final_response}</p>}</>}
    </div>
  </section>;
}
