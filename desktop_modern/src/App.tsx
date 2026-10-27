import AgentTaskPanel from "./AgentTaskPanel";
import { agentClient, taskActive, taskDisplayState, type AgentSnapshot, type AgentTask, type WorkspaceStatus } from "./core/agentClient";
import AgentWorkspacePanel from "./AgentWorkspacePanel";
import { version } from "../package.json";
import ProtectedRuntimePanel from "./ProtectedRuntimePanel";
import McpPanel from "./McpPanel";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import {
  Bot, ChevronDown, Copy, FilePlus2, Menu, MessageSquarePlus, PanelLeftClose,
  Search, Send, Settings, Square, Trash2, User, X,
} from "lucide-react";
import { core } from "./core/client";
import type {
  Attachment, Conversation, ConversationMode, Locale, MemoryItem, Message,
  PluginManifest, ProviderStatus, Theme,
} from "./core/types";
import { DEFAULT_LOCALE, setLocale } from "./i18n";

type SettingsTab = "models" | "protectedRuntime" | "mcp" | "plugins" | "memory" | "attachments" | "hotkeys" | "appearance" | "language" | "about";

const generationId = () => crypto.randomUUID();

export default function App() {
  const { t, i18n } = useTranslation();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [provider, setProvider] = useState<ProviderStatus | null>(null);
  const [mode, setMode] = useState<ConversationMode>("chat");
  const [model, setModel] = useState("");
  const [modelBusy, setModelBusy] = useState(false);
  const modelEpoch = useRef(0);
  const [draft, setDraft] = useState("");
  const [streamText, setStreamText] = useState("");
  const [activeGeneration, setActiveGeneration] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [sidebar, setSidebar] = useState(true);
  const [settings, setSettings] = useState<SettingsTab | null>(null);
  const [agentSnapshot, setAgentSnapshot] = useState<AgentSnapshot | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentHistory, setAgentHistory] = useState<AgentTask[]>([]);
  const [workspaceStatus, setWorkspaceStatus] = useState<WorkspaceStatus | null>(null);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const workspaceRequest = useRef(0);
  const taskUpdateEpoch = useRef(0);
  const modeEpoch = useRef(0);
  const modeChanging = useRef(false);
  const selectionEpoch = useRef(0);
  const currentRef = useRef(current);
  currentRef.current = current;
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("agent.theme") as Theme) || "system");
  const composer = useRef<HTMLTextAreaElement>(null);

  const refreshConversations = useCallback(async (search = "") => {
    const values = await core.request<Conversation[]>(search ? "search_conversations" : "list_conversations", search ? { query: search } : {});
    setConversations(values);
    return values;
  }, []);

  const loadConversation = useCallback(async (item: Conversation, refreshEpoch?: number) => {
    const modeVersion = modeEpoch.current;
    const modelVersion = modelEpoch.current;
    const epoch = refreshEpoch ?? ++selectionEpoch.current;
    if (epoch !== selectionEpoch.current || modeVersion !== modeEpoch.current) return;
    if (refreshEpoch === undefined) {
      currentRef.current = item;
      setCurrent(item);
      setMode(item.mode);
      setModel(item.model);
      setMessages([]);
      setAgentHistory([]);
    }
    const bundle = await core.request<{ conversation: Conversation; messages: Message[] }>("read_history", { conversation_id: item.id });
    if (epoch !== selectionEpoch.current || modeVersion !== modeEpoch.current) return;
    const loaded = refreshEpoch !== undefined || modelVersion !== modelEpoch.current ? { ...bundle.conversation, model: currentRef.current?.model || item.model } : bundle.conversation;
    currentRef.current = loaded;
    setCurrent(loaded);
    setMode(bundle.conversation.mode);
    if (refreshEpoch === undefined && modelVersion === modelEpoch.current) setModel(loaded.model);
    setMessages(bundle.messages);
    setStreamText("");
    setAgentHistory([]);
    const rows = await core.request<{ event: unknown }[]>("agent_history", { conversation_id: item.id }).catch(() => []);
    if (epoch !== selectionEpoch.current || modeVersion !== modeEpoch.current) return;
    const tasks = new Map<string, AgentTask>();
    for (const row of Array.isArray(rows) ? rows : []) {
      const event = row.event as AgentTask | AgentSnapshot | null;
      const task = event && "task" in event ? event.task : event as AgentTask | null;
      if (task?.task_id && task.conversation_id === item.id && task.state && Array.isArray(task.steps)) tasks.set(task.task_id, task);
    }
    setAgentHistory([...tasks.values()]);
  }, []);

  useEffect(() => {
    const saved = localStorage.getItem("agent.locale") as Locale | null;
    void setLocale(saved || DEFAULT_LOCALE);
    document.documentElement.dataset.theme = theme;
    Promise.all([
      core.request("health"),
      core.request<ProviderStatus>("provider_status"),
      refreshConversations(),
    ]).then(([, providerStatus, list]) => {
      setProvider(providerStatus);
      if (!currentRef.current) setModel(providerStatus.default_model || "");
      setStatus("ready");
      if (list[0]) void loadConversation(list[0]);
    }).catch((reason) => {
      setStatus("offline");
      setError(reason instanceof Error ? reason.message : String(reason));
    });
  }, [loadConversation, refreshConversations]);

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => { if (!currentRef.current) setModel(provider?.default_model || ""); }, [provider?.default_model]);

  useEffect(() => {
    if (workspaceBusy) return;
    const epoch = selectionEpoch.current;
    const request = ++workspaceRequest.current;
    setWorkspaceStatus(null);
    void agentClient.workspace(current?.id).then((next) => {
      if (epoch === selectionEpoch.current && request === workspaceRequest.current && next.conversation_id === (current?.id ?? null)) setWorkspaceStatus(next);
    }).catch((reason) => { if (epoch === selectionEpoch.current && request === workspaceRequest.current) setError(String(reason)); });
  }, [current?.id, workspaceBusy]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const epoch = taskUpdateEpoch.current;
        const scopeEpoch = selectionEpoch.current;
        const scopeRequest = workspaceRequest.current;
        const conversationId = currentRef.current?.id ?? null;
        const next = await agentClient.status();
        if (stopped) return;
        if (epoch !== taskUpdateEpoch.current) { timer = setTimeout(poll, 1000); return; }
        setAgentSnapshot(next);
        if (scopeEpoch === selectionEpoch.current && scopeRequest === workspaceRequest.current && next.workspace_status?.conversation_id === conversationId) {
          workspaceRequest.current++;
          setWorkspaceStatus(next.workspace_status);
        }
        if (taskActive(next.task)) timer = setTimeout(poll, 1000);
        else if (!modeChanging.current && next.task && currentRef.current?.id === next.task.conversation_id) {
          await loadConversation(currentRef.current, selectionEpoch.current);
          await refreshConversations();
        }
      } catch (reason) { if (!stopped) setError(String(reason)); }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [agentSnapshot?.task?.task_id, agentSnapshot?.task?.state, loadConversation, refreshConversations]);

  const agentAction = async (action: () => Promise<AgentSnapshot>) => {
    const epoch = ++taskUpdateEpoch.current;
    setAgentBusy(true);
    try { const next = await action(); if (epoch === taskUpdateEpoch.current) setAgentSnapshot(next); }
    catch (reason) { setError(String(reason)); }
    finally { setAgentBusy(false); }
  };

  const createConversation = useCallback(async (requestedMode: ConversationMode = mode) => {
    if (!model) throw new Error(t("error.noModels"));
    const epoch = selectionEpoch.current;
    const created = await core.request<Conversation>("create_conversation", { title: t("conversation.untitled"), mode: requestedMode, model });
    await refreshConversations();
    if (epoch !== selectionEpoch.current) throw new Error(t("agentTask.selectionChanged"));
    await loadConversation(created);
    return created;
  }, [loadConversation, mode, model, refreshConversations, t]);

  const selectModel = async (next: string) => {
    const conversation = currentRef.current;
    if (modelBusy || next === model || activeGeneration || (taskActive(agentSnapshot?.task) && agentSnapshot?.task?.conversation_id === conversation?.id)) return;
    const epoch = selectionEpoch.current;
    const version = ++modelEpoch.current;
    const previous = model;
    setModel(next);
    if (!conversation) return;
    currentRef.current = { ...conversation, model: next };
    setCurrent(currentRef.current);
    setModelBusy(true);
    try {
      await core.request("set_conversation_settings", { conversation_id: conversation.id, mode: conversation.mode, model: next });
      setConversations((rows) => rows.map((row) => row.id === conversation.id ? { ...row, model: next } : row));
    } catch (reason) {
      if (epoch === selectionEpoch.current && version === modelEpoch.current) {
        currentRef.current = { ...conversation, model: previous };
        setCurrent(currentRef.current);
        setModel(previous);
        setError(String(reason));
      }
    } finally { setModelBusy(false); }
  };

  const selectMode = async (next: ConversationMode) => {
    if (next === mode) return;
    const epoch = selectionEpoch.current;
    modeEpoch.current++;
    modeChanging.current = true;
    const conversation = current;
    setAgentBusy(true);
    try {
      if (next === "chat" && conversation && taskActive(agentSnapshot?.task) && agentSnapshot!.task!.conversation_id === conversation.id) {
        const stopped = await agentClient.cancel(agentSnapshot!.task!.task_id);
        taskUpdateEpoch.current++;
        setAgentSnapshot(stopped);
        if (taskActive(stopped.task)) throw new Error(t("agentTask.cancelRequired"));
      }
      if (conversation) await core.request("set_conversation_settings", { conversation_id: conversation.id, mode: next, model });
      if (epoch !== selectionEpoch.current) return;
      setMode(next);
      if (conversation) setCurrent({ ...conversation, mode: next });
    } catch (reason) { setError(String(reason)); }
    finally { modeChanging.current = false; setAgentBusy(false); }
  };

  const chooseWorkspace = async () => {
    const selection = selectionEpoch.current;
    let operationEpoch = selection;
    setWorkspaceBusy(true);
    try {
      const path = await open({ directory: true, multiple: false });
      if (typeof path !== "string" || selection !== selectionEpoch.current) return;
      const conversation = currentRef.current || await createConversation("agent");
      if (currentRef.current?.id !== conversation.id) return;
      const epoch = selectionEpoch.current;
      operationEpoch = epoch;
      const request = ++workspaceRequest.current;
      // Native selection cancels active work and invalidates old approvals.
      const taskEpoch = ++taskUpdateEpoch.current;
      const next = await agentClient.selectWorkspace(conversation.id, path);
      if (epoch === selectionEpoch.current && request === workspaceRequest.current && next.conversation_id === conversation.id) setWorkspaceStatus(next);
      const snapshot = await agentClient.status();
      if (taskEpoch === taskUpdateEpoch.current) setAgentSnapshot(snapshot);
    } catch (reason) { if (operationEpoch === selectionEpoch.current) setError(String(reason)); }
    finally { setWorkspaceBusy(false); }
  };

  const send = async () => {
    const content = draft.trim();
    if (!content || activeGeneration || agentBusy || modelBusy || workspaceBusy || taskActive(agentSnapshot?.task) || (mode === "agent" && !selectedWorkspace?.enabled)) return;
    setError("");
    try {
      const conversation = current || await createConversation();
      if (mode === "agent") {
        setAgentBusy(true);
        try {
          taskUpdateEpoch.current++;
          setAgentSnapshot(await agentClient.start(conversation.id, content, model));
          setDraft("");
        } finally { setAgentBusy(false); }
        return;
      }
      const sendEpoch = selectionEpoch.current;
      const id = generationId();
      setDraft("");
      setStreamText("");
      setActiveGeneration(id);
      setMessages((value) => [...value, { id: id + "-user", conversation_id: conversation.id, role: "user", content, created_at: new Date().toISOString() }]);
      await core.stream("stream_message", {
        conversation_id: conversation.id, content, mode, model, generation_id: id,
      }, (chunk) => setStreamText((value) => value + chunk));
      if (selectionEpoch.current === sendEpoch) await loadConversation(conversation, sendEpoch);
      await refreshConversations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setActiveGeneration(null);
    }
  };

  const removeConversation = async (item: Conversation) => {
    if (!window.confirm(t("conversation.confirmDelete"))) return;
    await core.request("delete_conversation", { conversation_id: item.id });
    const list = await refreshConversations();
    if (current?.id === item.id) {
      selectionEpoch.current++; currentRef.current = null; setCurrent(null); setModel(provider?.default_model || ""); setMessages([]);
      if (list[0]) await loadConversation(list[0]);
    }
  };

  const renameConversation = async (item: Conversation) => {
    const title = window.prompt(t("conversation.rename"), item.title)?.trim();
    if (!title) return;
    await core.request("rename_conversation", { conversation_id: item.id, title });
    await refreshConversations(query);
  };

  const visibleMessages = useMemo(() => messages.filter((message) => message.role !== "system"), [messages]);
  const selectedWorkspace = workspaceStatus?.conversation_id === (current?.id ?? null) ? workspaceStatus : null;
  const taskHere = agentSnapshot?.task && agentSnapshot.task.conversation_id === current?.id ? agentSnapshot.task : null;
  const taskSnapshot: AgentSnapshot = { workspace: selectedWorkspace?.path ?? null, execution_mode: selectedWorkspace?.enabled ? "approval_required" : "disabled", workspace_status: selectedWorkspace ?? undefined, task: taskHere };
  const companion = new URLSearchParams(window.location.search).get("companion") === "1";
  const activeElsewhere = taskActive(agentSnapshot?.task) && (companion || !taskHere || mode !== "agent");
  const sendDisabled = !draft.trim() || agentBusy || modelBusy || workspaceBusy || taskActive(agentSnapshot?.task) || (mode === "agent" && !selectedWorkspace?.enabled);
  const activeTaskBanner = activeElsewhere && <aside className="active-agent-banner" aria-label={t("agentTask.activeTask")}><strong>{t("agentTask.activeTask")}: {agentSnapshot!.task!.goal}</strong><button onClick={() => { const item = conversations.find((item) => item.id === agentSnapshot!.task!.conversation_id); if (item) void loadConversation(item); }}>{t("agentTask.returnToTask")}</button><button disabled={agentBusy} onClick={() => void agentAction(() => agentClient.cancel(agentSnapshot!.task!.task_id))}>{t("agentTask.stop")}</button></aside>;

  if (companion) {
    return <main className="companion-shell" aria-label={t("companion.title")}>
      <header className="companion-header"><span><Bot size={18}/>{t("companion.title")}</span><button onClick={() => void invoke("show_main")}>{t("companion.openMain")}</button></header>
      <section className="companion-messages">
        {activeTaskBanner}
        {!visibleMessages.length && !streamText ? <div className="companion-empty">{t("companion.empty")}</div> : visibleMessages.slice(-4).map((message) => <MessageView key={message.id} message={message}/>)}
        {streamText && <MessageView message={{ id: "companion-stream", conversation_id: current?.id || "", role: "assistant", content: streamText, created_at: "" }}/>} 
      </section>
      {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError("")}><X size={16}/></button></div>}
      <div className="companion-composer"><textarea ref={composer} placeholder={t("chat.placeholder")} value={draft} rows={2} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }}/>{activeGeneration ? <button className="send-button stop" aria-label={t("chat.stop")} onClick={() => void core.cancel(activeGeneration)}><Square size={17}/></button> : <button className="send-button" aria-label={t("chat.send")} disabled={sendDisabled} onClick={() => void send()}><Send size={18}/></button>}</div>
    </main>;
  }

  return <div className="app-shell">
    <aside className={`sidebar ${sidebar ? "open" : "closed"}`} aria-label={t("nav.history")}>
      <div className="brand"><span className="brand-mark"><Bot size={20}/></span><strong>{t("app.name")}</strong><button className="icon-button mobile-only" onClick={() => setSidebar(false)}><X/></button></div>
      <button className="new-chat" onClick={() => { selectionEpoch.current++; currentRef.current = null; setCurrent(null); setModel(provider?.default_model || ""); setMessages([]); setAgentHistory([]); setDraft(""); composer.current?.focus(); }}><MessageSquarePlus size={18}/>{t("nav.newChat")}</button>
      <label className="search-box"><Search size={16}/><input aria-label={t("nav.search")} placeholder={t("nav.search")} value={query} onChange={(event) => { const next = event.target.value; setQuery(next); void refreshConversations(next); }}/></label>
      <div className="section-label">{t("nav.projects")}</div>
      <div className="project-list"><button className="project-chip" onClick={() => void refreshConversations()}>All</button>{[...new Set(conversations.map((item) => item.project_id).filter(Boolean))].map((project) => <button className="project-chip" key={project}>{project}</button>)}</div>
      <div className="section-label">{t("nav.history")}</div>
      <div className="conversation-list">
        {conversations.map((item) => <div key={item.id} className={`conversation-row ${current?.id === item.id ? "active" : ""}`}>
          <button className="conversation-main" onClick={() => void loadConversation(item)}><span>{item.title}</span><small>{item.mode === "agent" ? "Agent" : item.model}</small></button>
          <button className="mini" aria-label={t("conversation.rename")} onClick={() => void renameConversation(item)}>···</button>
          <button className="mini danger" aria-label={t("conversation.delete")} onClick={() => void removeConversation(item)}><Trash2 size={14}/></button>
        </div>)}
      </div>
      <div className="sidebar-footer"><button onClick={() => setSettings("models")}><Settings size={18}/>{t("nav.settings")}</button></div>
    </aside>

    <main className="workspace">
      <header className="topbar">
        <button className="icon-button" aria-label="menu" onClick={() => setSidebar(!sidebar)}>{sidebar ? <PanelLeftClose/> : <Menu/>}</button>
        <div className="mode-switch" role="group">
          <button disabled={agentBusy || modelBusy || workspaceBusy} className={mode === "chat" ? "selected" : ""} onClick={() => void selectMode("chat")}>{t("chat.modeChat")}</button>
          <button disabled={agentBusy || modelBusy || workspaceBusy} className={mode === "agent" ? "selected" : ""} onClick={() => void selectMode("agent")}>{t("chat.modeAgent")}</button>
        </div>
        <label className="model-select"><span>{t("chat.model")}</span><select disabled={modelBusy || agentBusy || !!activeGeneration || taskActive(taskHere)} value={model} onChange={(event) => void selectModel(event.target.value)}>{model && !provider?.models.includes(model) && <option value={model}>{model} ({t("agentTask.modelUnavailable")})</option>}{provider?.models.map((id) => <option key={id}>{id}</option>)}</select><ChevronDown size={14}/></label>
        <span className={`status-dot ${status}`}>{t(`app.${status}`)}</span>
      </header>

      <section className="conversation-view">
        {provider && (!provider.connected || (!!model && !provider.models.includes(model))) && <p role="alert" className="notice">{t("agentTask.modelUnavailable")}: {provider.error || model}</p>}
        {activeTaskBanner}
        {mode === "agent" && <AgentWorkspacePanel status={selectedWorkspace} busy={workspaceBusy || agentBusy} onChoose={() => void chooseWorkspace()}/>}
        {mode === "agent" && <AgentTaskPanel snapshot={taskSnapshot} busy={agentBusy || workspaceBusy} approvalEnabled={!!selectedWorkspace?.enabled} onDecide={(proposalId, approved) => { if (taskHere && selectedWorkspace?.enabled) void agentAction(() => agentClient.decide(taskHere.task_id, proposalId, approved)); }} onCancel={() => { if (taskHere) void agentAction(() => agentClient.cancel(taskHere.task_id)); }}/>} 
        {mode === "agent" && agentHistory.filter((task) => task.conversation_id === current?.id && task.task_id !== agentSnapshot?.task?.task_id).map((task) => <details key={task.task_id}><summary>{t("agentTask.history")}: {task.goal} · {t(`agentTask.${taskDisplayState(task) === "no_operation" ? "no_operation_status" : taskDisplayState(task)}`)}</summary><AgentTaskPanel snapshot={{ workspace: task.workspace || null, execution_mode: "disabled", task }} busy={false} readOnly onDecide={() => undefined} onCancel={() => undefined}/></details>)}
        {!visibleMessages.length && !streamText ? <div className="empty-state"><span className="hero-mark"><Bot size={32}/></span><h1>{t("chat.empty")}</h1><p>{mode === "agent" ? t("agentTask.goalHint") : t("app.ready")}</p></div> :
          <div className="messages">{visibleMessages.map((message) => <MessageView key={message.id} message={message}/>) }{streamText && <MessageView message={{ id: "stream", conversation_id: current?.id || "", role: "assistant", content: streamText, created_at: "" }}/>}</div>}
      </section>

      <div className="composer-wrap">
        {error && <div className="error-banner"><strong>{t("error.title")}</strong><span>{error}</span><button onClick={() => setError("")}><X size={16}/></button></div>}
        <div className="composer">
          <button className="icon-button" aria-label={t("chat.attach")} onClick={() => setSettings("attachments")}><FilePlus2/></button>
          <textarea ref={composer} placeholder={t("chat.placeholder")} value={draft} rows={1} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }}/>
          {activeGeneration ? <button className="send-button stop" aria-label={t("chat.stop")} onClick={() => void core.cancel(activeGeneration)}><Square size={17}/></button> : <button className="send-button" aria-label={t("chat.send")} disabled={sendDisabled} onClick={() => void send()}><Send size={18}/></button>}
        </div>
        <small className="composer-note">{mode === "agent" ? t("agentTask.approvalNote") : t("chat.disclaimer")}</small>
      </div>
    </main>
    {settings && <SettingsModal tab={settings} setTab={setSettings} onClose={() => setSettings(null)} provider={provider} setProvider={setProvider} conversation={current} theme={theme} setTheme={setTheme} locale={i18n.language as Locale}/>} 
  </div>;
}

