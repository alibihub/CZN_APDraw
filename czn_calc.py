#!/usr/bin/env python3
"""カオゼロ デッキ使い切り計算機（Python版）

デッキ全体を1ターンで使い切れる確率と、使い切りに必要な追加AP・追加ドロー枚数を、
モンテカルロ法で見積もります。標準ライブラリのみで動作します。

使い方:
    python czn_calc.py            （同じフォルダの czn-deck.json を使う）
    python czn_calc.py deck.json
    python czn_calc.py deck.json --ap 3 --hand 5 --limit 10 --trials 5000 --target 90

deck.json は Web版の「JSONを保存」で書き出した形式（{"params":{...},"cards":[...]}）か、
カード配列だけのJSONです。コマンドライン引数は、ファイル内の params より優先されます。
"""
import argparse
import json
import os
import random
import sys

KNOWN_WHEN = {"always", "handOnlyThis", "handAtLeast", "apAtLeast"}
DEFAULTS = {"ap": 3, "hand": 5, "limit": 10, "trials": 2000, "target": 90}


def check(effect, hand_len, ap):
    w = effect.get("when", "always")
    if w == "always":
        return True
    if w == "handOnlyThis":
        return hand_len == 1
    if w == "handAtLeast":
        return hand_len >= effect.get("n", 0)
    if w == "apAtLeast":
        return ap >= effect.get("n", 0)
    return False


def build(raw):
    """カードの配列（dictのリスト）を、デッキ・開幕カード・警告に変換する。"""
    if not isinstance(raw, list):
        raise ValueError("カードの配列がありません")
    deck, openers, warns = [], [], set()
    for c in raw:
        kws = c.get("keywords", [])
        card = {
            "name": str(c.get("name", "?")),
            "cost": c.get("cost", 0) or 0,
            "costX": bool(c.get("costX", False)),
            "draw": c.get("draw", 0) or 0,
            "ap": c.get("ap", 0) or 0,
            "effects": c.get("effects", []),
            "opener": "開幕" in kws,
            "starter": "開戦" in kws,
        }
        for k in kws:
            if k not in ("開幕", "開戦"):
                warns.add(f"キーワード「{k}」は未対応のため無視")
        for e in card["effects"]:
            if e.get("when", "always") not in KNOWN_WHEN:
                warns.add(f"条件「{e['when']}」は未対応のため無効")
        count = c.get("count", 1)
        count = 1 if count is None else int(count)
        (openers if card["opener"] else deck).extend([card] * count)
    return deck, openers, sorted(warns)


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data if isinstance(data, list) else data.get("cards")
    params = {} if isinstance(data, list) else data.get("params", {})
    try:
        deck, openers, warns = build(raw)
    except ValueError as e:
        sys.exit(f"エラー: {e}")
    return deck, openers, params, warns


def effective(card, hand_len, ap):
    cost, draw, gain = card["cost"], card["draw"], card["ap"]
    for e in card["effects"]:
        if check(e, hand_len, ap):
            cost += e.get("costDelta", 0)
            draw += e.get("draw", 0)
            gain += e.get("ap", 0)
    cost = ap if card["costX"] else max(0, cost)
    return cost, draw, gain


def simulate(deck, openers, p, x_ap, x_draw, rng):
    """1回分。戻り値は (使い切れたか, Xカード使用時のAPの合計)。
    優先順位: AP収支がプラス → ドローが多い → AP収支が良い → 低コスト（Xカードは最後）"""
    stock = [c for c in deck if not c["starter"]]
    rng.shuffle(stock)
    hand = [c for c in deck if c["starter"]]

    def draw(n):
        while n > 0 and stock and len(hand) < p["limit"]:
            hand.append(stock.pop())
            n -= 1

    ap = p["ap"] + x_ap
    open_draw = 0
    for c in openers:
        _, d, g = effective(c, p["hand"], ap)
        ap += g
        open_draw += d
    draw(max(0, p["hand"] + x_draw + open_draw - len(hand)))

    x_sum = 0
    while hand:
        best = None
        for i, c in enumerate(hand):
            cost, d, g = effective(c, len(hand), ap)
            if cost > ap:
                continue
            key = (2 if c["costX"] else (0 if g >= cost else 1), -d, -(g - cost), cost)
            if best is None or key < best[0]:
                best = (key, i, cost, d, g)
        if best is None:
            break
        _, i, cost, d, g = best
        card = hand.pop(i)
        if card["costX"]:
            x_sum += ap
        ap += g - cost
        draw(d)
    return (not stock and not hand), x_sum


