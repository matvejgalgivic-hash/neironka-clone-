"""One-window local chat with persistent conversations and rolling memory."""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import tkinter as tk
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext

from llama_cpp import Llama


APP_NAME = "Clone"
MODEL_NAME = "Qwen3-4B-Q4_K_M.gguf"
INPUT_PLACEHOLDER = "Напиши сообщение…"
SYSTEM_PROMPT = (
    "Ты дружелюбный ИИ-помощник и программист. Отвечай на русском, если пользователь не попросил иначе. "
    "Учитывай память и контекст диалога. Пиши рабочий код на любом языке программирования, "
    "приводи полный пример в блоке кода и поясняй запуск. Отвечай ясно и не выдумывай факты."
)
MEMORY_PROMPT = (
    "Сожми память об этом диалоге в короткое резюме на русском (до 180 слов). "
    "Сохрани важные факты о пользователе, его цели, предпочтения, принятые решения, "
    "незавершённые дела и важные детали. Объедини старое резюме с новыми сообщениями. "
    "Удали повторы, не добавляй предположений. Верни только резюме."
)
APP_DATA = Path(os.getenv("APPDATA", str(Path.home()))) / "MyNeuralNet"
HISTORY_FILE = APP_DATA / "history.json"


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


class ChatApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1120x760")
        self.root.minsize(850, 600)
        self.root.configure(bg="#111318")
        self.model: Llama | None = None
        self.busy = False
        self.generating_session_id: str | None = None
        self.live_answer = ""
        self.stream_header_shown = False
        self.sessions: list[dict] = []
        self.history_ids: list[str] = []
        self.active_id = ""
        self.memory_summary = ""
        self.messages: list[dict[str, str]] = []
        self.pending_memory: list[dict[str, str]] = []
        self.thinking_enabled = False
        self._load_history()
        self._build_ui()
        if not self.sessions:
            self._new_chat()
        else:
            self._activate(self.sessions[0]["id"])
        threading.Thread(target=self._load_model, daemon=True).start()

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(self.root, bg="#17191f", width=250)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        tk.Label(sidebar, text="✳  Clone", bg="#17191f", fg="#f1f2f4",
                 font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x", padx=18, pady=(22, 24))
        tk.Button(sidebar, text="＋  Новый чат", command=self._new_chat,
                  bg="#252831", fg="#f4f4f5", activebackground="#30343e",
                  activeforeground="white", relief="flat", anchor="w", padx=14, pady=11,
                  font=("Segoe UI", 10, "bold")).pack(fill="x", padx=12, pady=(0, 24))
        tk.Label(sidebar, text="Недавние", bg="#17191f", fg="#8c9099",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=18, pady=(0, 8))
        self.history_list = tk.Listbox(sidebar, bg="#17191f", fg="#d7d9de",
                                       selectbackground="#343844", selectforeground="white",
                                       borderwidth=0, highlightthickness=0, activestyle="none",
                                       font=("Segoe UI", 10))
        self.history_list.pack(fill="both", expand=True, padx=8, pady=(0, 12))
        self.history_list.bind("<<ListboxSelect>>", self._history_selected)
        tk.Button(sidebar, text="Удалить выбранный чат", command=self._delete_selected,
                  bg="#17191f", fg="#e58b8b", activebackground="#272a31",
                  activeforeground="#ffaaaa", relief="flat", anchor="w", padx=12,
                  font=("Segoe UI", 9)).pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(sidebar, text="Локальная модель · без интернета", bg="#17191f",
                 fg="#777d89", font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=16, pady=14)

        main = tk.Frame(self.root, bg="#212329")
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        topbar = tk.Frame(main, bg="#212329", height=58)
        topbar.grid(row=0, column=0, sticky="ew", padx=26, pady=(12, 6))
        topbar.grid_columnconfigure(0, weight=1)
        self.chat_title = tk.Label(topbar, text="Новый чат", bg="#212329", fg="#f1f2f4",
                                   font=("Segoe UI", 12, "bold"), anchor="w")
        self.chat_title.grid(row=0, column=0, sticky="w")
        tk.Label(topbar, text="4B · локально", bg="#212329", fg="#9a9da5",
                 font=("Segoe UI", 9)).grid(row=0, column=1, sticky="e")
        self.think_button = tk.Button(topbar, text="Размышление: выкл", command=self._toggle_thinking,
                                      bg="#2b2d33", fg="#d8d9de", activebackground="#383b44",
                                      relief="flat", padx=10, pady=6, font=("Segoe UI", 9))
        self.think_button.grid(row=0, column=2, sticky="e", padx=(14, 0))

        self.chat = scrolledtext.ScrolledText(
            main, wrap="word", state="disabled", bg="#212329", fg="#e7e8eb",
            insertbackground="white", relief="flat", padx=50, pady=20,
            font=("Segoe UI", 11), spacing1=3, spacing3=8, borderwidth=0,
        )
        self.chat.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        self.chat.tag_configure("user", foreground="#f2f3f5", font=("Segoe UI", 10, "bold"), spacing1=8)
        self.chat.tag_configure("assistant", foreground="#a6e3b4", font=("Segoe UI", 10, "bold"), spacing1=8)
        self.chat.tag_configure("body", foreground="#e7e8eb", lmargin1=0, lmargin2=0)
        self.chat.tag_configure("info", foreground="#9a9da5", font=("Segoe UI", 9, "italic"))

        composer = tk.Frame(main, bg="#212329")
        composer.grid(row=2, column=0, sticky="ew", padx=50, pady=(8, 8))
        composer.grid_columnconfigure(0, weight=1)
        card = tk.Frame(composer, bg="#2b2d33", highlightbackground="#44464e", highlightthickness=1)
        card.grid(row=0, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)
        self.input = tk.Text(card, height=3, wrap="word", bg="#2b2d33", fg="#f1f2f4",
                             insertbackground="white", relief="flat", padx=15, pady=12,
                             font=("Segoe UI", 11), borderwidth=0, highlightthickness=0)
        self.input.grid(row=0, column=0, sticky="ew")
        self.input.insert("1.0", INPUT_PLACEHOLDER)
        self.input.configure(fg="#90949e")
        self.input.bind("<FocusIn>", self._clear_placeholder)
        self.input.bind("<FocusOut>", self._show_placeholder)
        self.input.bind("<Return>", self._enter_to_send)
        self.input.bind("<Control-v>", self._paste_from_clipboard)
        self.input.bind("<Control-c>", self._copy_to_clipboard)
        self.chat.bind("<Control-c>", self._copy_to_clipboard)
        tk.Button(card, text="↑", command=self.send, state="disabled", bg="#f1f2f4",
                  fg="#22242a", activebackground="white", relief="flat", width=3,
                  font=("Segoe UI", 14, "bold")).grid(row=0, column=1, padx=10, pady=9, sticky="se")
        self.send_button = card.grid_slaves(row=0, column=1)[0]
        self.status = tk.Label(main, text="Запускаю модель…", bg="#212329", fg="#8d9099",
                               font=("Segoe UI", 8), anchor="center")
        self.status.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        self._refresh_history_list()
        self._render_messages()

    def _load_history(self) -> None:
        try:
            if HISTORY_FILE.exists():
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                self.sessions = data.get("sessions", [])
        except (OSError, json.JSONDecodeError):
            self.sessions = []

    def _save_history(self) -> None:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        data = {"active_id": self.active_id, "sessions": self.sessions[:50]}
        temp_file = HISTORY_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(HISTORY_FILE)

    def _active_session(self) -> dict:
        for session in self.sessions:
            if session["id"] == self.active_id:
                return session
        raise RuntimeError("Активный диалог не найден")

    def _save_active(self) -> None:
        if not self.active_id:
            return
        session = self._active_session()
        session["messages"] = self.messages
        session["summary"] = self.memory_summary
        session["pending_memory"] = self.pending_memory
        session["updated"] = datetime.now().isoformat(timespec="seconds")
        self.sessions.sort(key=lambda item: item.get("updated", ""), reverse=True)
        self._save_history()
        self._refresh_history_list()

    def _new_chat(self) -> None:
        self._save_active()
        session = {"id": uuid.uuid4().hex, "title": "Новый чат", "messages": [],
                   "summary": "", "pending_memory": [], "updated": datetime.now().isoformat(timespec="seconds")}
        self.sessions.insert(0, session)
        self.active_id = session["id"]
        self.messages, self.pending_memory, self.memory_summary = [], [], ""
        self.chat_title.configure(text=session["title"])
        self._render_messages()
        self._save_active()
        if self.busy:
            self.status.configure(text="Ответ ещё генерируется в предыдущем чате")
            self.send_button.configure(state="disabled")

    def _activate(self, session_id: str) -> None:
        if self.busy or session_id == self.active_id:
            return
        self._save_active()
        session = next((item for item in self.sessions if item["id"] == session_id), None)
        if not session:
            return
        self.active_id = session_id
        self.messages = session.get("messages", [])
        self.memory_summary = session.get("summary", "")
        self.pending_memory = session.get("pending_memory", [])
        if hasattr(self, "chat_title"):
            self.chat_title.configure(text=session.get("title", "Чат"))
            self._render_messages()
            self._refresh_history_list()

    def _refresh_history_list(self) -> None:
        if not hasattr(self, "history_list"):
            return
        self.history_list.delete(0, "end")
        self.history_ids = []
        for index, session in enumerate(self.sessions[:50]):
            title = session.get("title", "Новый чат")
            self.history_list.insert("end", title[:32])
            self.history_ids.append(session["id"])
            if session["id"] == self.active_id:
                self.history_list.selection_set(index)

    def _history_selected(self, _event: tk.Event) -> None:
        selection = self.history_list.curselection()
        if selection and selection[0] < len(self.history_ids):
            self._activate(self.history_ids[selection[0]])

    def _delete_selected(self) -> None:
        selection = self.history_list.curselection()
        if not selection or selection[0] >= len(self.history_ids):
            return
        selected_id = self.history_ids[selection[0]]
        session = next((item for item in self.sessions if item["id"] == selected_id), None)
        if session is None:
            return
        title = session.get("title", "Новый чат")
        if not messagebox.askyesno("Удалить чат", f"Удалить «{title}»?", parent=self.root):
            return
        self.sessions = [item for item in self.sessions if item["id"] != session["id"]]
        if session["id"] == self.active_id:
            self.active_id = ""
            self.messages, self.pending_memory, self.memory_summary = [], [], ""
            self._new_chat()
        else:
            self._save_history()
            self._refresh_history_list()

    def _toggle_thinking(self) -> None:
        self.thinking_enabled = not self.thinking_enabled
        self.think_button.configure(
            text="Размышление: вкл" if self.thinking_enabled else "Размышление: выкл",
            fg="#b8a7ff" if self.thinking_enabled else "#d8d9de",
        )

    def _render_messages(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        if not self.messages:
            self.chat.insert("end", "Привет! Напиши сообщение, и я отвечу здесь.\n\n", "info")
        else:
            for message in self.messages:
                role = message.get("role")
                if role not in ("user", "assistant"):
                    continue
                self.chat.insert("end", ("Ты" if role == "user" else "Нейросеть") + "\n", role)
                self.chat.insert("end", message.get("content", "") + "\n\n", "body")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _append(self, tag: str, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _load_model(self) -> None:
        model_path = resource_path(os.path.join("models", MODEL_NAME))
        try:
            model = Llama(model_path=str(model_path), n_ctx=3072,
                          n_batch=256,
                          n_threads=max(2, min(12, os.cpu_count() or 4)),
                          n_threads_batch=max(2, min(12, os.cpu_count() or 4)),
                          n_gpu_layers=0, use_mmap=True, chat_format="chatml", verbose=False)
            self.model = model
            self.root.after(0, self._model_ready)
        except Exception as exc:
            self.root.after(0, lambda error=str(exc): self._model_error(error))

    def _model_ready(self) -> None:
        self.send_button.configure(state="normal")
        self.status.configure(text="Модель готова · история сохраняется на этом компьютере")
        self.input.focus_set()

    def _model_error(self, error: str) -> None:
        self.status.configure(text="Модель не запустилась")
        messagebox.showerror("Не удалось запустить нейросеть", error, parent=self.root)

    def _clear_placeholder(self, _event: tk.Event | None = None) -> None:
        if self.input.get("1.0", "end-1c") == INPUT_PLACEHOLDER:
            self.input.delete("1.0", "end")
            self.input.configure(fg="#f1f2f4")

    def _show_placeholder(self, _event: tk.Event | None = None) -> None:
        if not self.input.get("1.0", "end-1c").strip():
            self.input.insert("1.0", INPUT_PLACEHOLDER)
            self.input.configure(fg="#90949e")

    def _paste_from_clipboard(self, event: tk.Event) -> str:
        if event.widget is not self.input:
            return "break"
        try:
            value = self.root.clipboard_get()
        except tk.TclError:
            return "break"
        self._clear_placeholder()
        try:
            self.input.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.input.insert("insert", value)
        return "break"

    def _copy_to_clipboard(self, event: tk.Event) -> str:
        widget = event.widget
        try:
            selected = widget.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        self.root.clipboard_clear()
        self.root.clipboard_append(selected)
        return "break"

    def _enter_to_send(self, event: tk.Event) -> str:
        if event.state & 0x0001:
            return ""
        self.send()
        return "break"

    def send(self) -> None:
        if self.busy or self.model is None:
            return
        prompt = self.input.get("1.0", "end").strip()
        if not prompt or prompt == INPUT_PLACEHOLDER:
            return
        self.input.delete("1.0", "end")
        self.messages.append({"role": "user", "content": prompt})
        self.pending_memory.append({"role": "user", "content": prompt})
        session = self._active_session()
        if session.get("title") == "Новый чат":
            session["title"] = prompt[:32].replace("\n", " ")
            self.chat_title.configure(text=session["title"])
        self._append("user", "Ты\n")
        self._append("body", prompt + "\n\n")
        self.busy = True
        self.generating_session_id = self.active_id
        self.live_answer = ""
        self.stream_header_shown = False
        self.send_button.configure(state="disabled")
        self.status.configure(text="Печатает…")
        self._save_active()
        thread_messages = self._model_messages()
        session_id = self.active_id
        summary_snapshot = self.memory_summary
        pending_snapshot = [item.copy() for item in self.pending_memory]
        thinking_snapshot = self.thinking_enabled
        code_request = bool(re.search(
            r"\b(code|код|программ|скрипт|python|javascript|typescript|java|c\+\+|html|css|sql|rust|go)\b",
            prompt, re.IGNORECASE,
        ))
        token_limit = 1000 if code_request else 320
        threading.Thread(
            target=self._generate,
            args=(session_id, thread_messages, token_limit, summary_snapshot, pending_snapshot, thinking_snapshot),
            daemon=True,
        ).start()

    def _model_messages(self) -> list[dict[str, str]]:
        system = SYSTEM_PROMPT
        if self.memory_summary:
            system += "\n\nКраткая память о предыдущем разговоре:\n" + self.memory_summary
        system += (" Показывай краткий ход рассуждения перед ответом." if self.thinking_enabled
                   else " Не показывай ход рассуждений; сразу дай готовый ответ.")
        context = [message.copy() for message in self.messages[-10:]]
        if context and context[-1]["role"] == "user":
            context[-1]["content"] += "\n" + ("/think" if self.thinking_enabled else "/no_think")
        return [{"role": "system", "content": system}] + context

    def _generate(
        self,
        session_id: str,
        history: list[dict[str, str]],
        token_limit: int,
        previous_summary: str,
        pending_memory: list[dict[str, str]],
        thinking_enabled: bool,
    ) -> None:
        try:
            assert self.model is not None
            stream = self.model.create_chat_completion(
                messages=history,
                temperature=0.6 if thinking_enabled else 0.7,
                top_p=0.95 if thinking_enabled else 0.8,
                top_k=20, repeat_penalty=1.08, max_tokens=token_limit, stream=True,
            )
            self.root.after(0, self._begin_stream, session_id)
            pieces: list[str] = []
            raw = ""
            visible_offset = 0
            in_think = False
            show_thinking = thinking_enabled
            for chunk in stream:
                token = chunk["choices"][0].get("delta", {}).get("content", "")
                if token:
                    pieces.append(token)
                    raw += token
                    if show_thinking:
                        self.root.after(0, self._stream_token, session_id, token)
                    else:
                        visible, visible_offset, in_think = self._visible_chunk(raw, visible_offset, in_think)
                        if visible:
                            self.root.after(0, self._stream_token, session_id, visible)
            if not show_thinking and not in_think and visible_offset < len(raw):
                self.root.after(0, self._stream_token, session_id, raw[visible_offset:])
            answer = "".join(pieces).strip()
            new_pending = [item.copy() for item in pending_memory]
            new_pending.append({"role": "assistant", "content": answer})
            new_summary = previous_summary
            if len(new_pending) >= 8:
                self.root.after(0, lambda: self.status.configure(text="Обновляю краткую память чата…"))
                new_summary = self._summarize_memory(previous_summary, new_pending)
                if new_summary != previous_summary:
                    new_pending = []
            self.root.after(0, self._finish_turn, session_id, answer, new_summary, new_pending)
        except Exception as exc:
            self.root.after(0, self._show_error, session_id, str(exc))

    def _summarize_memory(self, previous_summary: str, pending_memory: list[dict[str, str]]) -> str:
        assert self.model is not None
        transcript = []
        if previous_summary:
            transcript.append("Предыдущее резюме:\n" + previous_summary)
        transcript.append("Новые сообщения:\n" + "\n".join(
            ("Пользователь: " if m["role"] == "user" else "Ассистент: ") + m["content"]
            for m in pending_memory
        ))
        result = self.model.create_chat_completion(
            messages=[{"role": "system", "content": MEMORY_PROMPT},
                      {"role": "user", "content": "\n\n".join(transcript)}],
            temperature=0.1, max_tokens=120,
        )
        summary = result["choices"][0]["message"].get("content", "").strip()
        if summary:
            self.memory_summary = summary
            self.pending_memory.clear()
        return summary or previous_summary

    def _begin_stream(self, session_id: str) -> None:
        if self.active_id == session_id and not self.stream_header_shown:
            self._append("assistant", "Нейросеть\n")
            self.stream_header_shown = True

    def _stream_token(self, session_id: str, text: str) -> None:
        if self.generating_session_id != session_id:
            return
        self.live_answer += text
        if self.active_id == session_id:
            self._begin_stream(session_id)
            self._append("body", text)

    @staticmethod
    def _visible_chunk(raw: str, offset: int, in_think: bool) -> tuple[str, int, bool]:
        output: list[str] = []
        while offset < len(raw):
            if in_think:
                end = raw.find("</think>", offset)
                if end < 0:
                    return "".join(output), max(offset, len(raw) - 7), True
                offset = end + len("</think>")
                in_think = False
                continue
            start = raw.find("<think>", offset)
            if start >= 0:
                output.append(raw[offset:start])
                offset = start + len("<think>")
                in_think = True
                continue
            safe_end = max(offset, len(raw) - 6)
            output.append(raw[offset:safe_end])
            offset = safe_end
        return "".join(output), offset, in_think

    def _finish_turn(self, session_id: str, answer: str, summary: str,
                     pending_memory: list[dict[str, str]]) -> None:
        session = next((item for item in self.sessions if item["id"] == session_id), None)
        if session is not None:
            messages = session.setdefault("messages", [])
            messages.append({"role": "assistant", "content": answer})
            session["summary"] = summary
            session["pending_memory"] = pending_memory
            session["updated"] = datetime.now().isoformat(timespec="seconds")
            self.sessions.sort(key=lambda item: item.get("updated", ""), reverse=True)
            if self.active_id == session_id:
                self.messages = messages
                self.memory_summary = summary
                self.pending_memory = pending_memory
                self._append("body", "\n\n")
            self._save_history()
            self._refresh_history_list()
        self.busy = False
        self.generating_session_id = None
        self.live_answer = ""
        self.stream_header_shown = False
        self.send_button.configure(state="normal" if self.model else "disabled")
        if session is None:
            self.status.configure(text="Этот чат удалён · ответ не сохранён")
        else:
            self.status.configure(text="Ответ сохранён в истории" if self.active_id != session_id else "Готово")

    def _show_error(self, session_id: str, error: str) -> None:
        if self.active_id == session_id:
            self._append("info", f"\nОшибка ответа: {error}\n\n")
        self.busy = False
        self.generating_session_id = None
        self.live_answer = ""
        self.stream_header_shown = False
        self.send_button.configure(state="normal" if self.model else "disabled")
        self.status.configure(text="Ошибка генерации" if self.active_id == session_id else "Предыдущий ответ не получен")


def main() -> None:
    # A second launch brings focus to the existing chat instead of opening another window.
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        mutex = kernel32.CreateMutexW(None, False, "Local\\MyNeuralNetSingleInstance")
        if ctypes.get_last_error() == 183:
            hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            return
    root = tk.Tk()
    ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
