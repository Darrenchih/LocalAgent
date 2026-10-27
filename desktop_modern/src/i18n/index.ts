import { agentTaskTranslations } from "./agentTask";
import { protectedRuntimeTranslations } from "./protectedRuntime";
import { mcpTranslations } from "./mcp";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import type { Locale } from "../core/types";

export const DEFAULT_LOCALE: Locale = "zh-TW";

const zhTW = {
  app: { name: "本機 AI 助理", ready: "已就緒", loading: "載入中", offline: "無法連線" },
  nav: { newChat: "新增對話", search: "搜尋對話", projects: "專案", settings: "設定", history: "對話記錄" },
  chat: { placeholder: "輸入訊息…", send: "傳送", stop: "停止", copy: "複製", copied: "已複製", empty: "開始新的對話", modeChat: "聊天", modeAgent: "Agent", model: "模型", attach: "加入附件", disclaimer: "附件只供本機預覽，不會傳送給模型。" },
  conversation: { rename: "重新命名", delete: "刪除", confirmDelete: "確定刪除此對話？", untitled: "新對話" },
  settings: { title: "設定", models: "模型與提供者", plugins: "外掛", memory: "記憶", attachments: "附件", hotkeys: "快速鍵", appearance: "外觀", language: "語言", about: "關於", close: "關閉", themeSystem: "跟隨系統", themeLight: "淺色", themeDark: "深色" },
  provider: { connected: "已連線", disconnected: "未連線", defaultModel: "預設模型", refresh: "重新整理" },
  plugin: { add: "新增外掛", edit: "編輯", remove: "移除", enabled: "啟用", disabled: "停用", permissions: "權限", configuredOnly: "僅完成設定，未連線", noStart: "此版本不會啟動 MCP 伺服器。" },
  memory: { add: "新增記憶", edit: "編輯記憶", delete: "刪除記憶", search: "搜尋記憶", enabled: "啟用", chatEnabled: "在聊天中使用記憶", global: "全域", project: "專案", projectId: "專案 ID", content: "內容", source: "來源對話" },
  attachment: { choose: "選擇檔案", preview: "預覽", remove: "移除", empty: "尚無附件", notSent: "不會傳送給模型", unavailable: "無法預覽" },
  agent: { activity: "Agent 活動", toolRequest: "工具要求", humanApproval: "真人批准", approve: "批准（不建立授權）", reject: "拒絕", formalDisabled: "正式執行已停用", offlineOnly: "僅離線重播與受限批准", result: "結果", status: "狀態", risk: "風險", operation: "操作", identifiers: "識別資訊", collapse: "收合", expand: "展開" },
  hotkey: { title: "全域快速鍵", register: "註冊", release: "解除", companion: "顯示小視窗", unavailable: "快速鍵無法註冊" },
  companion: { title: "快速聊天", openMain: "開啟完整視窗", empty: "輸入訊息開始對話" },
  common: { save: "儲存", cancel: "取消", confirm: "確認", add: "新增", edit: "編輯", remove: "移除", enable: "啟用", disable: "停用", on: "開", off: "關" },
  error: { title: "發生錯誤", coreUnavailable: "Agent Core 無法使用", retry: "重試", invalidRequest: "要求格式不正確", noModels: "Ollama 中沒有可用模型" },
  status: { pending: "等待中", running: "執行中", completed: "已完成", failed: "失敗", cancelled: "已取消", rejected: "已拒絕", approvedNoGrant: "已批准（未建立 Grant）" },
  about: { version: "版本", modelService: "未隨附 Ollama 或模型。請連接您現有的相容模型服務。", safety: "Agent 工具限於隔離工作資料夾，每次操作均需批准。受保護管理不會啟用工具。", fallback: "仍可使用 Tkinter 備援介面。" },
};

const en: typeof zhTW = {
  app: { name: "Local AI Assistant", ready: "Ready", loading: "Loading", offline: "Unavailable" },
  nav: { newChat: "New chat", search: "Search conversations", projects: "Projects", settings: "Settings", history: "History" },
  chat: { placeholder: "Message…", send: "Send", stop: "Stop", copy: "Copy", copied: "Copied", empty: "Start a new conversation", modeChat: "Chat", modeAgent: "Agent", model: "Model", attach: "Attach", disclaimer: "Attachments are local previews and are not sent to the model." },
  conversation: { rename: "Rename", delete: "Delete", confirmDelete: "Delete this conversation?", untitled: "New conversation" },
  settings: { title: "Settings", models: "Models & Providers", plugins: "Plugins", memory: "Memory", attachments: "Attachments", hotkeys: "Hotkeys", appearance: "Appearance", language: "Language", about: "About", close: "Close", themeSystem: "System", themeLight: "Light", themeDark: "Dark" },
  provider: { connected: "Connected", disconnected: "Disconnected", defaultModel: "Default model", refresh: "Refresh" },
  plugin: { add: "Add plugin", edit: "Edit", remove: "Remove", enabled: "Enabled", disabled: "Disabled", permissions: "Permissions", configuredOnly: "Configured, not connected", noStart: "This build does not start MCP servers." },
  memory: { add: "Add memory", edit: "Edit memory", delete: "Delete memory", search: "Search memory", enabled: "Enabled", chatEnabled: "Use memory in chat", global: "Global", project: "Project", projectId: "Project ID", content: "Content", source: "Source conversation" },
  attachment: { choose: "Choose file", preview: "Preview", remove: "Remove", empty: "No attachments", notSent: "Not sent to model", unavailable: "Preview unavailable" },
  agent: { activity: "Agent activity", toolRequest: "Tool request", humanApproval: "Human approval", approve: "Approve (no authorization)", reject: "Reject", formalDisabled: "Formal execution disabled", offlineOnly: "Offline replay and restricted approval only", result: "Result", status: "Status", risk: "Risk", operation: "Operation", identifiers: "Identifiers", collapse: "Collapse", expand: "Expand" },
  hotkey: { title: "Global hotkey", register: "Register", release: "Release", companion: "Show companion", unavailable: "Hotkey could not be registered" },
  companion: { title: "Quick chat", openMain: "Open full window", empty: "Type a message to start" },
  common: { save: "Save", cancel: "Cancel", confirm: "Confirm", add: "Add", edit: "Edit", remove: "Remove", enable: "Enable", disable: "Disable", on: "On", off: "Off" },
  error: { title: "Something went wrong", coreUnavailable: "Agent Core is unavailable", retry: "Retry", invalidRequest: "Invalid request", noModels: "No Ollama models are available" },
  status: { pending: "Pending", running: "Running", completed: "Completed", failed: "Failed", cancelled: "Cancelled", rejected: "Rejected", approvedNoGrant: "Approved (no Grant)" },
  about: { version: "Version", modelService: "Ollama and models are not bundled. Connect your existing compatible model service.", safety: "Agent tools require approval for each call and operate in an isolated workspace. Protected management does not enable tools.", fallback: "The Tkinter fallback remains available." },
};