def rate(deck, openers, p, x_ap, x_draw, rng):
    ok = x_total = 0
    for _ in range(p["trials"]):
        success, xs = simulate(deck, openers, p, x_ap, x_draw, rng)
        if success:
            ok += 1
            x_total += xs
    return ok / p["trials"], (x_total / ok if ok else 0.0)


def find_min(deck, openers, p, kind, rng):
    for k in range(31):
        r, _ = rate(deck, openers, p, k if kind == "ap" else 0, k if kind == "draw" else 0, rng)
        if r >= p["target"]:
            return k
    return None


def prepare_params(file_params, overrides):
    """前提の数値を整える。target は % で渡し、内部では 0〜1 にする。"""
    p = dict(DEFAULTS)
    p.update({k: v for k, v in (file_params or {}).items() if k in DEFAULTS and v is not None})
    p.update({k: v for k, v in (overrides or {}).items() if k in DEFAULTS and v is not None})
    p["trials"] = max(100, int(p["trials"]))
    p["target"] = float(p["target"]) / 100
    return p


def analyze(deck, openers, p, rng):
    base, avg_x = rate(deck, openers, p, 0, 0, rng)
    return {
        "n": len(deck),
        "cost": sum(c["cost"] for c in deck),
        "gain": sum(c["ap"] for c in deck),
        "draws": sum(c["draw"] for c in deck),
        "has_x": any(c["costX"] for c in deck),
        "base": base,
        "avg_x": avg_x,
        "xa": find_min(deck, openers, p, "ap", rng),
        "xd": find_min(deck, openers, p, "draw", rng),
    }


def report(p, openers, r, warns):
    fmt = lambda v: "30超" if v is None else str(v)
    need_ap = max(0, r["cost"] - p["ap"] - r["gain"])
    need_draw = max(0, r["n"] - p["hand"] - r["draws"])
    lines = [
        f"前提: AP {p['ap']} / 開始時の手札 {p['hand']} / 手札上限 {p['limit']} / 試行 {p['trials']}",
        f"デッキ {r['n']}枚（開幕 {len(openers)}枚は除く）  総コスト {r['cost']}  AP獲得 {r['gain']}  ドロー {r['draws']}",
        "-" * 50,
        f"追加なしの使い切り成功率      : {r['base'] * 100:.1f}%",
        f"必要な追加AP（成功率{p['target'] * 100:.0f}%以上）: {fmt(r['xa'])}",
        f"必要な追加ドロー              : {fmt(r['xd'])}",
    ]
    if r["has_x"]:
        lines.append(f"Xカード使用時の残りAP(平均)   : {r['avg_x']:.2f}")
    lines.append(f"理論下限  APの不足 {need_ap} / ドローの不足 {need_draw}（条件付き効果・並び順・手札上限は無視）")
    lines += [f"警告: {w}" for w in warns]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck", nargs="?", help="デッキのJSONファイル（省略時はこのスクリプトと同じフォルダの czn-deck.json）")
    ap.add_argument("--ap", type=int, help="ターン開始時のAP")
    ap.add_argument("--hand", type=int, help="開始時の手札枚数（開戦カードを含む）")
    ap.add_argument("--limit", type=int, help="手札上限")
    ap.add_argument("--trials", type=int, help="試行回数")
    ap.add_argument("--target", type=float, help="目標成功率(%%)")
    ap.add_argument("--seed", type=int, help="乱数の種（再現用）")
    a = ap.parse_args()
    if a.deck is None:
        a.deck = os.path.join(os.path.dirname(os.path.abspath(__file__)), "czn-deck.json")
    if not os.path.exists(a.deck):
        sys.exit(f"エラー: デッキのファイルが見つかりません: {a.deck}")

    deck, openers, file_params, warns = load(a.deck)
    if not deck and not openers:
        sys.exit("エラー: カードが1枚もありません")
    p = prepare_params(file_params, {k: getattr(a, k) for k in DEFAULTS})
    print(report(p, openers, analyze(deck, openers, p, random.Random(a.seed)), warns))


if __name__ == "__main__":
    main()
