"""Telegram: alerts out, commands in. Errors never print the bot token."""
from __future__ import annotations
import requests


class Telegram:
    def __init__(self, token=None, chat_id=None, dry=False):
        self.token, self.chat_id, self.dry = token, str(chat_id) if chat_id else None, dry
        self.sent: list[str] = []
        self.log: list[tuple] = []          # (text, buttons) for every message, used by the self-test

    @property
    def ok(self):
        return self.dry or bool(self.token and self.chat_id)

    def _call(self, method, **kw):
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", timeout=25, **kw)
        return r

    def send(self, text: str, buttons=None) -> bool:
        """buttons: list of rows, each row a list of (label, callback_data)."""
        self.sent.append(text)
        self.log.append((text, buttons))
        self.last_buttons = buttons
        if self.dry:
            print("--- TELEGRAM ---\n" + text + ("\n[buttons: " + " | ".join(b[0] for r in buttons for b in r) + "]" if buttons else "") + "\n----------------")
            return True
        if not self.ok:
            print("telegram: not configured")
            return False
        for chunk in [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]:
            try:
                body = {"chat_id": self.chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
                if buttons and len(text) <= 3800:
                    body["reply_markup"] = {"inline_keyboard": [[{"text": l, "callback_data": d} for l, d in row] for row in buttons]}
                r = self._call("sendMessage", json=body)
                if r.status_code != 200:
                    print(f"telegram: send failed {r.status_code}")
                    return False
            except Exception as e:
                print(f"telegram: send error {type(e).__name__}")
                return False
        return True

    def send_document(self, filename: str, data: bytes, caption: str = "") -> bool:
        if self.dry:
            print(f"--- TELEGRAM FILE {filename} ({len(data)} bytes) ---")
            return True
        if not self.ok:
            return False
        try:
            r = self._call("sendDocument", data={"chat_id": self.chat_id, "caption": caption}, files={"document": (filename, data)})
            return r.status_code == 200
        except Exception as e:
            print(f"telegram: document error {type(e).__name__}")
            return False

    def answer_callback(self, callback_id: str, text: str = ""):
        if self.dry or not self.ok:
            return
        try:
            self._call("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})
        except Exception:
            pass          # old taps expire; harmless

    def get_updates(self, offset=None) -> list[dict]:
        if self.dry or not self.ok:
            return []
        try:
            r = self._call("getUpdates", json={"offset": offset, "timeout": 0, "allowed_updates": ["message", "callback_query"]})
            return r.json().get("result", []) if r.status_code == 200 else []
        except Exception as e:
            print(f"telegram: poll error {type(e).__name__}")
            return []