const zhCN: typeof zhTW = {
  app: { name: "本地 AI 助手", ready: "已就绪", loading: "加载中", offline: "无法连接" },
  nav: { newChat: "新建对话", search: "搜索对话", projects: "项目", settings: "设置", history: "对话记录" },
  chat: { placeholder: "输入消息…", send: "发送", stop: "停止", copy: "复制", copied: "已复制", empty: "开始新对话", modeChat: "聊天", modeAgent: "Agent", model: "模型", attach: "添加附件", disclaimer: "附件仅供本地预览，不会发送给模型。" },
  conversation: { rename: "重命名", delete: "删除", confirmDelete: "确定删除此对话？", untitled: "新对话" },
  settings: { title: "设置", models: "模型与提供商", plugins: "插件", memory: "记忆", attachments: "附件", hotkeys: "快捷键", appearance: "外观", language: "语言", about: "关于", close: "关闭", themeSystem: "跟随系统", themeLight: "浅色", themeDark: "深色" },
  provider: { connected: "已连接", disconnected: "未连接", defaultModel: "默认模型", refresh: "刷新" },
  plugin: { add: "添加插件", edit: "编辑", remove: "移除", enabled: "启用", disabled: "停用", permissions: "权限", configuredOnly: "仅完成配置，未连接", noStart: "此版本不会启动 MCP 服务器。" },
  memory: { add: "添加记忆", edit: "编辑记忆", delete: "删除记忆", search: "搜索记忆", enabled: "启用", chatEnabled: "在聊天中使用记忆", global: "全局", project: "项目", projectId: "项目 ID", content: "内容", source: "来源对话" },
  attachment: { choose: "选择文件", preview: "预览", remove: "移除", empty: "暂无附件", notSent: "不会发送给模型", unavailable: "无法预览" },
  agent: { activity: "Agent 活动", toolRequest: "工具请求", humanApproval: "人工批准", approve: "批准（不创建授权）", reject: "拒绝", formalDisabled: "正式执行已停用", offlineOnly: "仅离线重放与受限批准", result: "结果", status: "状态", risk: "风险", operation: "操作", identifiers: "标识信息", collapse: "收起", expand: "展开" },
  hotkey: { title: "全局快捷键", register: "注册", release: "释放", companion: "显示小窗口", unavailable: "快捷键无法注册" },
  companion: { title: "快速聊天", openMain: "打开完整窗口", empty: "输入消息开始对话" },
  common: { save: "保存", cancel: "取消", confirm: "确认", add: "添加", edit: "编辑", remove: "移除", enable: "启用", disable: "停用", on: "开", off: "关" },
  error: { title: "发生错误", coreUnavailable: "Agent Core 无法使用", retry: "重试", invalidRequest: "请求格式不正确", noModels: "Ollama 中没有可用模型" },
  status: { pending: "等待中", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消", rejected: "已拒绝", approvedNoGrant: "已批准（未创建 Grant）" },
  about: { version: "版本", modelService: "未附带 Ollama 或模型。请连接您现有的兼容模型服务。", safety: "Agent 工具限于隔离工作文件夹，每次操作均需批准。受保护管理不会启用工具。", fallback: "仍可使用 Tkinter 备用界面。" },
};

export const resources = {
  "zh-TW": { translation: { ...zhTW, agentTask: agentTaskTranslations["zh-TW"], mcp: mcpTranslations["zh-TW"], protectedRuntime: protectedRuntimeTranslations["zh-TW"] } },
  en: { translation: { ...en, agentTask: agentTaskTranslations.en, mcp: mcpTranslations.en, protectedRuntime: protectedRuntimeTranslations.en } },
  "zh-CN": { translation: { ...zhCN, agentTask: agentTaskTranslations["zh-CN"], mcp: mcpTranslations["zh-CN"], protectedRuntime: protectedRuntimeTranslations["zh-CN"] } },
};

const savedLocale = typeof localStorage === "undefined" ? null : localStorage.getItem("agent.locale");
const initialLocale = savedLocale && savedLocale in resources ? savedLocale : DEFAULT_LOCALE;

void i18n.use(initReactI18next).init({ resources, lng: initialLocale, fallbackLng: DEFAULT_LOCALE, interpolation: { escapeValue: false } });

export async function setLocale(locale: Locale): Promise<void> {
  localStorage.setItem("agent.locale", locale);
  await i18n.changeLanguage(locale);
}

export default i18n;

