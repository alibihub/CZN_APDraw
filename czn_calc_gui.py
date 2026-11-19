#!/usr/bin/env python3
"""カオゼロ デッキ使い切り計算機（GUI版 / tkinter）

czn_calc.py と同じフォルダに置いて実行します。計算だけなら標準ライブラリのみで動作します。
「画像から追加」を使うには Pillow が必要です（pip install pillow）。
    python czn_calc_gui.py
"""
import json
import os
import random
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import czn_calc as cz

try:  # 画像からの読み取りには Pillow が必要（pip install pillow）
    from PIL import ImageTk
    from PIL import Image
    import czn_cards as cc
    HAVE_CARDS = True
except ImportError:
    HAVE_CARDS = False

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


class RegisterDialog(tk.Toplevel):
    """未登録のカードの情報を入力するダイアログ。
    result は ("register", info) / ("same", entry) / ("skip",) / ("abort",) のどれか。"""

    def __init__(self, parent, tile, near, near_dist, index, total):
        super().__init__(parent)
        self.title(f"未登録のカード（{index}/{total}枚目）")
        self.transient(parent)
        self.result = ("abort",)
        self._photo = ImageTk.PhotoImage(tile)

        frm = ttk.Frame(self, padding=10)
        frm.pack()
        ttk.Label(frm, image=self._photo).grid(row=0, column=0, rowspan=8, padx=(0, 12))
        self.v = {k: tk.StringVar(value=d) for k, d in (("name", ""), ("cost", "0"), ("draw", "0"), ("ap", "0"))}
        for i, (k, label) in enumerate((("name", "名前"), ("cost", "コスト（Xは X と入力）"),
                                        ("draw", "ドロー枚数"), ("ap", "AP獲得"))):
            ttk.Label(frm, text=label).grid(row=i, column=1, sticky="w", pady=2)
            ttk.Entry(frm, textvariable=self.v[k], width=24).grid(row=i, column=2, sticky="w", pady=2)
        self.kw_open, self.kw_start = tk.BooleanVar(), tk.BooleanVar()
        kf = ttk.Frame(frm)
        kf.grid(row=4, column=1, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(kf, text="開幕", variable=self.kw_open).pack(side="left")
        ttk.Checkbutton(kf, text="開戦", variable=self.kw_start).pack(side="left", padx=8)
        ttk.Label(frm, text="条件付き効果（JSONの配列・省略可）").grid(row=5, column=1, columnspan=2, sticky="w")
        self.effects = ttk.Entry(frm, width=40)
        self.effects.grid(row=6, column=1, columnspan=2, sticky="we")

        bar = ttk.Frame(frm)
        bar.grid(row=8, column=0, columnspan=3, sticky="we", pady=(12, 0))
        ttk.Button(bar, text="登録", command=self._on_register).pack(side="left")
        if near is not None and near_dist <= cc.SUGGEST_MAX:
            ttk.Button(bar, text=f"「{near['name']}」と同じ（距離 {near_dist * 100:.1f}）",
                       command=lambda: self._finish(("same", near))).pack(side="left", padx=6)
        ttk.Button(bar, text="スキップ", command=lambda: self._finish(("skip",))).pack(side="left", padx=6)
        ttk.Button(bar, text="中止", command=lambda: self._finish(("abort",))).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", lambda: self._finish(("abort",)))
        self.grab_set()
        self.wait_window(self)

    def _on_register(self):
        name = self.v["name"].get().strip()
        if not name:
            messagebox.showerror("入力を確認してください", "名前を入力してください。", parent=self)
            return
        try:
            cost_s = self.v["cost"].get().strip()
            cost_x = cost_s.upper() == "X"
            cost = 0 if cost_x else int(cost_s)
            draw = int(self.v["draw"].get() or 0)
            ap = int(self.v["ap"].get() or 0)
            eff = self.effects.get().strip()
            effects = json.loads(eff) if eff else []
            if not isinstance(effects, list):
                raise ValueError("条件付き効果は、JSONの配列で入力してください")
        except ValueError as e:
            messagebox.showerror("入力を確認してください", str(e), parent=self)
            return
        info = {"name": name, "cost": cost}
        if cost_x:
            info["costX"] = True
        if draw:
            info["draw"] = draw
        if ap:
            info["ap"] = ap
        kws = [k for k, v in (("開幕", self.kw_open), ("開戦", self.kw_start)) if v.get()]
        if kws:
            info["keywords"] = kws
        if effects:
            info["effects"] = effects
        self._finish(("register", info))

    def _finish(self, result):
        self.result = result
        self.destroy()


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
        ttk.Button(bar, text="画像から追加", command=self.import_images).pack(side="left", padx=6)

        right = ttk.LabelFrame(root, text="結果", padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.out = scrolledtext.ScrolledText(right, wrap="word", font=("Meiryo UI", 10), state="disabled")
        self.out.grid(row=0, column=0, sticky="nsew")
        self.status = ttk.Label(right, text="", foreground="#666", wraplength=420, justify="left")
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

    # ---- 画像からの読み取り ----
    def import_images(self):
        if not HAVE_CARDS:
            messagebox.showerror("Pillowが必要です", "ターミナルで  pip install pillow  を実行してから、もう一度起動してください。")
            return
        paths = filedialog.askopenfilenames(
            title="デッキ画面のスクリーンショットを選択",
            filetypes=[("画像", "*.png *.jpg *.jpeg *.webp *.bmp"), ("すべて", "*.*")])
        if not paths:
            return
        try:
            current = self._read_cards()
        except ValueError as e:
            messagebox.showerror("追加できません", str(e))
            return

        tiles, nodeck = [], []
        for path in paths:
            try:
                img = Image.open(path).convert("RGB")
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("画像を開けません", f"{os.path.basename(path)}: {e}")
                return
            boxes = cc.detect_cards(img)
            if boxes:
                tiles += [cc.canonical(img, b) for b in boxes]
            else:
                nodeck.append(os.path.basename(path))

        db = cc.CardDB()
        found = {}  # 登録カードのid -> [登録情報, 枚数]
        registered = skipped = 0
        for i, tile in enumerate(tiles, 1):
            self.status.config(text=f"照合中… {i}/{len(tiles)}")
            self.update_idletasks()
            entry, _ = db.match(tile)
            if entry is None:
                near, near_dist = db.nearest(tile)
                result = RegisterDialog(self, tile, near, near_dist, i, len(tiles)).result
                if result[0] == "abort":
                    self.status.config(text="中止しました（カード一覧は変更していません）")
                    return
                if result[0] == "skip":
                    skipped += 1
                    continue
                if result[0] == "same":
                    entry = result[1]
                else:
                    entry = db.add(tile, result[1])
                    registered += 1
            found.setdefault(entry["id"], [entry, 0])[1] += 1

        new_cards = [cc.to_card(e, n) for e, n in found.values()]
        self.cards.delete("1.0", "end")
        self.cards.insert("1.0", cards_text(current + new_cards))
        total = sum(n for _, n in found.values())
        msg = f"{total}枚（{len(found)}種類）を追加しました。"
        if registered:
            msg += f" 新規登録 {registered}種類。"
        if skipped:
            msg += f" スキップ {skipped}枚。"
        if nodeck:
            msg += f" カードを検出できなかった画像: {', '.join(nodeck)}"
        self.status.config(text=msg)

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
