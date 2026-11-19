"""デッキ画像（ゲーム内のセーブデータ画面、またはエクスポート画像）からカードを切り出し、登録済みカードと照合するモジュール。

必要なもの: Pillow（pip install pillow）
カードのデータは、このファイルと同じフォルダの card_db/ に保存されます。
    card_db/cards.json   登録したカードの情報
    card_db/<id>.png     カード画像（照合用）
"""
import json
import os

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(HERE, "card_db")

# カードの大きさは、画像の「幅」に比例します（1920pxの幅で 184x276px）。
# ゲーム内のデッキ画面でも、エクスポート画像（カード枚数に応じて縦に長くなる）でも同じです。
CARD_W, CARD_H = 184 / 1920, 276 / 1920
CANON = (184, 276)  # 照合用に正規化したカード画像の大きさ
BG_THR = 205        # この明るさ未満を「カード（背景より暗い部分）」とみなす
MATCH_MAX = 0.04    # この距離以下なら同じカードとみなす（同じカードは約0.002〜0.015、別カードは0.13以上だった）
SUGGEST_MAX = 0.10  # 未登録でも、この距離以下なら「近い登録カード」として候補に出す
WORK_W = 480        # 検出に使う縮小画像の幅


# ---------------------------------------------------------------- 切り出し
def _components(gray_small):
    """縮小画像の暗い部分を、つながり（連結成分）ごとの外接矩形にして返す。"""
    w, h = gray_small.size
    px = gray_small.load()
    seen = [[False] * w for _ in range(h)]
    out = []
    for y0 in range(h):
        for x0 in range(w):
            if seen[y0][x0] or px[x0, y0] >= BG_THR:
                continue
            stack = [(x0, y0)]
            seen[y0][x0] = True
            minx = maxx = x0
            miny = maxy = y0
            while stack:
                x, y = stack.pop()
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and px[nx, ny] < BG_THR:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            out.append((minx, miny, maxx + 1, maxy + 1))
    return out


def _refine(gray, box, f):
    """粗く求めた枠を、元の解像度で補正する（縮小率 f の分だけ前後を探す）。"""
    W, H = gray.size
    px = gray.load()
    x0, y0, x1, y1 = box
    m = int(f) + 2

    def col(x, ya, yb):
        x = min(max(x, 0), W - 1)
        return sum(1 for y in range(ya, yb) if px[x, y] < BG_THR) / max(1, yb - ya)

    def row(y, xa, xb):
        y = min(max(y, 0), H - 1)
        return sum(1 for x in range(xa, xb) if px[x, y] < BG_THR) / max(1, xb - xa)

    ya, yb = y0 + (y1 - y0) // 10, y1 - (y1 - y0) // 10
    xa, xb = x0 + (x1 - x0) // 10, x1 - (x1 - x0) // 10
    nx0 = next((x for x in range(x0 - m, x0 + m + 1) if col(x, ya, yb) >= 0.6), x0)
    nx1 = next((x for x in range(x1 + m, x1 - m - 1, -1) if col(x, ya, yb) >= 0.6), x1 - 1) + 1
    ny0 = next((y for y in range(y0 - m, y0 + m + 1) if row(y, xa, xb) >= 0.6), y0)
    ny1 = next((y for y in range(y1 + m, y1 - m - 1, -1) if row(y, xa, xb) >= 0.6), y1 - 1) + 1
    return nx0, ny0, nx1, ny1


def detect_cards(img):
    """画像の中から、カード1枚ごとの枠 (x0, y0, x1, y1) を、左上から順（行ごとに左から右）に返す。
    カードの大きさ（画像の幅に比例）に合う、背景より暗いまとまりをカードとみなします。"""
    W, H = img.size
    gray = img.convert("L")
    small = gray.resize((WORK_W, max(1, round(H * WORK_W / W))), Image.BILINEAR)
    f = W / WORK_W
    ew, eh = CARD_W * WORK_W, CARD_H * WORK_W
    coarse = [c for c in _components(small)
              if 0.8 * ew <= c[2] - c[0] <= 1.2 * ew and 0.8 * eh <= c[3] - c[1] <= 1.2 * eh]
    boxes = [_refine(gray, (round(c[0] * f), round(c[1] * f), round(c[2] * f), round(c[3] * f)), f) for c in coarse]
    # 行ごとにまとめて、左から右へ並べる
    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)
    rows, tol = [], eh * f * 0.5
    for b in boxes:
        cy = (b[1] + b[3]) / 2
        if rows and abs(cy - rows[-1][0]) < tol:
            rows[-1][1].append(b)
        else:
            rows.append([cy, [b]])
    return [b for _, r in rows for b in sorted(r, key=lambda b: b[0])]


