#!/usr/bin/env python3
"""Claude Endpoint Mapper (Nobara / Linux): Tk front end for Claude V100.

Pick an endpoint, fetch its models, choose a tier and friendly name per model,
then Apply. Apply runs `endpoint_mapper.py apply --restart` as a subprocess; the
API keys travel on its stdin and never appear in a command line.
"""
import os
import pathlib
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import em_config  # noqa: E402
import em_form  # noqa: E402

TIER_CHOICES = ("",) + em_config.TIERS
SEARCH_PROVIDERS = ("tavily", "brave", "exa", "other")
ENDPOINT_HINTS = ("http://localhost:4000", "http://localhost:3456", "https://api.anthropic.com")


class ModelRow:
    def __init__(self, parent, name="", tier="", label="", use=True, on_remove=None):
        self.use = tk.BooleanVar(value=use)
        self.name = tk.StringVar(value=name)
        self.tier = tk.StringVar(value=tier)
        self.label = tk.StringVar(value=label)
        self.widgets = (
            ttk.Checkbutton(parent, variable=self.use),
            ttk.Entry(parent, textvariable=self.name, width=40),
            ttk.Combobox(parent, textvariable=self.tier, values=TIER_CHOICES, width=9, state="readonly"),
            ttk.Entry(parent, textvariable=self.label, width=24),
            ttk.Button(parent, text="✕", width=3, command=lambda: on_remove(self)),
        )

    def grid(self, row):
        for col, w in enumerate(self.widgets):
            w.grid(row=row, column=col, padx=3, pady=1, sticky="w")

    def destroy(self):
        for w in self.widgets:
            w.destroy()

    def as_dict(self):
        return {"use": self.use.get(), "name": self.name.get(), "tier": self.tier.get(),
                "label": self.label.get()}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude Endpoint Mapper - Claude V100")
        self.geometry("860x760")
        self.rows = []
        self.has_saved_key = False
        self.events = queue.Queue()  # worker threads hand results to the UI thread through this
        self._build()
        self._prefill()
        self._update_search_controls()
        self.after(50, self._drain)

    # ---------------------------------------------------------------- layout --
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Endpoint").grid(row=0, column=0, sticky="w")
        self.url = tk.StringVar()
        ttk.Combobox(top, textvariable=self.url, values=ENDPOINT_HINTS).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(top, text="Auth").grid(row=0, column=2, sticky="w")
        self.auth = tk.StringVar(value="bearer")
        ttk.Combobox(top, textvariable=self.auth, values=("bearer", "x-api-key"), width=10,
                     state="readonly").grid(row=0, column=3, padx=6)
        ttk.Label(top, text="API key").grid(row=1, column=0, sticky="w")
        self.key = tk.StringVar()
        ttk.Entry(top, textvariable=self.key, show="•").grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        self.key_hint = ttk.Label(top, text="")
        self.key_hint.grid(row=2, column=1, sticky="w", padx=6)
        self.fetch_btn = ttk.Button(top, text="Fetch models", command=self._fetch)
        self.fetch_btn.grid(row=1, column=2, columnspan=2, sticky="ew", padx=6)

        self.switch_note = ttk.Label(self, text="", foreground="#b00020")
        self.switch_note.pack(fill="x", **pad)
        if not em_config.has_native_switch(os.environ.get("EM_ASAR") or em_config.SYSTEM_ASAR):
            self.switch_note.config(
                text="This Claude Desktop has no allowNonAnthropicModels switch (claude-desktop-extra); "
                     "Apply will refuse.")

        ttk.Label(self, wraplength=820, justify="left",
                  text="Models: pick a tier per model (blank = shows in the picker with no tier slot). "
                       "Friendly name is what the model picker displays.").pack(fill="x", **pad)
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, **pad)
        self.canvas = tk.Canvas(box, highlightthickness=0, height=240)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        for col, title in enumerate(("Use", "Model id", "Tier", "Friendly name")):
            ttk.Label(self.inner, text=title, font=("TkDefaultFont", 9, "bold")).grid(row=0, column=col, sticky="w", padx=3)
        ttk.Button(self, text="Add row", command=lambda: self._add_row()).pack(anchor="w", **pad)

        ws = ttk.LabelFrame(self, text="Web search (runs inside Claude, not the gateway)")
        ws.pack(fill="x", **pad)
        self.ws_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(ws, text="Enable built-in web search", variable=self.ws_on,
                        command=self._update_search_controls).grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.ws_provider = tk.StringVar(value="tavily")
        self.ws_provider_box = ttk.Combobox(ws, textvariable=self.ws_provider, values=SEARCH_PROVIDERS,
                                            width=10, state="readonly")
        self.ws_provider_box.grid(row=0, column=1, padx=6)
        self.ws_provider_box.bind("<<ComboboxSelected>>", lambda e: self._update_search_controls())
        ttk.Label(ws, text="Key (blank = keyless Tavily / keep stored)").grid(row=1, column=0, sticky="w", padx=6)
        self.ws_key = tk.StringVar()
        self.ws_key_entry = ttk.Entry(ws, textvariable=self.ws_key, show="•", width=36)
        self.ws_key_entry.grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=2)
        ttk.Label(ws, text="Search URL (other only)").grid(row=2, column=0, sticky="w", padx=6)
        self.ws_url = tk.StringVar()
        self.ws_url_entry = ttk.Entry(ws, textvariable=self.ws_url, width=48)
        self.ws_url_entry.grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=2)

        tgt = ttk.Frame(self)
        tgt.pack(fill="x", **pad)
        self.code = tk.BooleanVar(value=True)
        ttk.Checkbutton(tgt, text="Claude Code (~/.claude-v100/settings.json)", variable=self.code).pack(side="left")
        ttk.Label(tgt, text="Applying restarts Claude V100 only.").pack(side="left", padx=14)
        self.apply_btn = ttk.Button(self, text="Apply and restart Claude V100", command=self._apply)
        self.apply_btn.pack(**pad)

        self.log = tk.Text(self, height=9, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=False, **pad)
        self._log("Ready. Set an endpoint, fetch models, choose tiers / friendly names, then Apply.")

    # ----------------------------------------------------------------- state --
    def _post(self, fn):
        """Thread-safe: run fn on the UI thread (Tk is only touched from there)."""
        self.events.put(fn)

    def _drain(self):
        while True:
            try:
                fn = self.events.get_nowait()
            except queue.Empty:
                break
            fn()
        self.after(50, self._drain)

    def _log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _add_row(self, name="", tier="", label="", use=True):
        row = ModelRow(self.inner, name, tier, label, use, on_remove=self._remove_row)
        self.rows.append(row)
        row.grid(len(self.rows))
        return row

    def _remove_row(self, row):
        self.rows.remove(row)
        row.destroy()
        for i, r in enumerate(self.rows, start=1):
            r.grid(i)

    def _update_search_controls(self):
        on = self.ws_on.get()
        state = "readonly" if on else "disabled"
        self.ws_provider_box.config(state=state)
        self.ws_key_entry.config(state="normal" if on else "disabled")
        self.ws_url_entry.config(state="normal" if on and self.ws_provider.get() == "other" else "disabled")

    def _prefill(self):
        try:
            pre = em_config.read_prefill(em_config.userdata_3p())
        except ValueError as e:
            self._log(f"Existing config not read: {e}")
            return
        if not pre:
            return
        self.url.set(pre["base_url"])
        self.auth.set(pre["auth_scheme"])
        for m in pre["models"]:
            self._add_row(m["name"], m["tier"], m["label"])
        if pre["has_key"]:
            self.has_saved_key = True
            self.key_hint.config(text="A key is already saved. Leave blank to keep it.")
        ws = pre["web_search"]
        if ws:
            self.ws_on.set(True)
            if ws["provider"] in ("tavily", "brave", "exa"):
                self.ws_provider.set(ws["provider"])
            elif ws["provider"] == "custom":
                self.ws_provider.set("other")
                self.ws_url.set(ws["custom_url"])
        self._log("Loaded the existing Claude V100 config.")

    # --------------------------------------------------------------- actions --
    def _fetch(self):
        url, key, auth = self.url.get().strip(), self.key.get().strip(), self.auth.get()
        if not url:
            self._log("Enter the endpoint base URL first.")
            return
        self.fetch_btn.config(state="disabled")
        self._log(f"Fetching models from {url} ...")

        def work():
            try:
                ids = em_config.fetch_models(url, key, auth)
            except Exception as e:  # shown to the user, never contains the key
                message = str(e)  # `e` is unbound once the except block ends
                self._post(lambda: self._fetch_failed(message))
                return
            self._post(lambda: self._fetched(ids))

        threading.Thread(target=work, daemon=True).start()

    def _fetched(self, ids):
        old = {r.name.get(): r.as_dict() for r in self.rows}
        for r in list(self.rows):
            self._remove_row(r)
        for model_id in ids:
            prev = old.get(model_id)
            if prev:
                self._add_row(model_id, prev["tier"], prev["label"], prev["use"])
            else:
                tier = em_form.guess_tier(model_id)
                self._add_row(model_id, tier, tier.title())
        self.fetch_btn.config(state="normal")
        self._log(f"Found {len(ids)} models.")

    def _fetch_failed(self, message):
        self.fetch_btn.config(state="normal")
        self._log(f"Fetch failed: {message}")

    def _apply(self):
        try:
            argv, stdin = em_form.build_apply_request(
                self.url.get(), self.auth.get(), self.key.get(), [r.as_dict() for r in self.rows],
                self.code.get(), self.ws_on.get(), self.ws_provider.get(), self.ws_key.get(),
                self.ws_url.get())
        except ValueError as e:
            self._log(str(e))
            return
        if not self.key.get().strip() and not self.has_saved_key:
            self._log("Enter the API key (none is saved yet).")
            return
        self.apply_btn.config(state="disabled")
        self._log("Applying ...")
        cmd = [sys.executable, str(HERE / "endpoint_mapper.py"), *argv]

        def work():
            try:
                done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=180)
                text = (done.stdout + done.stderr).strip()
                ok = done.returncode == 0
            except Exception as e:
                text, ok = str(e), False
            self._post(lambda: self._applied(ok, text))

        threading.Thread(target=work, daemon=True).start()

    def _applied(self, ok, text):
        self.apply_btn.config(state="normal")
        self._log(text or "(no output)")
        if ok:
            self.has_saved_key = True
            self.key.set("")
            self.ws_key.set("")
            self.key_hint.config(text="A key is already saved. Leave blank to keep it.")
            self._log("Done. Claude V100 was restarted.")
        else:
            self._log("Apply failed; see above.")


def main():
    app = App()
    if "--selftest" in sys.argv:
        app.update()
        print("gui ok")
        app.destroy()
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
