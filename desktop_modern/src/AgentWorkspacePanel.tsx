import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { core } from "./core/client";
import type { WorkspaceStatus } from "./core/agentClient";

export default function AgentWorkspacePanel({ status, busy, onChoose }: { status: WorkspaceStatus | null; busy: boolean; onChoose: () => void }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  return <section className="agent-workspace-panel" aria-label={t("agentTask.workspaceSettings")}>
    <div className="card-line"><strong>{t("agentTask.workspaceSettings")}</strong><button disabled={busy} onClick={onChoose}>{t("agentTask.chooseWorkspace")}</button></div>
    <p><span>{t("agentTask.selectedPath")}: </span><code>{status?.path || t("agentTask.unavailable")}</code></p>
    <p>{t("agentTask.execution")}: {t(status?.enabled ? "agentTask.approval_required" : "agentTask.disabled")}</p>
    {status?.reason && <p role="status" title={status.reason}>{t(`agentTask.workspaceReasons.${status.reason}`, { defaultValue: status.reason })}</p>}
    <p>{t("agentTask.allowedRoot")}: <code>{status?.allowed_root || t("agentTask.unavailable")}</code></p>
    <p>{t("agentTask.scopeNote")}</p>
    <details onToggle={(event) => setExpanded(event.currentTarget.open)}><summary>{t("agentTask.permissionsCapabilities")}</summary>
      <p>{t("agentTask.permissions")}: {status?.permissions.length ? status.permissions.join(", ") : t("agentTask.none")}</p>
      <ul>{status?.tools.map((tool) => <li key={`${tool.source}:${tool.name}`}><code>{tool.name}</code> · {tool.risk} · {tool.source} · {t(tool.enabled ? "agentTask.available" : "agentTask.disabled")}</li>)}</ul>
      <p>{t("agentTask.templateNotice")}</p>
      <ul>{status?.templates.map((template) => <li key={template.name}><code>{template.name}</code> · {t(template.enabled ? "agentTask.available" : "agentTask.disabled")}</li>)}</ul>
      <p>{t("agentTask.mcpDisabled")}</p>
      {expanded && <CapabilityDetails/>}
    </details>
  </section>;
}

interface Capabilities { builtin_tools: { function: { name: string; description?: string; parameters: unknown } }[]; mcp_tools: { name: string; description: string }[] }
function CapabilityDetails() {
  const { t } = useTranslation();
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    const load = async () => {
      // Reuse cached discovery data only. No connect, launch, or tools/call request.
      const servers = await invoke<{ tools?: { name: string; description?: string }[] }[]>("discovery_request", { action: "list_servers", params: {} }).catch(() => []);
      const descriptors = (Array.isArray(servers) ? servers : []).flatMap((server) => Array.isArray(server.tools) ? server.tools : []).slice(0, 128).map((tool) => ({ name: tool.name.slice(0, 128), description: (tool.description || "").slice(0, 4000) }));
      const value = await core.request<Capabilities>("agent_capabilities", { mcp_descriptors: descriptors });
      if (live) setCapabilities(value);
    };
    void load().catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, []);
  return <div className="agent-capabilities"><p>{t("agentTask.capabilitiesReadOnly")}</p>
    {failed && <p role="status">{t("agentTask.unavailable")}</p>}
    {(Array.isArray(capabilities?.builtin_tools) ? capabilities.builtin_tools : []).map((tool) => <details key={tool.function.name}><summary>{tool.function.name}</summary><p>{tool.function.description}</p><pre>{JSON.stringify(tool.function.parameters, null, 2)}</pre></details>)}
    {(Array.isArray(capabilities?.mcp_tools) ? capabilities.mcp_tools : []).map((tool, index) => <p key={`${tool.name}-${index}`}><code>{tool.name}</code> · MCP · {t("agentTask.disabled")} · {tool.description}</p>)}
  </div>;
}
