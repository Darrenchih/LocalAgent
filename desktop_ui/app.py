from __future__ import annotations

import threading
import tkinter as tk
import uuid
import json
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from .ipc_client import CoreClient
from .windows_hotkey import GlobalHotkey, HOTKEYS


class DesktopAgentApp:
    def __init__(self, root: tk.Tk, project_root: Path) -> None:
        self.root = root
        self.client = CoreClient(project_root)
        self.conversations: list[dict[str, Any]] = []
        self.current_id: str | None = None
        self.current_events: list[dict[str, Any]] = []
        self.generation_id: str | None = None
        self.last_assistant = ""
        self.companion: tk.Toplevel | None = None
        self.companion_generation: str | None = None
        self.mode = tk.StringVar(value="chat")
        self.model = tk.StringVar()
        self.search = tk.StringVar()
        self.status = tk.StringVar(value="Starting Agent Core…")
        self._build()
        self.hotkey = GlobalHotkey(root, self.show_companion)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self) -> None:
        self.root.title("Local Ollama Agent")
        self.root.geometry("1120x760")
        self.root.minsize(880, 600)
        style = ttk.Style()
        style.theme_use("vista" if "vista" in style.theme_names() else "clam")

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        sidebar = ttk.Frame(outer, width=250)
        sidebar.pack(side="left", fill="y", padx=(0, 10))
        ttk.Button(sidebar, text="＋ New conversation", command=self.new_conversation).pack(fill="x")
        search_row = ttk.Frame(sidebar)
        search_row.pack(fill="x", pady=7)
        search_box = ttk.Entry(search_row, textvariable=self.search)
        search_box.pack(side="left", fill="x", expand=True)
        search_box.bind("<Return>", lambda _event: self.search_conversations())
        ttk.Button(search_row, text="Search", width=7, command=self.search_conversations).pack(side="left", padx=(5, 0))
        self.conversation_list = tk.Listbox(sidebar, width=31, activestyle="none", exportselection=False)
        self.conversation_list.pack(fill="both", expand=True)
        self.conversation_list.bind("<<ListboxSelect>>", self._select_conversation)
        manage = ttk.Frame(sidebar)
        manage.pack(fill="x", pady=(7, 0))
        ttk.Button(manage, text="Rename", command=self.rename_conversation).pack(side="left", fill="x", expand=True)
        ttk.Button(manage, text="Delete", command=self.delete_conversation).pack(side="left", fill="x", expand=True, padx=(5, 0))

        main = ttk.Frame(outer)
        main.pack(side="left", fill="both", expand=True)
        toolbar = ttk.Frame(main)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="Mode:").pack(side="left")
        self.mode_box = ttk.Combobox(toolbar, textvariable=self.mode, values=("chat", "agent"), state="readonly", width=9)
        self.mode_box.pack(side="left", padx=(5, 18))
        self.mode_box.bind("<<ComboboxSelected>>", self._settings_changed)
        ttk.Label(toolbar, text="Model:").pack(side="left")
        self.model_box = ttk.Combobox(toolbar, textvariable=self.model, state="readonly", width=28)
        self.model_box.pack(side="left", padx=5)
        self.model_box.bind("<<ComboboxSelected>>", self._settings_changed)
        desktopbar = ttk.Frame(main)
        desktopbar.pack(fill="x", pady=(0, 8))
        ttk.Button(desktopbar, text="Plugins", command=self.open_plugins).pack(side="left")
        ttk.Button(desktopbar, text="Provider", command=self.open_provider).pack(side="left", padx=4)
        ttk.Button(desktopbar, text="Companion", command=self.show_companion).pack(side="left")
        ttk.Button(desktopbar, text="Hotkey", command=self.open_hotkey_settings).pack(side="left", padx=4)
        ttk.Button(desktopbar, text="Attachments", command=self.open_attachments).pack(side="left")
        ttk.Button(desktopbar, text="Memory", command=self.open_memory).pack(side="left", padx=4)
        ttk.Button(desktopbar, text="Copy last reply", command=self.copy_last_reply).pack(side="right")

        vertical = ttk.Panedwindow(main, orient="vertical")
        vertical.pack(fill="both", expand=True)
        message_frame = ttk.Frame(vertical)
        vertical.add(message_frame, weight=4)
        scroll = ttk.Scrollbar(message_frame)
        scroll.pack(side="right", fill="y")
        self.messages = tk.Text(
            message_frame, wrap="word", state="disabled", padx=16, pady=14,
            font=("Segoe UI", 10), bg="white", relief="solid", borderwidth=1,
            yscrollcommand=scroll.set,
        )
        self.messages.pack(fill="both", expand=True)
        scroll.configure(command=self.messages.yview)
        self.messages.tag_configure("user", foreground="#1d4ed8", spacing1=10, spacing3=8)
        self.messages.tag_configure("assistant", foreground="#18181b", spacing1=10, spacing3=8)
        self.messages.tag_configure("notice", foreground="#92400e", background="#fffbeb", spacing1=8, spacing3=8)

        activity = ttk.LabelFrame(vertical, text="Agent Activity (protected offline replay)", padding=6)
        vertical.add(activity, weight=1)
        self.activity_tree = ttk.Treeview(activity, columns=("tool", "event", "approval", "source", "summary"), show="headings", height=5)
        for key, label, width in (
            ("tool", "Tool", 125), ("event", "Event", 125),
            ("approval", "Approval", 90), ("source", "Source", 135), ("summary", "Summary", 245),
        ):
            self.activity_tree.heading(key, text=label)
            self.activity_tree.column(key, width=width, anchor="w")
        self.activity_tree.pack(side="left", fill="both", expand=True)
        activity_actions = ttk.Frame(activity)
        activity_actions.pack(side="left", fill="y", padx=(7, 0))
        ttk.Button(activity_actions, text="Stage Host request", command=self.stage_protected_request).pack(fill="x")
        ttk.Button(activity_actions, text="Review selected", command=self.review_protected_event).pack(fill="x", pady=(5, 0))
        ttk.Button(activity_actions, text="Replay approved evidence", command=self.replay_protected_evidence).pack(fill="x", pady=(5, 0))

        compose = ttk.Frame(main)
        compose.pack(fill="x", pady=(8, 0))
        self.input = tk.Text(compose, height=4, wrap="word", font=("Segoe UI", 10))
        self.input.pack(side="left", fill="x", expand=True)
        self.input.bind("<Return>", self._enter_to_send)
        self.input.bind("<Shift-Return>", lambda _event: None)
        buttons = ttk.Frame(compose)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        self.send_button = ttk.Button(buttons, text="Send", command=self.send)
        self.send_button.pack(fill="both", expand=True)
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_generation, state="disabled")
        self.stop_button.pack(fill="both", expand=True, pady=(5, 0))
        ttk.Label(main, textvariable=self.status, anchor="w").pack(fill="x", pady=(6, 0))

    def _async(self, work: Callable[[], Any], done: Callable[[Any], None] | None = None) -> None:
        def run() -> None:
            try:
                result = work()
            except Exception as error:
                self.root.after(0, lambda: self._show_error(str(error)))
            else:
                if done:
                    self.root.after(0, lambda: done(result))
        threading.Thread(target=run, daemon=True).start()

    def _initial_load(self) -> dict[str, Any]:
        return {
            "health": self.client.request("health"),
            "models": self.client.request("list_models"),
            "conversations": self.client.request("list_conversations"),
            "settings": self.client.request("get_desktop_settings"),
        }

    def _apply_initial(self, data: dict[str, Any]) -> None:
        self.model_box["values"] = data["models"]
        if data["models"]:
            self.model.set(data["models"][0])
        self._set_conversations(data["conversations"])
        self._apply_hotkey(data["settings"]["hotkey"])
        if self.conversations:
            self.conversation_list.selection_set(0)
            self._load_selected(0)

    def _set_conversations(self, values: list[dict[str, Any]]) -> None:
        selected_id = self.current_id
        self.conversations = values
        self.conversation_list.delete(0, "end")
        for index, item in enumerate(values):
            self.conversation_list.insert("end", f"{item['title']}  ·  {item['mode']}")
            if item["id"] == selected_id:
                self.conversation_list.selection_set(index)

    def _refresh_conversations(self) -> list[dict[str, Any]]:
        query = self.search.get().strip()
        if query:
            return self.client.request("search_conversations", query=query)
        return self.client.request("list_conversations")

    def search_conversations(self) -> None:
        self.status.set("Searching…")
        self._async(self._refresh_conversations, self._search_done)

    def _search_done(self, values: list[dict[str, Any]]) -> None:
        self._set_conversations(values)
        self.status.set(f"{len(values)} conversation(s) found")

    def new_conversation(self) -> None:
        if not self.model.get():
            self._show_error("No local Ollama model is available.")
            return
        self._async(
            lambda: self.client.request("create_conversation", title="New conversation", mode=self.mode.get(), model=self.model.get()),
            self._after_created,
        )

    def _after_created(self, created: dict[str, Any]) -> None:
        self.current_id = created["id"]
        self.search.set("")
        self._async(self._refresh_conversations, self._after_new_list)

    def _after_new_list(self, values: list[dict[str, Any]]) -> None:
        self._set_conversations(values)
        for index, item in enumerate(values):
            if item["id"] == self.current_id:
                self.conversation_list.selection_clear(0, "end")
                self.conversation_list.selection_set(index)
                self._load_selected(index)
                break

    def rename_conversation(self) -> None:
        if not self.current_id:
            return
        current = next((c for c in self.conversations if c["id"] == self.current_id), None)
        title = simpledialog.askstring("Rename conversation", "Conversation title:", initialvalue=current["title"] if current else "", parent=self.root)
        if title:
            conversation_id = self.current_id
            self._async(
                lambda: self.client.request("rename_conversation", conversation_id=conversation_id, title=title),
                lambda _: self._async(self._refresh_conversations, self._set_conversations),
            )

    def delete_conversation(self) -> None:
        if not self.current_id:
            return
        if not messagebox.askyesno("Delete conversation", "Delete this conversation and its local history?", default=messagebox.NO, parent=self.root):
            return
        conversation_id = self.current_id
        self._async(lambda: self.client.request("delete_conversation", conversation_id=conversation_id), self._deleted)

    def _deleted(self, _result: dict[str, Any]) -> None:
        self.current_id = None
        self._clear_views()
        self._async(self._refresh_conversations, self._after_delete_list)

    def _after_delete_list(self, values: list[dict[str, Any]]) -> None:
        self._set_conversations(values)
        self.status.set("Conversation deleted")
        if values:
            self.conversation_list.selection_set(0)
            self._load_selected(0)

    def _select_conversation(self, _event: object) -> None:
        selected = self.conversation_list.curselection()
        if selected:
            self._load_selected(selected[0])

    def _load_selected(self, index: int) -> None:
        self.current_id = self.conversations[index]["id"]
        conversation_id = self.current_id
        self.status.set("Loading conversation…")
        self._async(lambda: self._load_bundle(conversation_id), self._render_bundle)

    def _load_bundle(self, conversation_id: str) -> dict[str, Any]:
        protected = self.client.request("list_protected_events", conversation_id=conversation_id)
        legacy = self.client.request("list_tool_events", conversation_id=conversation_id)
        return {
            "history": self.client.request("read_history", conversation_id=conversation_id),
            "events": protected + legacy,
        }

    def _render_bundle(self, data: dict[str, Any]) -> None:
        self._render_history(data["history"])
        self._render_events(data["events"])

    def _render_history(self, data: dict[str, Any]) -> None:
        conversation = data["conversation"]
        self.mode.set(conversation["mode"])
        self.model.set(conversation["model"])
        self.messages.configure(state="normal")
        self.messages.delete("1.0", "end")
        if conversation["mode"] == "agent":
            self.messages.insert("end", "RESTRICTED AGENT MODE · formal tools and grants are disabled.\n", "notice")
        self.last_assistant = ""
        for item in data["messages"]:
            self._append_message(item["role"], item["content"])
            if item["role"] == "assistant":
                self.last_assistant = item["content"]
        self.messages.configure(state="disabled")
        self.messages.see("end")
        self.status.set(f"Ready · {conversation['mode']} · {conversation['model']}")

    def _append_message(self, role: str, content: str) -> None:
        label = "You" if role == "user" else "Assistant"
        tag = "user" if role == "user" else "assistant"
        self.messages.insert("end", f"{label}\n{content}\n", tag)

    def _render_events(self, events: list[dict[str, Any]]) -> None:
        self.current_events = events
        for item in self.activity_tree.get_children():
            self.activity_tree.delete(item)
        for index, event in enumerate(events):
            event_name = event.get("event_type") or event.get("execution_state")
            source = event.get("source_kind", "mock_legacy")
            summary = event.get("result_summary") or event.get("operation_summary") or event.get("source_event", "")
            self.activity_tree.insert("", "end", iid=str(index), values=(
                event["tool_name"], event_name, event["approval_state"], source, summary,
            ))

    def _settings_changed(self, _event: object) -> None:
        if not self.current_id:
            return
        conversation_id = self.current_id
        self._async(
            lambda: self.client.request("set_conversation_settings", conversation_id=conversation_id, mode=self.mode.get(), model=self.model.get()),
            lambda _: self._settings_saved(),
        )

    def _settings_saved(self) -> None:
        self._load_current()
        self._async(self._refresh_conversations, self._set_conversations)

    def _load_current(self) -> None:
        if self.current_id:
            conversation_id = self.current_id
            self._async(lambda: self._load_bundle(conversation_id), self._render_bundle)

    def _enter_to_send(self, event: tk.Event[tk.Misc]) -> str | None:
        if event.state & 0x0001:
            return None
        self.send()
        return "break"

    def send(self) -> str:
        if self.generation_id:
            return "break"
        content = self.input.get("1.0", "end").strip()
        if not self.current_id or not content:
            return "break"
        self.generation_id = str(uuid.uuid4())
        generation_id = self.generation_id
        conversation_id = self.current_id
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.input.delete("1.0", "end")
        self.messages.configure(state="normal")
        self._append_message("user", content)
        self.messages.insert("end", "Assistant\n", "assistant")
        self.messages.configure(state="disabled")
        self.messages.see("end")
        self.status.set("Ollama is streaming…")

        def on_event(event: dict[str, Any]) -> None:
            if event.get("type") == "delta":
                self.root.after(0, lambda: self._append_delta(str(event.get("content", ""))))

        self._async(
            lambda: self.client.stream_request(
                "stream_message", on_event,
                conversation_id=conversation_id, content=content,
                generation_id=generation_id, mode=self.mode.get(), model=self.model.get(),
            ), self._stream_done,
        )
        return "break"

    def _append_delta(self, content: str) -> None:
        self.messages.configure(state="normal")
        self.messages.insert("end", content, "assistant")
        self.messages.configure(state="disabled")
        self.messages.see("end")

    def _stream_done(self, result: dict[str, Any]) -> None:
        cancelled = bool(result.get("cancelled"))
        self.generation_id = None
        self.send_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("Generation cancelled" if cancelled else "Reply received and saved")
        self._load_current()
        self._async(self._refresh_conversations, self._set_conversations)

    def stop_generation(self) -> None:
        if not self.generation_id:
            return
        generation_id = self.generation_id
        self.status.set("Cancelling generation…")
        self._async(
            lambda: self.client.request("cancel_generation", generation_id=generation_id),
            lambda result: self.status.set("Cancellation requested" if result["cancelled"] else "Generation already finished"),
        )

    def copy_last_reply(self) -> None:
        if not self.last_assistant:
            self.status.set("No assistant reply to copy")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_assistant)
        self.status.set("Last assistant reply copied")

    def add_mock_event(self) -> None:
        if not self.current_id:
            return
        if self.mode.get() != "agent":
            self._show_error("Switch this conversation to Agent Mode to preview mock activity.")
            return
        conversation_id = self.current_id
        self._async(
            lambda: self.client.request("create_mock_tool_event", conversation_id=conversation_id, operation_summary="CREATE inert stage2b-test preview (no execution)"),
            lambda _: self._load_current(),
        )

    def stage_protected_request(self) -> None:
        if not self.current_id:
            return
        if self.mode.get() != "agent":
            self._show_error("Switch this conversation to Agent Mode to stage the protected Host request.")
            return
        conversation_id = self.current_id
        self.status.set("Staging validated offline Host approval request…")
        self._async(
            lambda: self.client.request("stage_protected_request", conversation_id=conversation_id),
            lambda _: self._load_current(),
        )

    def replay_protected_evidence(self) -> None:
        if not self.current_id:
            return
        if self.mode.get() != "agent":
            self._show_error("Switch this conversation to Agent Mode to replay protected evidence.")
            return
        conversation_id = self.current_id
        self.status.set("Loading immutable REVIEW-027 evidence; no live execution…")
        self._async(
            lambda: self.client.request("replay_protected_evidence", conversation_id=conversation_id),
            lambda _: self._load_current(),
        )

    def review_protected_event(self) -> None:
        selected = self.activity_tree.selection()
        if not selected:
            self.status.set("Select an awaiting-approval event first")
            return
        event = self.current_events[int(selected[0])]
        if event.get("source_kind") != "offline_host_replay":
            self.status.set("This is a legacy mock event, not a protected Host request")
            return
        conversation_id = self.current_id
        if not conversation_id:
            return
        self._async(
            lambda: self.client.request(
                "get_protected_approval", conversation_id=conversation_id,
                request_id=event["request_id"],
            ),
            self._show_protected_approval,
        )

    def _show_protected_approval(self, request: dict[str, Any]) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Protected Agent approval — offline sink")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="HOST APPROVAL REQUEST · OFFLINE REPLAY", foreground="#b45309", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        details = (
            f"Tool: {request['tool_name']}\nOperation: {request['operation']}\n"
            f"Risk: {request['risk_summary']}\n\nRun ID: {request['run_id']}\n"
            f"Call ID: {request['call_id']}\nThread ID: {request['thread_id']}\n"
            f"Deadline: {request['deadline_at']}\nStatus: {request['status']}"
        )
        ttk.Label(frame, text=details, justify="left", wraplength=620).pack(anchor="w", pady=(12, 8))
        ttk.Label(
            frame,
            text="Default is Reject. These buttons report only to a fake authorization sink; they cannot create a Grant or execute the tool.",
            wraplength=620,
        ).pack(anchor="w", pady=(0, 12))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        pending = request["status"] == "pending"
        reject = ttk.Button(buttons, text="Reject", command=lambda: self._decide_protected(dialog, request, False))
        reject.pack(side="right")
        approve = ttk.Button(buttons, text="Approve (no Grant)", command=lambda: self._decide_protected(dialog, request, True))
        approve.pack(side="right", padx=(0, 8))
        if not pending:
            approve.configure(state="disabled")
            reject.configure(state="disabled")
            ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._decide_protected(dialog, request, False) if pending else dialog.destroy())

    def _decide_protected(self, dialog: tk.Toplevel, request: dict[str, Any], approved: bool) -> None:
        dialog.destroy()
        conversation_id = request["conversation_id"]
        self._async(
            lambda: self.client.request(
                "submit_protected_decision", conversation_id=conversation_id,
                request_id=request["request_id"], approved=approved,
            ),
            lambda result: self._protected_decision_done(result, approved),
        )

    def _protected_decision_done(self, result: dict[str, Any], approved: bool) -> None:
        self.status.set(
            "Approval recorded in offline sink; no Grant or execution"
            if approved else "Request rejected; no Grant or execution"
        )
        self._load_current()

    def review_mock_event(self) -> None:
        selected = self.activity_tree.selection()
        if not selected:
            self.status.set("Select a pending mock event first")
            return
        event = self.current_events[int(selected[0])]
        if event["approval_state"] != "pending":
            self.status.set("This mock event has already been decided")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Mock tool approval — no real execution")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="MOCK / INERT ONLY", foreground="#b45309", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"Tool: {event['tool_name']}").pack(anchor="w", pady=(12, 3))
        ttk.Label(frame, text=f"Summary: {event['operation_summary']}", wraplength=440).pack(anchor="w")
        ttk.Label(frame, text="Default: reject. No grant or tool execution can be created here.", wraplength=440).pack(anchor="w", pady=12)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Reject", command=lambda: self._decide_mock(dialog, event["tool_call_id"], False)).pack(side="right")
        ttk.Button(buttons, text="Approve mock only", command=lambda: self._decide_mock(dialog, event["tool_call_id"], True)).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._decide_mock(dialog, event["tool_call_id"], False))

    def _decide_mock(self, dialog: tk.Toplevel, tool_call_id: str, approved: bool) -> None:
        dialog.destroy()
        self._async(
            lambda: self.client.request("decide_mock_approval", tool_call_id=tool_call_id, approved=approved),
            lambda _: self._load_current(),
        )

    def _clear_views(self) -> None:
        self.messages.configure(state="normal")
        self.messages.delete("1.0", "end")
        self.messages.configure(state="disabled")
        self._render_events([])

    def _apply_hotkey(self, choice: str) -> None:
        candidates = [choice] + [item for item in HOTKEYS if item != choice]
        for candidate in candidates:
            if self.hotkey.register(candidate):
                note = "" if candidate == choice else f" (requested {choice} was unavailable)"
                self.status.set(f"Ready · Companion hotkey {candidate}{note}")
                self._async(lambda: self.client.request("set_hotkey", hotkey=candidate))
                return
        self.status.set("Ready · global hotkey unavailable; use Companion button")

    def open_hotkey_settings(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Companion hotkey")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Choose a system-wide Companion shortcut. Conflicts are detected before saving.", wraplength=420).pack(anchor="w")
        chosen = tk.StringVar(value=self.hotkey.current or "Alt+Space")
        ttk.Combobox(frame, textvariable=chosen, values=tuple(HOTKEYS), state="readonly").pack(fill="x", pady=10)

        def apply() -> None:
            requested = chosen.get()
            if not self.hotkey.register(requested):
                messagebox.showerror("Companion hotkey", f"{requested} is already in use. Choose another shortcut.", parent=window)
                return
            self._async(lambda: self.client.request("set_hotkey", hotkey=requested))
            self.status.set(f"Companion hotkey: {requested}")
            window.destroy()

        ttk.Button(frame, text="Apply", command=apply).pack(anchor="e")

    def show_companion(self) -> None:
        if self.companion and self.companion.winfo_exists():
            self.companion.deiconify()
            self.companion.lift()
            return
        window = tk.Toplevel(self.root)
        self.companion = window
        window.title("Local Agent Companion")
        self.root.update_idletasks()
        x = max(20, self.root.winfo_rootx() + 180)
        y = max(20, self.root.winfo_rooty() + 100)
        window.geometry(f"560x430+{x}+{y}")
        window.attributes("-topmost", True)
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Quick chat · shared conversation and Agent Core").pack(anchor="w")
        output = tk.Text(frame, state="disabled", wrap="word", height=15)
        output.pack(fill="both", expand=True, pady=7)
        entry = tk.Text(frame, height=4, wrap="word")
        entry.pack(fill="x")

        def send_companion(_event: object = None) -> str:
            if self.companion_generation or not self.current_id:
                return "break"
            content = entry.get("1.0", "end").strip()
            if not content:
                return "break"
            entry.delete("1.0", "end")
            generation_id = str(uuid.uuid4())
            self.companion_generation = generation_id
            conversation_id = self.current_id
            output.configure(state="normal")
            output.insert("end", f"You\n{content}\nAssistant\n")
            output.configure(state="disabled")

            def on_event(event: dict[str, Any]) -> None:
                if event.get("type") == "delta":
                    self.root.after(0, lambda: append(str(event.get("content", ""))))

            def append(text: str) -> None:
                output.configure(state="normal")
                output.insert("end", text)
                output.configure(state="disabled")
                output.see("end")

            def done(_result: dict[str, Any]) -> None:
                self.companion_generation = None
                append("\n")
                self._load_current()

            self._async(lambda: self.client.stream_request(
                "stream_message", on_event, conversation_id=conversation_id,
                content=content, generation_id=generation_id,
                mode=self.mode.get(), model=self.model.get(),
            ), done)
            return "break"

        entry.bind("<Return>", lambda event: None if event.state & 1 else send_companion(event))
        ttk.Button(frame, text="Send", command=send_companion).pack(anchor="e", pady=(6, 0))
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        entry.focus_set()

    def open_memory(self) -> None:
        conversation_id = self.current_id
        self._async(
            lambda: {
                "items": self.client.request("list_memories"),
                "settings": self.client.request("get_memory_settings", conversation_id=conversation_id),
            },
            self._show_memory,
        )

    def _show_memory(self, data: dict[str, Any]) -> None:
        items = data["items"]
        settings = data["settings"]
        window = tk.Toplevel(self.root)
        window.title("Memory — explicit user-managed data")
        window.geometry("960x500")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Memory is reference data only. It cannot authorize tools and is never saved automatically.",
            foreground="#92400e",
        ).pack(anchor="w")
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=8)
        query = tk.StringVar()
        search = ttk.Entry(controls, textvariable=query)
        search.pack(side="left", fill="x", expand=True)

        def refresh(search_text: str = "") -> None:
            self._async(
                lambda: {
                    "items": self.client.request("list_memories", query=search_text),
                    "settings": self.client.request("get_memory_settings", conversation_id=self.current_id),
                },
                lambda result: (window.destroy(), self._show_memory(result)),
            )

        search.bind("<Return>", lambda _event: refresh(query.get()))
        ttk.Button(controls, text="Search", command=lambda: refresh(query.get())).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="Show all", command=lambda: refresh()).pack(side="left", padx=(5, 0))

        tree = ttk.Treeview(frame, columns=("type", "scope", "enabled", "source", "content"), show="headings")
        for key, title, width in (
            ("type", "Type", 90), ("scope", "Scope", 145), ("enabled", "Enabled", 65),
            ("source", "Source conversation", 180), ("content", "Content", 410),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True)
        for index, item in enumerate(items):
            tree.insert("", "end", iid=str(index), values=(
                item["memory_type"], item["scope"], item["enabled"],
                item.get("source_conversation_id") or "explicit / no conversation", item["content"],
            ))

        def selected() -> dict[str, Any] | None:
            selection = tree.selection()
            return items[int(selection[0])] if selection else None

        options = ttk.Frame(frame)
        options.pack(fill="x", pady=(8, 0))
        chat_enabled = tk.BooleanVar(value=bool(settings["chat_enabled"]))
        ttk.Checkbutton(options, text="Use enabled memory in Chat Mode", variable=chat_enabled).pack(side="left")
        ttk.Label(options, text="Current project:").pack(side="left", padx=(18, 4))
        project = tk.StringVar(value=settings.get("project_id", ""))
        ttk.Entry(options, textvariable=project, width=20).pack(side="left")

        def save_settings() -> None:
            self._async(lambda: self.client.request("set_memory_chat_enabled", enabled=chat_enabled.get()))
            if self.current_id:
                conversation_id = self.current_id
                self._async(lambda: self.client.request(
                    "set_conversation_project", conversation_id=conversation_id, project_id=project.get()
                ))
            self.status.set("Memory settings saved")

        ttk.Button(options, text="Save settings", command=save_settings).pack(side="left", padx=5)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(8, 0))

        def save_item(values: dict[str, Any], existing: dict[str, Any] | None) -> None:
            if existing:
                work = lambda: self.client.request("update_memory", memory_id=existing["memory_id"], **values)
            else:
                work = lambda: self.client.request(
                    "create_memory", source_conversation_id=self.current_id, **values
                )
            self._async(work, lambda _: refresh())

        def toggle() -> None:
            item = selected()
            if item:
                self._async(
                    lambda: self.client.request("set_memory_enabled", memory_id=item["memory_id"], enabled=not item["enabled"]),
                    lambda _: refresh(),
                )

        def remove() -> None:
            item = selected()
            if not item or not messagebox.askyesno(
                "Delete memory", "Permanently delete the selected memory?", default=messagebox.NO, parent=window
            ):
                return
            self._async(lambda: self.client.request("delete_memory", memory_id=item["memory_id"]), lambda _: refresh())

        ttk.Button(actions, text="Add", command=lambda: self._memory_editor(None, lambda value: save_item(value, None))).pack(side="left")
        ttk.Button(actions, text="Edit", command=lambda: self._memory_editor(selected(), lambda value: save_item(value, selected())) if selected() else None).pack(side="left", padx=4)
        ttk.Button(actions, text="Enable / Disable", command=toggle).pack(side="left")
        ttk.Button(actions, text="Delete", command=remove).pack(side="left", padx=4)
        ttk.Button(actions, text="Close", command=window.destroy).pack(side="right")

    def _memory_editor(self, existing: dict[str, Any] | None, save: Callable[[dict[str, Any]], None]) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit memory" if existing else "Add memory")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        memory_type = tk.StringVar(value=(existing or {}).get("memory_type", "long_term"))
        scope = tk.StringVar(value=(existing or {}).get("scope", "global"))
        enabled = tk.BooleanVar(value=bool((existing or {}).get("enabled", True)))
        ttk.Label(frame, text="Type").grid(row=0, column=0, sticky="w")
        kind = ttk.Combobox(frame, textvariable=memory_type, values=("long_term", "project"), state="readonly")
        kind.grid(row=0, column=1, sticky="ew")
        ttk.Label(frame, text="Scope").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=scope).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="Use global for long-term; project:<id> for project memory.", foreground="#52525b").grid(row=2, column=1, sticky="w")
        ttk.Label(frame, text="Content").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        content = tk.Text(frame, width=60, height=8, wrap="word")
        content.grid(row=3, column=1, sticky="nsew", pady=(8, 0))
        content.insert("1.0", (existing or {}).get("content", ""))
        ttk.Checkbutton(frame, text="Enabled", variable=enabled).grid(row=4, column=1, sticky="w", pady=7)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)

        def apply() -> None:
            value = {
                "memory_type": memory_type.get(), "scope": scope.get().strip(),
                "content": content.get("1.0", "end").strip(), "enabled": enabled.get(),
            }
            dialog.destroy()
            save(value)

        ttk.Button(frame, text="Save explicit memory", command=apply).grid(row=5, column=1, sticky="e")

    def open_provider(self) -> None:
        self._async(lambda: self.client.request("provider_status"), self._show_provider)

    def _show_provider(self, status: dict[str, Any]) -> None:
        window = tk.Toplevel(self.root)
        window.title("Model Provider")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Provider: {status['name']} ({status['type']})").pack(anchor="w")
        state = "Connected" if status["connected"] else f"Unavailable: {status['error']}"
        ttk.Label(frame, text=f"Status: {state}").pack(anchor="w", pady=(4, 10))
        chosen = tk.StringVar(value=status["default_model"])
        box = ttk.Combobox(frame, textvariable=chosen, values=status["models"], state="readonly", width=34)
        box.pack(fill="x")

        def save() -> None:
            if chosen.get():
                self._async(lambda: self.client.request("set_default_model", model=chosen.get()), lambda _: window.destroy())

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Refresh", command=lambda: (window.destroy(), self.open_provider())).pack(side="left")
        ttk.Button(actions, text="Set default", command=save).pack(side="right")

    def open_plugins(self) -> None:
        self._async(lambda: self.client.request("list_plugins"), self._show_plugins)

    def _show_plugins(self, plugins: list[dict[str, Any]]) -> None:
        window = tk.Toplevel(self.root)
        window.title("Plugins — configuration only")
        window.geometry("850x420")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Plugins remain non-executable in this build.", foreground="#92400e").pack(anchor="w")
        tree = ttk.Treeview(frame, columns=("id", "source", "version", "enabled", "approval", "status"), show="headings")
        for key, title, width in (
            ("id", "Plugin", 170), ("source", "Source", 90), ("version", "Version", 80),
            ("enabled", "Enabled", 70), ("approval", "Approval", 80), ("status", "Status", 180),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True, pady=8)
        for index, plugin in enumerate(plugins):
            tree.insert("", "end", iid=str(index), values=(
                plugin["plugin_id"], plugin["source_type"], plugin["version"],
                plugin["enabled"], plugin["approval_required"], plugin["connection_status"],
            ))

        def selected() -> dict[str, Any] | None:
            values = tree.selection()
            return plugins[int(values[0])] if values else None

        def edit(existing: dict[str, Any] | None = None) -> None:
            self._plugin_editor(existing, lambda manifest: self._async(
                lambda: self.client.request("upsert_plugin", manifest=manifest),
                lambda _: (window.destroy(), self.open_plugins()),
            ))

        def toggle() -> None:
            plugin = selected()
            if not plugin:
                return
            manifest = {key: plugin[key] for key in (
                "schema_version", "plugin_id", "name", "source_type", "version",
                "description", "enabled", "approval_required", "executable", "config",
            )}
            manifest["enabled"] = not manifest["enabled"]
            self._async(lambda: self.client.request("upsert_plugin", manifest=manifest), lambda _: (window.destroy(), self.open_plugins()))

        def remove() -> None:
            plugin = selected()
            if not plugin or not messagebox.askyesno("Remove plugin", f"Remove configuration for {plugin['name']}?", default=messagebox.NO, parent=window):
                return
            self._async(lambda: self.client.request("remove_plugin", plugin_id=plugin["plugin_id"]), lambda _: (window.destroy(), self.open_plugins()))

        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        ttk.Button(actions, text="Add", command=lambda: edit(None)).pack(side="left")
        ttk.Button(actions, text="Edit", command=lambda: edit(selected())).pack(side="left", padx=4)
        ttk.Button(actions, text="Enable / Disable", command=toggle).pack(side="left")
        ttk.Button(actions, text="Remove", command=remove).pack(side="left", padx=4)

    def _plugin_editor(self, existing: dict[str, Any] | None, save: Callable[[dict[str, Any]], None]) -> None:
        window = tk.Toplevel(self.root)
        window.title("Plugin Manifest 1.0")
        window.transient(self.root)
        fields: dict[str, tk.StringVar] = {}
        defaults = existing or {}
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        for row, (key, label, default) in enumerate((
            ("plugin_id", "Plugin ID", "my-plugin"), ("name", "Name", "My plugin"),
            ("version", "Version", "1.0.0"), ("description", "Description", "Local configuration"),
            ("config", "Config JSON", "{}"),
        )):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            value = defaults.get(key, default)
            if key == "config" and isinstance(value, dict):
                value = json.dumps(value)
            fields[key] = tk.StringVar(value=str(value))
            ttk.Entry(frame, textvariable=fields[key], width=48).grid(row=row, column=1, sticky="ew", pady=3)
        source = tk.StringVar(value=defaults.get("source_type", "mcp"))
        enabled = tk.BooleanVar(value=bool(defaults.get("enabled", False)))
        approval = tk.BooleanVar(value=bool(defaults.get("approval_required", True)))
        ttk.Label(frame, text="Source").grid(row=5, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=source, values=("mcp", "github", "gmail", "custom"), state="readonly").grid(row=5, column=1, sticky="ew")
        ttk.Checkbutton(frame, text="Enabled (configuration only)", variable=enabled).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Human approval required", variable=approval).grid(row=7, column=1, sticky="w")

        def submit() -> None:
            try:
                config = json.loads(fields["config"].get())
            except json.JSONDecodeError as error:
                messagebox.showerror("Plugin", f"Invalid Config JSON: {error}", parent=window)
                return
            manifest = {
                "schema_version": "1.0", "plugin_id": fields["plugin_id"].get(),
                "name": fields["name"].get(), "source_type": source.get(),
                "version": fields["version"].get(), "description": fields["description"].get(),
                "enabled": enabled.get(), "approval_required": approval.get(),
                "executable": False, "config": config,
            }
            window.destroy()
            save(manifest)

        ttk.Button(frame, text="Save manifest", command=submit).grid(row=8, column=1, sticky="e", pady=(12, 0))

    def open_attachments(self) -> None:
        if not self.current_id:
            return
        conversation_id = self.current_id
        self._async(lambda: self.client.request("list_attachments", conversation_id=conversation_id), lambda items: self._show_attachments(conversation_id, items))

    def _show_attachments(self, conversation_id: str, items: list[dict[str, Any]]) -> None:
        window = tk.Toplevel(self.root)
        window.title("Attachments — preview only")
        window.geometry("760x390")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Attachments are never treated as commands or authorization, and are not sent to this text-only model.", wraplength=720, foreground="#92400e").pack(anchor="w")
        tree = ttk.Treeview(frame, columns=("name", "type", "size", "status"), show="headings")
        for key, label, width in (("name", "File", 220), ("type", "Type", 140), ("size", "Bytes", 90), ("status", "Capability", 250)):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True, pady=8)
        for index, item in enumerate(items):
            tree.insert("", "end", iid=str(index), values=(item["display_name"], item["media_type"], item["size_bytes"], item["capability_status"]))

        def selected() -> dict[str, Any] | None:
            chosen = tree.selection()
            return items[int(chosen[0])] if chosen else None

        def add() -> None:
            path = filedialog.askopenfilename(parent=window, filetypes=[("Supported files", "*.png *.gif *.jpg *.jpeg *.webp *.txt *.md *.pdf")])
            if path:
                self._async(lambda: self.client.request("add_attachment", conversation_id=conversation_id, path=path), lambda _: (window.destroy(), self.open_attachments()))

        def preview() -> None:
            item = selected()
            if not item:
                return
            self._async(lambda: self.client.request("attachment_preview", attachment_id=item["id"]), lambda result: self._show_preview(item, result))

        def remove() -> None:
            item = selected()
            if not item or not messagebox.askyesno("Remove attachment", "Remove attachment metadata? The original file will be preserved.", default=messagebox.NO, parent=window):
                return
            self._async(lambda: self.client.request("delete_attachment", attachment_id=item["id"]), lambda _: (window.destroy(), self.open_attachments()))

        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        ttk.Button(actions, text="Add file", command=add).pack(side="left")
        ttk.Button(actions, text="Preview", command=preview).pack(side="left", padx=4)
        ttk.Button(actions, text="Remove metadata", command=remove).pack(side="left")
        ttk.Button(actions, text="Screenshot…", command=lambda: messagebox.showinfo("Screenshot attachment", "Press Win+Shift+S, save the capture, then choose Add file. Direct clipboard capture is not yet supported.", parent=window)).pack(side="left", padx=4)

    def _show_preview(self, item: dict[str, Any], result: dict[str, Any]) -> None:
        if not result["supported"]:
            messagebox.showinfo("Attachment preview", result["reason"])
            return
        window = tk.Toplevel(self.root)
        window.title(item["display_name"])
        image = tk.PhotoImage(data=result["data_base64"])
        label = ttk.Label(window, image=image)
        label.image = image
        label.pack(padx=10, pady=10)

    def _show_error(self, message: str) -> None:
        self.generation_id = None
        self.send_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set(f"Error: {message}")
        messagebox.showerror("Local Ollama Agent", message)

    def close(self) -> None:
        if self.generation_id:
            try:
                self.client.request("cancel_generation", generation_id=self.generation_id)
            except Exception:
                pass
        self.hotkey.unregister()
        self.client.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = DesktopAgentApp(root, Path(__file__).resolve().parents[1])
    app._async(app._initial_load, app._apply_initial)
    root.mainloop()


if __name__ == "__main__":
    main()