function MessageView({ message }: { message: Message }) {
  const { t } = useTranslation();
  return <article className={`message ${message.role}`}>
    <div className="avatar">{message.role === "assistant" ? <Bot size={18}/> : <User size={18}/>}</div>
    <div className="message-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ code: ({ children, ...props }) => <code {...props}>{children}</code> }}>{message.content}</ReactMarkdown></div>
    {message.role === "assistant" && <button className="copy-button" aria-label={t("chat.copy")} onClick={() => void navigator.clipboard.writeText(message.content)}><Copy size={15}/></button>}
  </article>;
}

function SettingsModal({ tab, setTab, onClose, provider, setProvider, conversation, theme, setTheme, locale }: { tab: SettingsTab; setTab: (tab: SettingsTab) => void; onClose: () => void; provider: ProviderStatus | null; setProvider: (value: ProviderStatus) => void; conversation: Conversation | null; theme: Theme; setTheme: (value: Theme) => void; locale: Locale }) {
  const { t } = useTranslation();
  const tabs: SettingsTab[] = ["models", "protectedRuntime", "mcp", "plugins", "memory", "attachments", "hotkeys", "appearance", "language", "about"];
  return <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="settings-modal" role="dialog" aria-label={t("settings.title")}><header><h2>{t("settings.title")}</h2><button className="icon-button" onClick={onClose}><X/></button></header><div className="settings-layout"><nav>{tabs.map((name) => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{t(name === "protectedRuntime" ? "protectedRuntime.title" : name === "mcp" ? "mcp.title" : `settings.${name}`)}</button>)}</nav><div className="settings-page">
    {tab === "protectedRuntime" && <ProtectedRuntimePanel/>}
    {tab === "mcp" && <McpPanel/>}
    {tab === "models" && <ModelsPanel provider={provider} onRefresh={async () => setProvider(await core.request("provider_status"))}/>} 
    {tab === "plugins" && <PluginsPanel/>}{tab === "memory" && <MemoryPanel conversation={conversation}/>} 
    {tab === "attachments" && <AttachmentsPanel conversation={conversation}/>} 
    {tab === "hotkeys" && <HotkeyPanel/>}
    {tab === "appearance" && <label className="field"><span>{t("settings.appearance")}</span><select value={theme} onChange={(event) => { const next = event.target.value as Theme; setTheme(next); localStorage.setItem("agent.theme", next); document.documentElement.dataset.theme = next; }}><option value="system">{t("settings.themeSystem")}</option><option value="light">{t("settings.themeLight")}</option><option value="dark">{t("settings.themeDark")}</option></select></label>}
    {tab === "language" && <label className="field"><span>{t("settings.language")}</span><select aria-label={t("settings.language")} value={locale} onChange={(event) => void setLocale(event.target.value as Locale)}><option value="zh-TW">繁體中文</option><option value="en">English</option><option value="zh-CN">简体中文</option></select></label>}
    {tab === "about" && <div className="about"><span className="hero-mark"><Bot/></span><h3>{t("app.name")}</h3><p>{t("about.version")}: {version}</p><p>{t("about.modelService")}</p><p>{t("about.safety")}</p></div>}
  </div></div></section></div>;
}

