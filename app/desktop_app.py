# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Windows entry point for NR Voice Typing. Runs with ordinary user privileges."""
from speech_engine import LOGGER, ROOT
from windows_integration import SingleInstance
from app_info import APP_NAME


def main():
    instance = SingleInstance(ROOT)
    try:
        if instance.already_running:
            instance.show_existing()
            return
        from voice_app import VoiceTypingApp
        app = VoiceTypingApp()
        app.mainloop()
    except Exception as exc:
        LOGGER.exception("The app could not start")
        # pythonw has no console: make a startup failure visible to the user.
        try:
            import tkinter as tk
            from tkinter import messagebox
            error_root = tk.Tk()
            error_root.withdraw()
            messagebox.showerror(f"{APP_NAME} could not start",
                                 f"{exc}\n\nCheck runtime/data/app.log. If dependencies are missing, run 2 - Get Libraries.cmd.",
                                 parent=error_root)
            error_root.destroy()
        except Exception:
            pass
        raise
    finally:
        instance.close()


if __name__ == "__main__":
    main()
