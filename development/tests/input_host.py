# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""A separate test-owned Windows application with two editable input fields."""
import json
import sys
import os
import tkinter as tk
from pathlib import Path
import ctypes as C
from ctypes import wintypes as W

directory = Path(sys.argv[1])
root = tk.Tk()
root.title("Voice Studio — isolated input test")
root.geometry("640x300+30+50")
tk.Label(root, text="Automated dictation test — no user document is used", font=("Segoe UI", 12)).pack(pady=12)
first = tk.Text(root, height=4, font=("Segoe UI", 13))
first.pack(fill="x", padx=12)
second = tk.Entry(root, font=("Segoe UI", 13))
second.pack(fill="x", padx=12, pady=12)
root.update()
user = C.WinDLL("user32")
user.GetAncestor.argtypes = [W.HWND, W.UINT]
user.GetAncestor.restype = W.HWND
hwnd = user.GetAncestor(root.winfo_id(), 2)
root.lift()
first.focus_force()
last = ""


def tick():
    global last
    command_path = directory / "command.json"
    try:
        command = json.loads(command_path.read_text())
        if command["id"] != last:
            last = command["id"]
            action = command["action"]
            if action == "quit":
                root.destroy()
                return
            if action == "clear":
                first.delete("1.0", "end")
                second.delete(0, "end")
            if action in ("first", "second"):
                root.lift()
                (first if action == "first" else second).focus_force()
    except (OSError, ValueError):
        pass
    state = {"hwnd": hwnd, "pid": os.getpid(), "first": first.get("1.0", "end-1c"),
             "second": second.get(), "command": last,
             "first_point": [first.winfo_rootx()+20, first.winfo_rooty()+20],
             "second_point": [second.winfo_rootx()+20, second.winfo_rooty()+10]}
    temporary = directory / "host-state.tmp"
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(directory / "host-state.json")
    except OSError:
        # Windows can briefly deny replacement while the test reads the old file.
        pass
    root.after(60, tick)


tick()
root.mainloop()