function ModelsPanel({ provider, onRefresh }: { provider: ProviderStatus | null; onRefresh: () => void }) {
  const { t } = useTranslation();
  return <div><h3>{t("settings.models")}</h3><div className="card"><div className="card-line"><strong>{provider?.name || "Ollama"}</strong><span className={`pill ${provider?.connected ? "good" : ""}`}>{t(provider?.connected ? "provider.connected" : "provider.disconnected")}</span></div><p>{provider?.error}</p><label className="field"><span>{t("provider.defaultModel")}</span><select value={provider?.default_model || ""} onChange={(event) => void core.request("set_default_model", { model: event.target.value }).then(onRefresh)}>{provider?.models.map((id) => <option key={id}>{id}</option>)}</select></label><button onClick={onRefresh}>{t("provider.refresh")}</button></div></div>;
}

function PluginsPanel() {
  const { t } = useTranslation(); const [items, setItems] = useState<PluginManifest[]>([]);
  const reload = useCallback(() => void core.request<PluginManifest[]>("list_plugins").then(setItems), []); useEffect(reload, [reload]);
  const edit = async (existing?: PluginManifest) => { const plugin_id = window.prompt("Plugin ID", existing?.plugin_id || "")?.trim(); if (!plugin_id) return; const name = window.prompt("Name", existing?.name || plugin_id)?.trim() || plugin_id; await core.request("upsert_plugin", { manifest: { schema_version: "1.0", plugin_id, name, source_type: existing?.source_type || "mcp", version: existing?.version || "1.0.0", description: existing?.description || "", enabled: existing?.enabled ?? false, approval_required: true, executable: false, config: existing?.config || {} } }); reload(); };
  return <div><div className="page-title"><h3>{t("settings.plugins")}</h3><button onClick={() => void edit()}>{t("plugin.add")}</button></div><p className="notice">{t("plugin.noStart")}</p>{items.map((item) => <div className="card" key={item.plugin_id}><div className="card-line"><div><strong>{item.name}</strong><code>{item.plugin_id}</code></div><span className="pill">{item.connection_status || t("plugin.configuredOnly")}</span></div><p>{item.description}</p><div className="row-actions"><button onClick={() => void core.request("upsert_plugin", { manifest: { ...item, enabled: !item.enabled, executable: false } }).then(reload)}>{item.enabled ? t("common.disable") : t("common.enable")}</button><button onClick={() => void edit(item)}>{t("plugin.edit")}</button><button className="danger" onClick={() => void core.request("remove_plugin", { plugin_id: item.plugin_id }).then(reload)}>{t("plugin.remove")}</button></div></div>)}</div>;
}

