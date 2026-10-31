# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""NR Voice Typing desktop UI. All Tk state is owned by the main thread."""
from __future__ import annotations

import queue
from collections import deque
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path

from app_storage import History, load_settings, save_settings
from audio_capture import AudioBuffer, Microphone, input_devices
from project_paths import APP_DIR
from speech_engine import (DEFAULT_MODELS_DIR, DATA_DIR,
                           LOGGER, discover_models, normalize_models_directory)
from transcription_worker import TranscriptionWorker
from processing_progress import ProcessingProgress, overall_fraction
from ui_widgets import PALETTES, ActionButton, IconButton, Wave, FloatingWave
from windows_integration import GlobalHotkey, HOTKEYS, capture_target, insert_text, modifiers_pressed
from app_info import (APP_NAME, APP_VERSION, CREATOR_NAME, CREATOR_ROLE,
                      CREATOR_CONTACT, GITHUB_PROFILE)


class VoiceTypingApp(tk.Tk):
    def __init__(self, *, data_dir=None, start_services=True):
        super().__init__()
        self.title(f"{APP_NAME} · Offline")
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.settings_path = self.data_dir / "settings.json"
        self.settings = load_settings(self.settings_path)
        self.history = History(self.data_dir / "history.sqlite3")
        self.events = queue.Queue()
        self.worker = TranscriptionWorker(self.emit)
        self.hotkey = None
        self.overlay = None
        self.settings_window = None
        self.history_window = None
        self.about_window = None
        self.closed = False
        self.services_enabled = start_services
        self.models = []
        self.current_info = None
        self.model_ready = False
        self.loading = False
        self.active = False
        self.finishing = False
        self.pending = 0
        self.session = 0
        self.cancel_event = threading.Event()
        self.microphone = None
        self.buffer = None
        self.parts = []
        self.draft = ""
        self.audio_jobs = deque()
        self.segment_id = 0
        self.export_audio = None
        self.exporting = False
        self.muted = False
        self.append_session = False
        self.resume_available = False
        self.manual_finish_requested = False
        self.original_editor_text = ""
        self.session_base_text = ""
        self.target = None
        self.elapsed = 0.0
        self.processing_seconds = 0.0
        self.progress = ProcessingProgress(cache_path=self.data_dir / "processing_timings.json")
        self.job_weights = []
        self.display_progress = 0
        self.submitted = 0
        self.completed_at = 0
        self.clipboard_paste = None
        self.source = "Microphone"
        self.devices = {"Default microphone": None}
        self.status = tk.StringVar(value="Finding local speech models…" if start_services else "Ready to test")
        self.model_status = tk.StringVar(value="LOCAL ENGINE")
        self.timer = tk.StringVar(value="00:00")
        self.phase_label = tk.StringVar(value="READY")
        self.word_count = tk.StringVar(value="0 words")
        self.language = tk.StringVar(value="Dari / فارسی" if self.settings["language"] == "fa" else "English")
        self.live = tk.BooleanVar(value=self.settings["live"])
        self.brand_logo = None
        logo_path = APP_DIR / "assets" / "nr_voice_typing_logo_v2.png"
        if logo_path.exists():
            try:
                self.brand_logo = tk.PhotoImage(file=str(logo_path))
                self.iconphoto(True, self.brand_logo)
            except tk.TclError as exc:
                LOGGER.warning("Could not load brand logo: %s", exc)
        self._build_ui()
        width = min(int(self.settings["width"]), self.winfo_screenwidth()-60)
        height = min(int(self.settings["height"]), self.winfo_screenheight()-90)
        self.geometry(f"{width}x{height}+{max(0, (self.winfo_screenwidth()-width)//2)}+35")
        self.minsize(min(760, width), min(580, height))
        self.protocol("WM_DELETE_WINDOW", self.close_to_background)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Control-o>", lambda e: self.import_audio())
        self.bind("<Control-s>", lambda e: self.save_text())
        self.after(60, self._tick)
        if start_services:
            self.after(250, self._start_services)

    @property
    def busy(self):
        return self.active or self.finishing

    def emit(self, kind, payload=None):
        if not self.closed:
            self.events.put((kind, payload))

    def _start_services(self):
        self.set_hotkey()
        self.show_overlay(self.settings["overlay"])
        self.scan_models()
        def scan_devices():
            try:
                self.emit("devices", input_devices())
            except Exception as exc:
                self.emit("notice", f"Microphone discovery failed: {exc}")
        threading.Thread(target=scan_devices, daemon=True).start()

    # The main screen contains only everyday actions. Advanced controls live in Settings.
    def _build_ui(self):
        self.colors = c = PALETTES[self.settings["theme"]]
        self._ui_images = []
        self.configure(bg=c["bg"])
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=c["editor"], background=c["card"],
                        foreground=c["text"], arrowcolor=c["muted"], padding=7,
                        bordercolor=c["line"], selectbackground=c["soft"], selectforeground=c["text"])
        style.map("TCombobox", fieldbackground=[("readonly", c["editor"])],
                  foreground=[("readonly", c["text"])])
        style.configure("Vertical.TScrollbar", background=c["line"], troughcolor=c["editor"],
                        bordercolor=c["editor"], arrowcolor=c["muted"], relief="flat", width=12)
        self.option_add("*TCombobox*Listbox.background", c["card"])
        self.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["soft"])
        self.shell = tk.Frame(self, bg=c["bg"])
        self.shell.pack(fill="both", expand=True, padx=28, pady=22)
        self.shell.columnconfigure(0, weight=1)
        self.shell.rowconfigure(3, weight=1)
        top = self.frame(self.shell)
        top.grid(row=0, column=0, sticky="ew")
        if self.brand_logo:
            factor = max(1, self.brand_logo.width() // 60)
            header_logo = self.brand_logo.subsample(factor, factor)
            self._ui_images.append(header_logo)
            tk.Label(top, image=header_logo, bg=c["bg"], bd=0).pack(side="left", padx=(0, 10))
        self.label(top, "NR VOICE TYPING", 11, bold=True).pack(side="left")
        self.icon_button(top, "⚙", self.open_settings, "Settings").pack(side="right")
        self.icon_button(top, "☀" if self.settings["theme"] == "dark" else "☾",
                         self.toggle_theme, "Switch color theme").pack(side="right", padx=8)
        self.icon_button(top, "ⓘ", self.open_about, "About NR Voice Typing").pack(side="right")
        self.label(top, "●  ON DEVICE", 9, color=c["accent"]).pack(side="right", padx=14)

        intro = self.frame(self.shell)
        intro.grid(row=1, column=0, sticky="ew", pady=(26, 20))
        self.label(intro, "Your voice. In writing.", 27, bold=True).pack(anchor="w")
        self.label(intro, "Speak here or type into any app. Your audio stays on this device.", 11, color=c["muted"]).pack(anchor="w", pady=(7, 0))

        options = self.frame(self.shell)
        options.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.label(options, "DICTATION", 9, bold=True, color=c["muted"]).pack(side="left", padx=(0, 14))
        self.lang_combo = ttk.Combobox(options, textvariable=self.language,
                                      values=["English", "Dari / فارسی"], state="readonly", width=15)
        self.lang_combo.pack(side="left")
        self.lang_combo.bind("<<ComboboxSelected>>", lambda e: self.change_language())
        self.live_check = tk.Checkbutton(options, text="Live preview", variable=self.live,
                                        command=self.persist, bg=c["bg"], fg=c["text"],
                                        selectcolor=c["editor"], activebackground=c["bg"],
                                        activeforeground=c["accent"], font=("Segoe UI", 10), bd=1,
                                        relief="groove", padx=8, pady=8, cursor="hand2")
        self.live_check.pack(side="left", padx=14)
        self.import_button = self.button(options, "＋  Import audio", self.import_audio)
        self.import_button.pack(side="right")

        card = self.frame(self.shell, "card")
        card.configure(highlightthickness=1, highlightbackground=c["line"])
        card.grid(row=3, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        editor_top = self.frame(card, "card")
        editor_top.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 10))
        self.label(editor_top, "TRANSCRIPT", 9, bold=True, color=c["muted"]).pack(side="left")
        self.label(editor_top, variable=self.word_count, size=9, color=c["muted"]).pack(side="right")
        editor_area = self.frame(card, "editor")
        editor_area.grid(row=1, column=0, sticky="nsew", padx=1)
        self.editor = tk.Text(editor_area, wrap="word", undo=True, maxundo=100, bd=0,
                              relief="flat", padx=22, pady=16, spacing1=5, spacing3=7,
                              font=("Segoe UI", 14), bg=c["editor"], fg=c["text"],
                              insertbackground=c["accent"], selectbackground=c["soft"],
                              selectforeground=c["text"], highlightthickness=0, height=5)
        self.editor.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(editor_area, command=self.editor.yview)
        scroll.pack(side="right", fill="y")
        self.editor.configure(yscrollcommand=scroll.set)
        self.editor.bind("<<Modified>>", self.text_changed)
        self.placeholder = tk.Label(self.editor, text="The page is yours.\nStart speaking whenever you’re ready.",
                                    font=("Segoe UI", 14), fg=c["muted"], bg=c["editor"], justify="left")
        self.placeholder.place(x=23, y=20)
        self.placeholder.bind("<Button-1>", lambda e: self.editor.focus_set())
        actions = self.frame(card, "card")
        actions.grid(row=2, column=0, sticky="ew", padx=14, pady=10)
        self.button(actions, "Copy text", self.copy_text).pack(side="left")
        self.button(actions, "Save .txt", self.save_text).pack(side="left", padx=5)
        self.audio_button = self.icon_button(actions, "⇩", self.download_audio,
                                             "Download recording as audio (.m4a)")
        self.audio_button.pack(side="left", padx=(0, 5))
        self.clear_button = self.button(actions, "Clear", self.clear_text)
        self.clear_button.pack(side="left")
        self.button(actions, "History  ↗", self.open_history).pack(side="right")

        controls = self.frame(self.shell)
        controls.grid(row=4, column=0, sticky="ew", pady=(18, 10))
        left = self.frame(controls)
        left.pack(side="left", fill="x", expand=True)
        self.main_wave = Wave(left, c["bg"], c["accent"], 142, 48)
        self.main_wave.pack(side="left")
        timing = self.frame(left)
        timing.pack(side="left", padx=12)
        self.label(timing, variable=self.phase_label, size=8, bold=True, color=c["accent"]).pack(anchor="w")
        self.label(timing, variable=self.timer, size=12, bold=True).pack(anchor="w")
        control_actions = self.frame(controls)
        control_actions.pack(side="right")
        self.mute_button = self.icon_button(control_actions, "🎙", self.toggle_mute,
                                            "Mute microphone")
        self.mute_button.pack(side="left", padx=(0, 8))
        self.resume_button = self.button(control_actions, "↻  Resume", self.resume_recording)
        self.cancel_button = self.icon_button(control_actions, "✕", self.cancel,
                                              "Cancel this recording")
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.record_button = self.button(control_actions, "●  Start speaking", self.toggle_recording, primary=True)
        self.record_button.pack(side="left")
        self.status_label = self.label(self.shell, variable=self.status, size=10, color=c["muted"])
        self.status_label.configure(justify="left", anchor="w")
        self.status_label.grid(row=5, column=0, sticky="ew", pady=(0, 12))
        self.status_label.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(100, e.width-8)))
        tk.Frame(self.shell, bg=c["line"], height=1).grid(row=6, column=0, sticky="ew")
        bottom = self.frame(self.shell)
        bottom.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        self.shortcut_label = self.label(bottom, self.settings["hotkey"] + "  ·  type anywhere", 9, color=c["muted"])
        self.shortcut_label.pack(side="left")
        self.label(bottom, variable=self.model_status, size=9, color=c["muted"]).pack(side="right")
        self.apply_direction()
        self.update_controls()

    def frame(self, parent, color=None):
        return tk.Frame(parent, bg=self.colors[color] if color else parent.cget("bg"))

    def label(self, parent, text="", size=10, bold=False, color=None, variable=None):
        return tk.Label(parent, text=text, textvariable=variable, bg=parent.cget("bg"),
                        fg=color or self.colors["text"], font=("Segoe UI", size, "bold" if bold else "normal"), bd=0)

    def button(self, parent, text, command, primary=False):
        return ActionButton(parent, text, command, self.colors, primary)

    def icon_button(self, parent, icon, command, tooltip):
        return IconButton(parent, icon, command, self.colors, tooltip)

    def update_controls(self):
        self.record_button.configure(text="■  Stop & transcribe" if self.active else
                                     "Converting…" if self.finishing else "●  Start speaking",
                                     state="normal" if self.active or (self.model_ready and not self.finishing) else "disabled")
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        self.mute_button.configure(state="normal" if self.active else "disabled")
        self.audio_button.configure(state="normal" if self.export_audio is not None
                                    and (not self.busy or self.muted) and not self.exporting else "disabled")
        self.mute_button.set_icon("🔇" if self.muted else "🎙",
                                  "Unmute microphone" if self.muted else "Mute microphone")
        if self.resume_available and not self.busy:
            if not self.resume_button.winfo_manager():
                self.resume_button.pack(side="left", padx=(0, 8), before=self.cancel_button)
        elif self.resume_button.winfo_manager():
            self.resume_button.pack_forget()
        self.import_button.configure(state="normal" if self.model_ready and not self.busy else "disabled")
        self.clear_button.configure(state="disabled" if self.busy else "normal")
        self.live_check.configure(state="disabled" if self.busy else "normal")
        self.lang_combo.configure(state="disabled" if self.busy else "readonly")
        self.phase_label.set("TRANSCRIBING" if self.active and self.muted and self.pending else
                             "MUTED" if self.active and self.muted else
                             "LISTENING" if self.active else
                             "CONVERTING" if self.finishing else "READY")

    def change_language(self):
        self.settings["language"] = "fa" if self.language.get().startswith("Dari") else "en"
        self.apply_direction()
        self.persist()

    def apply_direction(self):
        self.editor.tag_configure("direction", justify="right" if self.settings["language"] == "fa" else "left")
        self.editor.tag_add("direction", "1.0", "end")
        self.editor.configure(font=("Tahoma" if self.settings["language"] == "fa" else "Segoe UI", 14))

    def text_changed(self, event=None):
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        text = self.editor.get("1.0", "end-1c")
        self.word_count.set(f"{len(text.split())} words")
        if text:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=23, y=20)
        self.apply_direction()

    def persist(self):
        self.settings["live"] = self.live.get()
        if self.state() == "normal":
            self.settings.update(width=max(760, self.winfo_width()), height=max(580, self.winfo_height()))
        try:
            save_settings(self.settings_path, self.settings)
            self.progress.save()
        except OSError as exc:
            LOGGER.warning("Could not save settings: %s", exc)
            self.status.set("Preferences could not be saved. Check folder permissions.")

    def toggle_theme(self):
        if self.busy:
            return
        text = self.editor.get("1.0", "end-1c")
        self.settings["theme"] = "light" if self.settings["theme"] == "dark" else "dark"
        for window in (self.settings_window, self.history_window, self.about_window):
            if window and window.winfo_exists():
                window.destroy()
        self.shell.destroy()
        self._build_ui()
        self.editor.insert("1.0", text)
        self.persist()

    def scan_models(self):
        if self.busy or self.loading:
            return
        self.loading = True
        self.model_ready = False
        self.update_controls()
        directory = Path(self.settings.get("models_dir", DEFAULT_MODELS_DIR))
        if not directory.is_dir():
            directory = DEFAULT_MODELS_DIR
        def scan():
            try:
                self.emit("models", discover_models(directory))
            except Exception as exc:
                self.emit("model_error", str(exc))
        threading.Thread(target=scan, daemon=True, name="model-discovery").start()

    def select_model(self, info):
        if self.busy:
            return
        self.current_info = info
        self.model_ready = False
        self.loading = True
        self.status.set(f"Loading {info.display_name}…")
        self.model_status.set("LOADING LOCAL ENGINE")
        self.update_controls()
        self.worker.load(info)

    def set_hotkey(self):
        if self.hotkey:
            self.hotkey.close()
        self.hotkey = GlobalHotkey(self.settings["hotkey"], self.emit)

    def show_overlay(self, visible=True):
        if visible:
            if self.overlay is None:
                self.overlay = FloatingWave(self, self.overlay_action,
                                             self.stop_recording, self.show_main, self.quit_app,
                                             position=self.settings["overlay_position"],
                                             position_changed=self.save_overlay_position)
            self.overlay.deiconify()
        elif self.overlay:
            self.overlay.withdraw()

    def save_overlay_position(self, position):
        self.settings["overlay_position"] = list(position)
        self.persist()

    def overlay_action(self):
        if self.finishing:
            self.status.set("Converting locally · the floating bar shows the current estimate.")
            return
        self.toggle_recording(external=True)

    def show_main(self):
        self.deiconify()
        self.lift()

    def toggle_recording(self, external=False, target=None):
        if self.active:
            self.stop_recording(manual=True)
        elif not self.finishing:
            self.start_recording(external, target)

    def begin_session(self, source, target=None, append=False):
        self.session += 1
        self.cancel_event = threading.Event()
        self.pending = 0
        self.parts = []
        self.draft = ""
        self.audio_jobs.clear()
        self.segment_id = 0
        self.processing_seconds = self.elapsed = 0.0
        self.progress.reset()
        self.job_weights = []
        self.display_progress = 0
        self.submitted = 0
        self.completed_at = 0
        self.target = target
        self.source = source
        self.append_session = bool(append)
        self.resume_available = False
        self.manual_finish_requested = False
        self.muted = False
        self.editor.configure(state="normal")
        self.original_editor_text = self.editor.get("1.0", "end-1c")
        self.session_base_text = self.original_editor_text if append else ""
        self.editor.edit_separator()
        self.editor.configure(state="disabled")

    def start_recording(self, external=False, target=None, append=False):
        if self.busy:
            return
        if not self.model_ready:
            self.status.set("The speech model is still loading. Open Settings if no model is found.")
            return
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        target = (target or capture_target()) if external else None
        self.begin_session("Dictation" if target else "Microphone", target, append=append)
        # External dictation needs only one final pass; live preview is an editor option.
        self.buffer = AudioBuffer(self.settings["threshold"], self.settings["silence_seconds"],
                                  live=self.live.get() and not target)
        self.microphone = Microphone(self.buffer)
        try:
            name = self.settings["microphone"]
            if name not in self.devices:
                raise RuntimeError("Saved microphone is unavailable. Choose a microphone in Settings.")
            self.microphone.start(self.devices[name])
        except Exception as exc:
            self.microphone = None
            self.editor.configure(state="normal")
            self.resume_available = append and bool(self.original_editor_text.strip())
            self.status.set(f"Microphone could not start: {exc}")
            self.update_controls()
            return
        self.active = True
        self.started_at = time.monotonic()
        self.status.set("Listening · text will go to your input after you pause." if target else
                        "Listening · pause to finish, or press Finish.")
        self.update_controls()

    def resume_recording(self):
        """Continue the current text only after a successful manual finish."""
        if not self.resume_available or self.busy:
            return
        self.start_recording(append=True)

    def toggle_mute(self):
        """Finalize everything up to Mute; Unmute continues the same transcript."""
        if not self.active or not self.microphone:
            return
        try:
            self.microphone.set_muted(not self.muted)
        except Exception as exc:
            self.fail_session(f"Microphone pause failed: {exc}")
            return
        self.muted = not self.muted
        if self.muted:
            self.export_audio = self.buffer.recorded_audio()
            self._submit_segment()
        self.status.set("Muted · transcribing captured speech…" if self.muted and self.pending else
                        "Muted · transcript ready; unmute to continue." if self.muted else
                        "Listening · continuing your transcript.")
        self.update_controls()

    def _submit_segment(self):
        audio = self.buffer.take_phrase(final=True)
        self.draft = ""
        self.submit_audio(audio, mode="final", segment=self.segment_id)
        self.segment_id += 1

    def _session_text(self):
        return " ".join(self.parts).strip()

    def _render_session_text(self):
        """Render a stable preview: a normal session replaces; Resume appends."""
        transcript = " ".join(part for part in (self._session_text(), self.draft) if part)
        if self.append_session and self.session_base_text.strip() and transcript:
            rendered = self.session_base_text.rstrip() + "\n\n" + transcript
        elif self.append_session and not transcript:
            rendered = self.session_base_text
        elif transcript:
            rendered = transcript
        else:
            rendered = self.original_editor_text
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", rendered, "direction")
        self.editor.see("end")
        if self.busy:
            self.editor.configure(state="disabled")

    def submit_audio(self, audio, mode="final", segment=None):
        if audio is None:
            return
        try:
            self.worker.transcribe(self.session, audio, self.settings["language"],
                                   self.cancel_event, self.settings["quality"])
            self.audio_jobs.append((mode, self.segment_id if segment is None else segment))
            self.pending += 1
            self.submitted += 1
            duration = len(audio)/16000 if not isinstance(audio, Path) else 0
            key = (self.current_info.key, self.settings["language"], self.settings["quality"])
            self.job_weights.append(self.progress.estimate(key, duration))
        except queue.Full:
            self.fail_session("Speech processing fell behind. Please record shorter phrases.")

    def stop_recording(self, reason="", manual=True):
        if not self.active:
            return
        self.manual_finish_requested = bool(manual)
        self.active = False
        self.finishing = True
        try:
            self.microphone.close()
        except Exception as exc:
            LOGGER.warning("Microphone close failed: %s", exc)
        self.microphone = None
        self.muted = False
        self.export_audio = self.buffer.recorded_audio()
        self._submit_segment()
        if not self.finishing:
            return
        self.status.set((reason + " · " if reason else "") + "Finishing your transcript…")
        self.update_controls()
        self.finish_if_ready()

    def finish_if_ready(self):
        if not self.finishing or self.pending:
            return
        self.finishing = False
        self.progress.finish()
        self.completed_at = time.monotonic()
        self.timer.set(f"{self.processing_seconds:.1f}s")
        try:
            self.progress.save()
        except OSError as exc:
            LOGGER.warning("Timing calibration could not be saved: %s", exc)
        self.editor.configure(state="normal")
        self.editor.edit_separator()
        text = self._session_text()
        if text:
            self._render_session_text()
            if self.settings["save_history"]:
                try:
                    self.history.add(self.source, self.current_info.display_name, text)
                except Exception as exc:
                    LOGGER.warning("History failed: %s", exc)
            self.status.set(f"Ready · {len(text.split())} words · {self.processing_seconds:.1f}s processing")
            if self.target and self.settings["auto_paste"]:
                self._insert_when_released(self.session, self.target, text, 0)
            self.resume_available = self.manual_finish_requested and self.target is None
        else:
            self._render_session_text()
            self.status.set("No speech found. Check your microphone or lower the sensitivity threshold in Settings.")
        self.update_controls()

    def _insert_when_released(self, session, target, text, attempt):
        if self.closed or session != self.session or self.cancel_event.is_set():
            return
        if modifiers_pressed() and attempt < 20:
            self.after(100, lambda: self._insert_when_released(session, target, text, attempt+1))
            return
        try:
            if self.clipboard_paste:
                self.clipboard_paste.restore()
            from windows_integration import user32
            self.clipboard_paste = insert_text(target, text, self.settings["insertion_method"],
                                               self.after, user32.GetAncestor(self.winfo_id(), 2))
            self.status.set("Paste sent · a copy is saved here. Check your input if the app blocks paste.")
        except RuntimeError as exc:
            self.status.set(str(exc))

    def fail_session(self, message):
        self.cancel()
        self.status.set(message)

    def cancel(self):
        if not self.busy:
            return
        self.cancel_event.set()
        self.session += 1  # Stale worker results can never leak into a later recording.
        if self.microphone:
            try:
                self.microphone.close()
            except Exception:
                LOGGER.exception("Microphone cleanup failed")
        self.microphone = None
        if self.buffer:
            self.export_audio = self.buffer.recorded_audio()
        self.active = self.finishing = False
        self.pending = 0
        self.progress.reset()
        self.completed_at = 0
        self.target = None
        self.muted = False
        self.resume_available = False
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.original_editor_text, "direction")
        self.status.set("Cancelled · existing text is kept; nothing was inserted.")
        self.update_controls()

    def _tick(self):
        if self.closed:
            return
        for _ in range(40):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._event(kind, payload)
            except Exception:
                LOGGER.exception("UI event failed: %s", kind)
                self.status.set("An operation failed. See runtime/data/app.log for details.")
        level = 0
        if self.active and self.buffer:
            snap = self.buffer.snapshot()
            self.elapsed = snap.elapsed
            level = snap.level
            self.timer.set(f"{int(snap.elapsed)//60:02d}:{int(snap.elapsed)%60:02d}")
            if snap.error:
                self.fail_session(snap.error)
            elif not self.muted and snap.stop_reason:
                self.stop_recording(snap.stop_reason, manual=False)
            elif (not self.muted and time.monotonic() - self.started_at > 3
                  and not getattr(getattr(self.microphone, "stream", None), "active", True)):
                self.fail_session("Microphone disconnected. Reconnect it and record again.")
            elif not self.muted and not self.pending:
                self.submit_audio(self.buffer.take_preview(), mode="preview")
            if snap.warning:
                self.status.set(f"Microphone warning: {snap.warning}. Check your audio device.")
        complete_flash = not self.busy and bool(self.completed_at) and time.monotonic()-self.completed_at < .7
        progress = overall_fraction(self.job_weights, self.submitted-self.pending, self.progress.fraction())
        self.display_progress = max(self.display_progress, progress) if self.finishing else progress
        progress = 1.0 if complete_flash else self.display_progress
        converting = self.finishing or (self.active and self.muted and bool(self.pending))
        if converting:
            # Reserve 100% for the real worker completion event; calibrated
            # progress may approach 99% while a slower-than-usual CPU finishes.
            self.timer.set(f"≈ {min(99, int(progress*100))}%")
        if self.state() != "withdrawn":
            self.main_wave.draw(level, self.active and not self.muted,
                                converting or complete_flash, progress)
        if self.overlay and self.overlay.winfo_viewable():
            self.overlay.update_wave(level, self.active, converting or complete_flash,
                                     progress, muted=self.muted)
        self.after(60 if self.busy else 180, self._tick)

    def _event(self, kind, payload):
        if kind == "audio_saved":
            self.exporting = False
            self.status.set(f"Audio saved: {payload}")
            self.update_controls()
        elif kind == "audio_save_error":
            self.exporting = False
            self.status.set(f"Audio could not be saved: {payload}")
            self.update_controls()
        elif kind == "models":
            models, diagnostics = payload
            self.models = [m for m in models if m.status == "ready"]
            self.loading = False
            if self.models:
                preferred = self.settings.get("selected_model_key")
                self.select_model(next((m for m in self.models if m.key == preferred), self.models[0]))
            else:
                self.status.set("No usable local model. Choose your models folder in Settings.")
                self.model_status.set("MODEL NEEDED")
        elif kind == "model_ready":
            self.current_info = payload
            self.model_ready, self.loading = True, False
            self.settings["selected_model_key"] = payload.key
            self.model_status.set(payload.display_name + " · OFFLINE")
            self.status.set("Ready · speak here, or use the floating wave in any input.")
            self.update_controls()
            self.persist()
        elif kind == "model_error":
            self.loading = self.model_ready = False
            self.model_status.set("ENGINE UNAVAILABLE")
            self.status.set("Model error: " + payload)
            self.update_controls()
        elif kind == "devices":
            self.devices = payload
        elif kind == "hotkey":
            self.toggle_recording(external=True, target=payload)
        elif kind == "hotkey_status":
            name, registered = payload
            self.shortcut_label.configure(text=name + ("  ·  type anywhere" if registered else "  ·  unavailable; use the wave"))
        elif kind == "notice":
            self.status.set(payload)
        elif kind == "progress_started":
            session, key, duration, started = payload
            if session == self.session and not self.cancel_event.is_set():
                self.progress.start(key, duration, started)
        elif kind == "progress_duration":
            session, duration = payload
            if session == self.session and not self.cancel_event.is_set():
                self.progress.set_duration(duration)
                index = self.submitted-self.pending
                if index < len(self.job_weights):
                    self.job_weights[index] = self.progress.expected
        elif kind == "progress":
            session, fraction = payload
            if session == self.session and not self.cancel_event.is_set():
                self.progress.report(fraction)
        elif kind == "progress_timing":
            session, key, duration, seconds = payload
            if session == self.session and not self.cancel_event.is_set():
                self.progress.learn(key, duration, seconds)
        elif kind == "result":
            session, text, seconds = payload
            if session != self.session or self.cancel_event.is_set():
                return
            self.pending = max(0, self.pending-1)
            self.processing_seconds += seconds
            mode, segment = self.audio_jobs.popleft() if self.audio_jobs else ("final", self.segment_id)
            if mode == "preview":
                if segment == self.segment_id and self.active and not self.muted:
                    self.draft = text.strip()
            else:
                if text.strip():
                    self.parts.append(text.strip())
            self._render_session_text()
            self.finish_if_ready()
            if self.active and self.muted and not self.pending:
                self.status.set("Muted · transcript ready; unmute to continue.")
            self.update_controls()
        elif kind == "transcribe_error":
            session, error = payload
            if session == self.session:
                self.fail_session("Transcription failed: " + error)

    def import_audio(self):
        if self.busy or not self.model_ready:
            return
        path = filedialog.askopenfilename(parent=self, title="Transcribe an audio file",
                                          filetypes=[("Audio / video", "*.wav *.mp3 *.m4a *.mp4 *.flac *.ogg *.webm *.mpeg"), ("All files", "*.*")])
        if path:
            self.begin_session(Path(path).name)
            self.finishing = True
            self.submit_audio(Path(path))
            self.status.set("Transcribing your file locally…")
            self.update_controls()

    def copy_text(self):
        text = self.editor.get("1.0", "end-1c").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.set("Copied to clipboard.")

    def download_audio(self):
        if self.export_audio is None or (self.busy and not self.muted) or self.exporting:
            return
        from audio_export import music_directory, default_voice_name, validate_name, save_voice
        try:
            folder = music_directory() / "NR Voice Typing Voices"
            suggested = default_voice_name(folder)
        except OSError as exc:
            self.status.set(f"Music folder is unavailable: {exc}")
            return
        name = simpledialog.askstring("Save recording", "Audio filename (.m4a) — English or Dari / فارسی:",
                                      initialvalue=suggested, parent=self)
        if name is None:
            return
        try:
            name = validate_name(name)
        except ValueError as exc:
            messagebox.showerror("Invalid filename", str(exc), parent=self)
            return
        audio = self.export_audio
        self.exporting = True
        self.update_controls()
        self.status.set("Compressing audio for download…")
        def export():
            try:
                self.emit("audio_saved", save_voice(audio, folder, name))
            except Exception as exc:
                self.emit("audio_save_error", str(exc))
        threading.Thread(target=export, daemon=True, name="voice-export").start()

    def save_text(self):
        text = self.editor.get("1.0", "end-1c").strip()
        if not text:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
                self.status.set("Transcript saved.")
            except OSError as exc:
                self.status.set(f"Could not save transcript: {exc}")

    def clear_text(self):
        if not self.busy:
            self.editor.delete("1.0", "end")

    def open_about(self):
        """Show product and creator information in one predictable place."""
        if self.about_window and self.about_window.winfo_exists():
            self.about_window.lift()
            return
        win = self.about_window = tk.Toplevel(self)
        win.title(f"About · {APP_NAME}")
        win.geometry("680x500")
        win.minsize(620, 450)
        win.configure(bg=self.colors["bg"])
        body = self.frame(win)
        body.pack(fill="both", expand=True, padx=24, pady=20)

        heading = self.frame(body)
        heading.pack(fill="x", pady=(0, 14))
        if self.brand_logo:
            factor = max(1, self.brand_logo.width() // 64)
            about_logo = self.brand_logo.subsample(factor, factor)
            self._ui_images.append(about_logo)
            tk.Label(heading, image=about_logo, bg=self.colors["bg"], bd=0).pack(side="left", padx=(0, 14))
        title = self.frame(heading)
        title.pack(side="left", fill="x", expand=True)
        self.label(title, APP_NAME, 22, bold=True).pack(anchor="w")
        self.label(title, f"Version {APP_VERSION} · Private by design · MIT licensed",
                   10, color=self.colors["muted"]).pack(anchor="w", pady=(4, 0))

        style = ttk.Style(self)
        style.configure("About.TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("About.TNotebook.Tab", padding=(16, 9), font=("Segoe UI", 10, "bold"))
        tabs = ttk.Notebook(body, style="About.TNotebook")
        tabs.pack(fill="both", expand=True)
        app_tab = self.frame(tabs, "card")
        me_tab = self.frame(tabs, "card")
        tabs.add(app_tab, text="About app")
        tabs.add(me_tab, text="About me")

        self.label(app_tab, "Offline voice typing that stays on your computer", 15,
                   bold=True).pack(anchor="w", padx=22, pady=(24, 12))
        app_copy = (
            "NR Voice Typing converts English and Dari/Persian speech into text using "
            "a locally installed model. Normal dictation does not send your audio to a "
            "cloud speech service.\n\n"
            "Use the main button, the global shortcut, or the transparent floating wave. "
            "Stop & transcribe replaces the editor text; Resume continues it. Timing and "
            "history stay in your local data folder.\n\n"
            "The source code is released under the MIT License so the community can inspect, "
            "learn from, and improve it while preserving the license and copyright notice."
        )
        self.label(app_tab, app_copy, 11, color=self.colors["muted"]).pack(
            anchor="w", fill="x", padx=22, pady=(0, 20))
        app_tab.winfo_children()[-1].configure(wraplength=570, justify="left")

        self.label(me_tab, CREATOR_NAME, 19, bold=True).pack(anchor="w", padx=22, pady=(28, 5))
        self.label(me_tab, CREATOR_ROLE, 11, color=self.colors["accent"]).pack(anchor="w", padx=22)
        self.label(me_tab, f"Contact: {CREATOR_CONTACT}", 11,
                   color=self.colors["muted"]).pack(anchor="w", padx=22, pady=(18, 0))
        self.label(me_tab, GITHUB_PROFILE, 10,
                   color=self.colors["accent"]).pack(anchor="w", padx=22, pady=(8, 0))
        dedication = (
            "Concept and development by Nooraqa Rasouli. Built to make private, offline "
            "voice input more practical for people who work across languages and accents.\n\n"
            "Created with gratitude and dedicated to family."
        )
        self.label(me_tab, dedication, 11, color=self.colors["muted"]).pack(
            anchor="w", fill="x", padx=22, pady=(22, 20))
        me_tab.winfo_children()[-1].configure(wraplength=570, justify="left")

    def open_settings(self):
        if self.busy or self.loading:
            self.status.set("Finish the current operation before changing settings.")
            return
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        win = self.settings_window = tk.Toplevel(self)
        win.title(f"{APP_NAME} · Settings")
        win.configure(bg=self.colors["bg"])
        win.resizable(False, False)
        body = self.frame(win)
        body.pack(fill="both", expand=True, padx=26, pady=22)
        self.label(body, "Make it yours", 21, bold=True).pack(anchor="w", pady=(0, 16))
        fields = {}
        def choice(label, key, values, current):
            row = self.frame(body)
            row.pack(fill="x", pady=5)
            self.label(row, label).pack(side="left")
            var = tk.StringVar(value=current)
            box = ttk.Combobox(row, textvariable=var, values=values, state="readonly", width=38)
            box.pack(side="right", padx=(24, 0))
            fields[key] = var
        names = [m.display_name for m in self.models]
        choice("Speech model", "model", names, self.current_info.display_name if self.current_info else "No model")
        choice("Microphone", "microphone", list(self.devices), self.settings["microphone"])
        choice("Global shortcut", "hotkey", list(HOTKEYS), self.settings["hotkey"])
        choice("Recognition", "quality", ["Fast", "Balanced", "Accurate"], self.settings["quality"])
        choice("Insert into other apps", "insertion_method", ["Clipboard paste", "Unicode typing"], self.settings["insertion_method"])
        choice("Stop after silence", "silence_seconds", ["0.8", "1.0", "1.5", "2.0", "3.0", "5.0", "8.0"], str(self.settings["silence_seconds"]))
        choice("Voice threshold", "threshold", ["0.002", "0.004", "0.008", "0.015", "0.03", "0.06"], str(self.settings["threshold"]))
        self.label(body, "Voice detection ignores non-speech. Lower threshold for a quiet voice; raise it for distant speech.", 9, color=self.colors["muted"]).pack(anchor="w", pady=(5, 12))
        self.label(body, "Accurate checks more alternatives for difficult speech; it takes longer.\nFor English with any accent, choose English. Try 3–5 seconds of silence for longer pauses.\nClipboard paste preserves Unicode and restores your clipboard. Use typing if an app blocks paste.",
                   9, color=self.colors["muted"]).pack(anchor="w", pady=(0, 10))
        for key, label in (("auto_paste", "Insert finished dictation into the original input"),
                           ("overlay", "Show the transparent floating wave"),
                           ("background_on_close", "Keep running when I close the main window"),
                           ("save_history", "Save transcripts in local history")):
            var = fields[key] = tk.BooleanVar(value=self.settings[key])
            tk.Checkbutton(body, text=label, variable=var, bg=self.colors["bg"], fg=self.colors["text"],
                           selectcolor=self.colors["editor"], activebackground=self.colors["bg"],
                           activeforeground=self.colors["text"], font=("Segoe UI", 10), bd=0).pack(anchor="w", pady=3)
        self.label(body, "Live preview is provisional; Stop or Mute finalizes the full sentence.\nA close microphone helps reduce other voices; speaker isolation is not guaranteed.\nWave: click to start / finish · drag to save position · right-click for menu.",
                   9, color=self.colors["muted"]).pack(anchor="w", pady=14)
        actions = self.frame(body)
        actions.pack(fill="x")
        def choose_folder():
            folder = filedialog.askdirectory(parent=win, title="Choose local models folder")
            if folder:
                root, hint = normalize_models_directory(Path(folder))
                self.settings["models_dir"] = str(root)
                if hint:
                    self.settings["selected_model_key"] = str(hint)
                win.destroy()
                self.scan_models()
                self.persist()
        self.button(actions, "Models folder…", choose_folder).pack(side="left")
        self.button(actions, "Quit app", self.quit_app).pack(side="left", padx=8)
        def apply():
            old_hotkey = self.settings["hotkey"]
            for key, var in fields.items():
                if key != "model":
                    self.settings[key] = float(var.get()) if key in ("threshold", "silence_seconds") else var.get()
            if old_hotkey != self.settings["hotkey"] and self.services_enabled:
                self.set_hotkey()
            if self.services_enabled:
                self.show_overlay(self.settings["overlay"])
            selected = next((m for m in self.models if m.display_name == fields["model"].get()), None)
            win.destroy()
            if selected and (not self.current_info or selected.key != self.current_info.key):
                self.select_model(selected)
            self.persist()
        self.button(actions, "Save settings", apply, primary=True).pack(side="right")

    def open_history(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.lift()
            return
        win = self.history_window = tk.Toplevel(self)
        win.title(f"{APP_NAME} · Local history")
        win.geometry("760x510")
        win.configure(bg=self.colors["bg"])
        body = self.frame(win)
        body.pack(fill="both", expand=True, padx=20, pady=20)
        self.label(body, "Your recent words", 20, bold=True).pack(anchor="w", pady=(0, 12))
        query = tk.StringVar()
        entry = tk.Entry(body, textvariable=query, font=("Segoe UI", 11), bg=self.colors["editor"], fg=self.colors["text"], insertbackground=self.colors["accent"], relief="flat")
        entry.pack(fill="x", ipady=8)
        listing = tk.Listbox(body, height=6, bg=self.colors["card"], fg=self.colors["text"],
                             selectbackground=self.colors["soft"], selectforeground=self.colors["text"], bd=0,
                             highlightthickness=0, font=("Segoe UI", 10), exportselection=False,
                             selectmode=tk.EXTENDED)
        listing.pack(fill="x", pady=12)
        preview = tk.Text(body, wrap="word", height=6, bg=self.colors["editor"], fg=self.colors["text"],
                          font=("Segoe UI", 12), bd=0, padx=12, pady=10)
        preview.pack(fill="both", expand=True)
        rows = []
        def refresh(*args):
            rows[:] = self.history.search(query.get())
            listing.delete(0, "end")
            for row in rows:
                listing.insert("end", f"{row[1].replace('T', '  ')}   ·   {row[4][:65]}")
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.configure(state="disabled")
        def selected():
            return [rows[index] for index in listing.curselection() if index < len(rows)]
        def show(event=None):
            chosen = selected()
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            if len(chosen) == 1:
                preview.insert("1.0", chosen[0][4])
            elif chosen:
                preview.insert("1.0", f"{len(chosen)} transcripts selected")
            preview.configure(state="disabled")
        def use():
            chosen = selected()
            if chosen and not self.busy:
                if self.editor.get("1.0", "end-1c").strip():
                    self.editor.insert("end", "\n\n")
                self.editor.insert("end", "\n\n".join(row[4] for row in chosen))
                win.destroy()
        def delete():
            chosen = selected()
            count = len(chosen)
            prompt = "Delete this saved transcript?" if count == 1 else f"Delete these {count} saved transcripts?"
            if chosen and messagebox.askyesno("Delete selected", prompt, parent=win):
                self.history.delete_many(row[0] for row in chosen)
                refresh()
        def select_all(event=None):
            listing.selection_set(0, "end")
            show()
            return "break"
        listing.bind("<<ListboxSelect>>", show)
        listing.bind("<Control-a>", select_all)
        query.trace_add("write", refresh)
        actions = self.frame(body)
        actions.pack(fill="x", pady=(12, 0))
        self.button(actions, "Select all", select_all).pack(side="left")
        self.button(actions, "🗑  Delete selected", delete).pack(side="left", padx=8)
        self.button(actions, "Add to editor", use, primary=True).pack(side="right")
        refresh()

    def close_to_background(self):
        if self.settings["background_on_close"] and self.services_enabled:
            self.persist()
            self.show_overlay(True)
            self.withdraw()
        else:
            self.quit_app()

    def quit_app(self):
        if self.closed:
            return
        if self.exporting:
            self.status.set("Finishing the audio download before closing…")
            self.after(200, self.quit_app)
            return
        self.cancel()
        self.persist()
        self.closed = True
        if self.clipboard_paste:
            self.clipboard_paste.restore()
        self.worker.close()
        if self.hotkey:
            self.hotkey.close()
        if self.overlay:
            self.overlay.destroy()
            self.overlay = None
        self.destroy()
