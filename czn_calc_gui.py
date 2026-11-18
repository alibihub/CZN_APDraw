#!/usr/bin/env python3
"""カオゼロ デッキ使い切り計算機（GUI版 / tkinter）

czn_calc.py と同じフォルダに置いて実行します。標準ライブラリのみで動作します。
    python czn_calc_gui.py
"""
import json
import os
import random
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import czn_calc as cz

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DECK = os.path.join(HERE, "czn-deck.json")
PARAM_LABELS = [
    ("ap", "ターン開始時のAP"),
    ("hand", "開始時の手札枚数（開戦を含む）"),
    ("limit", "手札上限"),
    ("trials", "試行回数"),
    ("target", "目標成功率(%)"),
]


def cards_text(cards):
    return "[\n" + ",\n".join(json.dumps(c, ensure_ascii=False) for c in cards) + "\n]"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("カオゼロ デッキ使い切り計算機")
        self.geometry("1040x660")
        self.minsize(820, 520)
        self._job = None

        self.vars = {k: tk.StringVar(value=str(v)) for k, v in cz.DEFAULTS.items()}
        self._build_ui()
        if os.path.exists(DEFAULT_DECK):
            self._load_file(DEFAULT_DECK)

    # ---- UI ----
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=5)
        root.columnconfigure(1, weight=4)
        root.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(root, text="前提とカード", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(1, weight=1)
        left.rowconfigure(len(PARAM_LABELS) + 1, weight=1)

        for i, (key, label) in enumerate(PARAM_LABELS):
            ttk.Label(left, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(left, textvariable=self.vars[key], width=10).grid(row=i, column=1, sticky="w", pady=2)

        ttk.Label(left, text="カード一覧（JSON）").grid(row=len(PARAM_LABELS), column=0, columnspan=2, sticky="w", pady=(10, 2))
        self.cards = scrolledtext.ScrolledText(left, wrap="none", font=("Consolas", 10), undo=True, height=16)
        self.cards.grid(row=len(PARAM_LABELS) + 1, column=0, columnspan=2, sticky="nsew")

        bar = ttk.Frame(left)
        bar.grid(row=len(PARAM_LABELS) + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.run_btn = ttk.Button(bar, text="計算する", command=self.calculate)
        self.run_btn.pack(side="left")
        ttk.Button(bar, text="JSONを開く", command=self.open_file).pack(side="left", padx=6)
        ttk.Button(bar, text="JSONを保存", command=self.save_file).pack(side="left")

        right = ttk.LabelFrame(root, text="結果", padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.out = scrolledtext.ScrolledText(right, wrap="word", font=("Meiryo UI", 10), state="disabled")
        self.out.grid(row=0, column=0, sticky="nsew")
        self.status = ttk.Label(right, text="", foreground="#666")
        self.status.grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _set_out(self, text):
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self.out.configure(state="disabled")

    # ---- ファイル ----
    def _load_file(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            cards = data if isinstance(data, list) else data.get("cards")
            if not isinstance(cards, list):
                raise ValueError("カードの配列がありません")
            if isinstance(data, dict):
                for k, v in data.get("params", {}).items():
                    if k in self.vars:
                        self.vars[k].set(str(v))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("読み込めません", str(e))
            return
        self.cards.delete("1.0", "end")
        self.cards.insert("1.0", cards_text(cards))
        self.status.config(text=f"読み込み: {os.path.basename(path)}")

    def open_file(self):
        path = filedialog.askopenfilename(initialdir=HERE, filetypes=[("JSON", "*.json"), ("すべて", "*.*")])
        if path:
            self._load_file(path)

    def save_file(self):
        try:
            cards = self._read_cards()
        except ValueError as e:
            messagebox.showerror("保存できません", str(e))
            return
        path = filedialog.asksaveasfilename(initialdir=HERE, initialfile="czn-deck.json", defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        doc = {"version": 1, "params": {k: v.get() for k, v in self.vars.items()}, "cards": cards}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        self.status.config(text=f"保存: {os.path.basename(path)}")

    # ---- 計算 ----
    def _read_cards(self):
        try:
            data = json.loads(self.cards.get("1.0", "end"))
        except json.JSONDecodeError as e:
            raise ValueError(f"JSONを読み取れません: {e}") from e
        cards = data if isinstance(data, list) else data.get("cards")
        if not isinstance(cards, list):
            raise ValueError("カードの配列がありません")
        return cards

    def calculate(self):
        if self._job is not None:
            return
        try:
            deck, openers, warns = cz.build(self._read_cards())
            if not deck and not openers:
                raise ValueError("カードが1枚もありません")
            p = cz.prepare_params({}, {k: float(v.get()) if k == "target" else int(v.get())
                                       for k, v in self.vars.items()})
        except ValueError as e:
            messagebox.showerror("入力を確認してください", str(e))
            return

        self.run_btn.state(["disabled"])
        self.status.config(text="計算中…")
        self._set_out("")
        box = {}

        def work():
            try:
                box["text"] = cz.report(p, openers, cz.analyze(deck, openers, p, random.Random()), warns)
            except Exception as e:  # noqa: BLE001
                box["error"] = str(e)

        self._job = threading.Thread(target=work, daemon=True)
        self._job.start()
        self.after(100, lambda: self._poll(box))

    def _poll(self, box):
        if self._job.is_alive():
            self.after(100, lambda: self._poll(box))
            return
        self._job = None
        self.run_btn.state(["!disabled"])
        if "error" in box:
            self.status.config(text="")
            messagebox.showerror("計算に失敗しました", box["error"])
        else:
            self._set_out(box["text"])
            self.status.config(text="完了")


if __name__ == "__main__":
    App().mainloop()
