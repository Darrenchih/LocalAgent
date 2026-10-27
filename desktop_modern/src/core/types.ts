export type Locale = "zh-TW" | "en" | "zh-CN";
export type Theme = "system" | "light" | "dark";
export type ConversationMode = "chat" | "agent";

export interface Conversation {
  id: string;
  title: string;
  mode: ConversationMode;
  model: string;
  project_id?: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface ProviderStatus {
  name: string;
  type: string;
  connected: boolean;
  models: string[];
  default_model: string;
  error: string | null;
}

export interface PluginManifest {
  schema_version: "1.0";
  plugin_id: string;
  name: string;
  source_type: "built_in" | "mcp" | "custom";
  version: string;
  description: string;
  enabled: boolean;
  approval_required: boolean;
  executable: false;
  config: Record<string, unknown>;
  connection_status?: string;
}

export interface MemoryItem {
  memory_id: string;
  memory_type: "long_term" | "project";
  scope: string;
  content: string;
  enabled: boolean;
  source_conversation_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Attachment {
  id: string;
  conversation_id: string;
  display_name: string;
  original_path: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  capability_status: "preview_only_not_sent_to_model";
}

export interface ProtectedEvent {
  event_type: string;
  status: string;
  run_id: string;
  call_id: string;
  thread_id: string;
  request_id: string;
  tool_name: string;
  operation: string;
  risk: string;
  source_kind: string;
  timestamp: string;
}