function MemoryPanel({ conversation }: { conversation: Conversation | null }) {
  const { t } = useTranslation(); const [items, setItems] = useState<MemoryItem[]>([]); const [query, setQuery] = useState(""); const [chatEnabled, setChatEnabled] = useState(false);
  const reload = useCallback(() => { void core.request<MemoryItem[]>("list_memories", { query }).then(setItems); void core.request<{ chat_enabled: boolean }>("get_memory_settings", { conversation_id: conversation?.id || null }).then((value) => setChatEnabled(value.chat_enabled)); }, [conversation, query]); useEffect(reload, [reload]);
  const edit = async (item?: MemoryItem) => { const content = window.prompt(t(item ? "memory.edit" : "memory.add"), item?.content || "")?.trim(); if (!content) return; if (item) await core.request("update_memory", { memory_id: item.memory_id, memory_type: item.memory_type, scope: item.scope, content, enabled: item.enabled }); else await core.request("create_memory", { memory_type: "long_term", scope: "global", content, source_conversation_id: conversation?.id || null, enabled: true }); reload(); };
  return <div><div className="page-title"><h3>{t("settings.memory")}</h3><button onClick={() => void edit()}>{t("memory.add")}</button></div><label className="toggle"><input type="checkbox" checked={chatEnabled} onChange={(event) => { const enabled = event.target.checked; void core.request("set_memory_chat_enabled", { enabled }).then(reload); }}/>{t("memory.chatEnabled")}</label><input className="wide-input" placeholder={t("memory.search")} value={query} onChange={(event) => setQuery(event.target.value)}/>{items.map((item) => <div className="card" key={item.memory_id}><div className="card-line"><span className="pill">{item.scope}</span><label className="toggle"><input type="checkbox" checked={item.enabled} onChange={(event) => void core.request("set_memory_enabled", { memory_id: item.memory_id, enabled: event.target.checked }).then(reload)}/>{t("memory.enabled")}</label></div><p>{item.content}</p><div className="row-actions"><button onClick={() => void edit(item)}>{t("memory.edit")}</button><button className="danger" onClick={() => void core.request("delete_memory", { memory_id: item.memory_id }).then(reload)}>{t("memory.delete")}</button></div></div>)}</div>;
}