def canonical(img, box):
    return img.crop(box).convert("RGB").resize(CANON, Image.LANCZOS)


# ---------------------------------------------------------------- 照合用の特徴
def signature(card_img):
    """カード全体・効果文の領域・上端（名前とコスト）の3つの縮小画像を、照合用の特徴にする。"""
    blur = card_img.filter(ImageFilter.GaussianBlur(1.0))
    w, h = CANON
    g = blur.convert("L")
    whole = g.resize((40, 60), Image.LANCZOS).tobytes()
    body = g.crop((int(w * 0.06), int(h * 0.50), int(w * 0.94), int(h * 0.97))).resize((64, 40), Image.LANCZOS).tobytes()
    head = g.crop((0, 0, w, int(h * 0.12))).resize((64, 12), Image.LANCZOS).tobytes()
    return whole, body, head


def _mad(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) * 255)


def distance(sa, sb):
    return 0.3 * _mad(sa[0], sb[0]) + 0.4 * _mad(sa[1], sb[1]) + 0.3 * _mad(sa[2], sb[2])


# ---------------------------------------------------------------- データベース
class CardDB:
    def __init__(self, directory=DB_DIR):
        self.dir = directory
        self.entries = []   # カード情報のdictのリスト
        self._sig = {}      # id -> 特徴
        self._load()

    def _load(self):
        path = os.path.join(self.dir, "cards.json")
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            self.entries = json.load(f).get("cards", [])
        for e in self.entries:
            img_path = os.path.join(self.dir, e["image"])
            if os.path.exists(img_path):
                self._sig[e["id"]] = signature(Image.open(img_path).convert("RGB"))

    def _save(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "cards": self.entries}, f, ensure_ascii=False, indent=2)

    def nearest(self, card_img):
        """最も近い登録カードと距離を返す（登録が無ければ (None, 1.0)）。"""
        sig = signature(card_img)
        best, best_d = None, 1.0
        for e in self.entries:
            s = self._sig.get(e["id"])
            if s is None:
                continue
            d = distance(sig, s)
            if d < best_d:
                best, best_d = e, d
        return best, best_d

    def match(self, card_img, max_dist=MATCH_MAX):
        """同じカードとみなせる登録カードを返す。無ければ (None, 最も近い距離)。"""
        best, best_d = self.nearest(card_img)
        return (best if best_d <= max_dist else None), best_d

    def add(self, card_img, info):
        """カード画像と情報（name, cost, costX, draw, ap, keywords, effects）を登録する。"""
        os.makedirs(self.dir, exist_ok=True)
        n = 1 + max([int(e["id"][1:]) for e in self.entries if e["id"][1:].isdigit()] + [0])
        entry = {"id": f"c{n:04d}", "image": f"c{n:04d}.png"}
        entry.update(info)
        card_img.save(os.path.join(self.dir, entry["image"]))
        self._sig[entry["id"]] = signature(card_img)
        self.entries.append(entry)
        self._save()
        return entry

    def remove(self, entry_id):
        self.entries = [e for e in self.entries if e["id"] != entry_id]
        self._sig.pop(entry_id, None)
        self._save()


def to_card(entry, count=1):
    """登録情報を、計算機のカード（JSON）に変換する。"""
    card = {"name": entry["name"], "cost": entry.get("cost", 0)}
    for k in ("costX", "draw", "ap", "keywords", "effects"):
        if entry.get(k):
            card[k] = entry[k]
    if count != 1:
        card["count"] = count
    return card
