# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Input safeguards: Unicode payloads, focus changes, and clipboard ownership."""
import ctypes as C
import unittest
from unittest.mock import Mock, patch

from clipboard_insertion import ClipboardPaste, _allocate, _duplicate, kernel
from windows_integration import IS_WINDOWS, InputTarget, insert_text


@unittest.skipUnless(IS_WINDOWS, "Native Windows insertion")
class InsertionTests(unittest.TestCase):
    target = InputTarget(1, 2, 3)

    def test_long_unicode_backup_is_lossless(self):
        text = ("سلام دنیا — café — English 👋 " * 50).encode("utf-16-le") + b"\0\0"
        original = _allocate(text)
        copied = None
        try:
            copied = _duplicate(13, original)
            pointer = kernel.GlobalLock(copied)
            try:
                self.assertEqual(C.string_at(pointer, len(text)), text)
            finally:
                kernel.GlobalUnlock(copied)
        finally:
            kernel.GlobalFree(original)
            if copied:
                kernel.GlobalFree(copied)

    def test_paste_is_layout_independent_and_restoration_is_scheduled(self):
        schedule = Mock()
        with patch("windows_integration.target_is_current", return_value=True), \
             patch("windows_integration.modifiers_pressed", return_value=False), \
             patch("clipboard_insertion.ClipboardPaste") as factory, \
             patch("windows_integration.user32.SendInput", return_value=4) as send:
            transaction = insert_text(self.target, "سلام\nEnglish", schedule=schedule, owner=7)
            factory.return_value.prepare.assert_called_once_with("سلام English")
            events = send.call_args.args[1]
            self.assertEqual([event.ki.wVk for event in events], [0x10, 0x2D, 0x2D, 0x10])
            self.assertEqual(schedule.call_args.args[0], 900)
            transaction.restore.assert_not_called()

    def test_focus_change_during_preparation_restores_without_typing(self):
        with patch("windows_integration.target_is_current", side_effect=[True, False]), \
             patch("windows_integration.modifiers_pressed", return_value=False), \
             patch("clipboard_insertion.ClipboardPaste") as factory, \
             patch("windows_integration.user32.SendInput") as send:
            with self.assertRaisesRegex(RuntimeError, "Destination changed"):
                insert_text(self.target, "test")
            send.assert_not_called()
            factory.return_value.restore.assert_called_once()

    def test_partial_native_send_releases_keys_and_never_retries_text(self):
        with patch("windows_integration.target_is_current", return_value=True), \
             patch("windows_integration.modifiers_pressed", return_value=False), \
             patch("clipboard_insertion.ClipboardPaste") as factory, \
             patch("windows_integration.user32.SendInput", side_effect=[2, 2]) as send:
            with self.assertRaisesRegex(RuntimeError, "blocked paste"):
                insert_text(self.target, "test")
            self.assertEqual(send.call_count, 2)
            self.assertTrue(all(event.ki.dwFlags & 2 for event in send.call_args.args[1]))
            factory.return_value.restore.assert_called_once()

    def test_user_copy_wins_over_delayed_restore(self):
        transaction = ClipboardPaste(1)
        transaction.sequence = 10
        with patch("clipboard_insertion.user") as user:
            user.OpenClipboard.return_value = True
            user.GetClipboardSequenceNumber.return_value = 11
            transaction.restore()
            user.EmptyClipboard.assert_not_called()
            self.assertTrue(transaction.closed)

    def test_windows_format_conversion_does_not_prevent_restoration(self):
        transaction = ClipboardPaste(1)
        transaction.sequence = 10
        with patch("clipboard_insertion.user") as user, \
             patch.object(transaction, "_still_owns_clipboard", return_value=True):
            user.OpenClipboard.return_value = True
            user.GetClipboardSequenceNumber.return_value = 12
            transaction.restore()
            user.EmptyClipboard.assert_called_once()
            self.assertTrue(transaction.closed)

    def test_marker_requires_the_exact_transaction_bytes(self):
        transaction = ClipboardPaste(1)
        transaction.marker_format = 100
        handle = _allocate(transaction.marker)
        try:
            with patch("clipboard_insertion.user.GetClipboardData", return_value=handle):
                self.assertTrue(transaction._still_owns_clipboard())
                transaction.marker = b"different-marker"
                self.assertFalse(transaction._still_owns_clipboard())
        finally:
            kernel.GlobalFree(handle)

    def test_locked_clipboard_retries_without_blocking_ui(self):
        transaction = ClipboardPaste(1)
        schedule = Mock()
        with patch("clipboard_insertion.user") as user:
            user.OpenClipboard.return_value = False
            transaction.restore(schedule)
            user.EmptyClipboard.assert_not_called()
            self.assertFalse(transaction.closed)
            self.assertEqual(schedule.call_args.args[0], 120)

    def test_unsafe_backup_failure_never_empties_clipboard(self):
        transaction = ClipboardPaste(1)
        with patch("clipboard_insertion.user") as user, \
             patch("clipboard_insertion._duplicate", side_effect=RuntimeError("unsupported")):
            user.OpenClipboard.return_value = True
            user.EnumClipboardFormats.return_value = 3
            user.GetClipboardData.return_value = 123
            with self.assertRaisesRegex(RuntimeError, "unsupported"):
                transaction.prepare("test")
            user.EmptyClipboard.assert_not_called()