function AttachmentsPanel({ conversation }: { conversation: Conversation | null }) {
  const { t } = useTranslation(); const [items, setItems] = useState<Attachment[]>([]); const [preview, setPreview] = useState<string | null>(null);
  const reload = useCallback(() => { if (conversation) void core.request<Attachment[]>("list_attachments", { conversation_id: conversation.id }).then(setItems); else setItems([]); }, [conversation]); useEffect(reload, [reload]);
  const choose = async () => { if (!conversation) return; const selected = await open({ multiple: false, filters: [{ name: "Preview", extensions: ["png", "gif", "jpg", "jpeg", "txt", "md", "pdf"] }] }); if (typeof selected === "string") { await core.request("add_attachment", { conversation_id: conversation.id, path: selected }); reload(); } };
  const show = async (item: Attachment) => { const value = await core.request<{ supported: boolean; media_type?: string; data_base64?: string }>("attachment_preview", { attachment_id: item.id }); setPreview(value.supported ? `data:${value.media_type};base64,${value.data_base64}` : null); };
  return <div><div className="page-title"><h3>{t("settings.attachments")}</h3><button disabled={!conversation} onClick={() => void choose()}>{t("attachment.choose")}</button></div><p className="notice">{t("chat.disclaimer")}</p>{preview && <img className="attachment-preview" src={preview} alt={t("attachment.preview")}/>} {!items.length && <p>{t("attachment.empty")}</p>}{items.map((item) => <div className="card card-line" key={item.id}><div><strong>{item.display_name}</strong><small>{Math.ceil(item.size_bytes / 1024)} KB · {t("attachment.notSent")}</small></div><div className="row-actions"><button onClick={() => void show(item)}>{t("attachment.preview")}</button><button className="danger" onClick={() => void core.request("delete_attachment", { attachment_id: item.id }).then(reload)}>{t("attachment.remove")}</button></div></div>)}</div>;
}

function HotkeyPanel() {
  const { t } = useTranslation(); const [choice, setChoice] = useState("Alt+Space"); const [message, setMessage] = useState("");
  useEffect(() => { void core.request<{ hotkey: string }>("get_desktop_settings").then((value) => setChoice(value.hotkey)); }, []);
  const register = async () => { try { await core.request("set_hotkey", { hotkey: choice }); await invoke("register_global_hotkey", { hotkey: choice }); setMessage(t("app.ready")); } catch { setMessage(t("hotkey.unavailable")); } };
  return <div><h3>{t("hotkey.title")}</h3><label className="field"><span>{t("hotkey.title")}</span><select value={choice} onChange={(event) => setChoice(event.target.value)}><option>Alt+Space</option><option>Ctrl+Alt+Space</option><option>Ctrl+Shift+Space</option></select></label><div className="row-actions"><button onClick={() => void register()}>{t("hotkey.register")}</button><button onClick={() => void invoke("release_global_hotkey")}>{t("hotkey.release")}</button><button onClick={() => void invoke("show_companion")}>{t("hotkey.companion")}</button></div>{message && <p>{message}</p>}</div>;
}


