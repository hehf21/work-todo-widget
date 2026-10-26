# -*- coding: utf-8 -*-
"""任务挂件 v1.3 —— 桌面小挂件：我的工作交办 / 完成情况 / 到期提醒 / 自建待办
数据源：<BASE>\\台账.json（工具\\台账引擎.py 生成）；BASE 默认取脚本上一级目录，可用环境变量 GJZB_HOME 覆盖。
自建待办写在 手工待办.json，和微信群交办合并显示；重新抓取不会覆盖。
完成状态、备注写在 完成状态.json（按 id）；外观（背景色/透明度）写在 挂件配置.json。
"""
import os, sys, json, re, uuid, datetime, threading, subprocess, winsound
import tkinter as tk
from tkinter import font as tkfont

_HERE  = os.path.dirname(os.path.abspath(__file__))
BASE   = os.getenv('GJZB_HOME') or os.path.dirname(_HERE)      # 默认取脚本上一级目录
LEDGER = os.path.join(BASE, '台账.json')
DONE   = os.path.join(BASE, '完成状态.json')
MANUAL = os.path.join(BASE, '手工待办.json')
ENGINE = os.path.join(BASE, '工具', '台账引擎.py')
CFG    = os.path.join(BASE, '挂件配置.json')
PY     = os.path.join(BASE, 'venv', 'Scripts', 'python.exe')
if not os.path.exists(PY):
    PY = sys.executable
MANUAL_GROUP = '手动添加'
ORDER = {'overdue': 0, 'today': 1, 'new': 2, 'open': 3, 'done': 4}

# ---------------- 设计 token ----------------
DEFAULT_BG = '#0F141B'
BG = DEFAULT_BG
CARD, CARD2, CARD_HOVER, HOVER, LINE = '#171E26', '#1E2732', '#1A222B', '#26313D', '#2A3541'
TXT, SUB, MUT = '#E8EEF6', '#93A1B2', '#66737F'
BLUE, RED, AMBER, GREEN = '#4C8DFF', '#FF6B66', '#F5A524', '#3FB950'
W, H, PAD, GAP = 460, 664, 16, 8
COL_W = 320                     # 收起状态（小条）宽度
FONT = 'Microsoft YaHei UI'
PRIO_COLOR = {'overdue': RED, 'today': AMBER, 'new': BLUE, 'open': MUT, 'done': GREEN}
THEME_DELTA = ((8, 10, 11), (15, 19, 23), (23, 29, 34), (27, 33, 38))   # BG→CARD/CARD2/HOVER/LINE
HOVER_DELTA = (11, 14, 16)      # BG→CARD_HOVER
BG_PRESETS = [('深夜黑', '#0F141B'), ('石墨灰', '#14181D'), ('藏青', '#0D1626'), ('墨绿', '#0A1A14')]


def rr_pts(x0, y0, x1, y1, r):
    """圆角矩形控制点（配合 create_polygon(smooth=True) 画圆角）"""
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


def _hex2rgb(s):
    s = str(s or '').strip().lstrip('#')
    if len(s) == 3: s = ''.join(c * 2 for c in s)
    if len(s) != 6: raise ValueError(s)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def apply_theme(bg_hex):
    """背景色一改，卡片/悬停/分割线按固定色阶一起推导，保证对比度"""
    global BG, CARD, CARD2, CARD_HOVER, HOVER, LINE
    try: r, g, b = _hex2rgb(bg_hex)
    except Exception: r, g, b = _hex2rgb(DEFAULT_BG)

    def mix(d):
        return '#%02X%02X%02X' % (min(r + d[0], 255), min(g + d[1], 255), min(b + d[2], 255))
    BG = '#%02X%02X%02X' % (r, g, b)
    CARD, CARD2, HOVER, LINE = [mix(d) for d in THEME_DELTA]
    CARD_HOVER = mix(HOVER_DELTA)
    return BG


def parse_dt(s):
    try: return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M')
    except Exception: return None


def rel_deadline(s):
    dl = parse_dt(s)
    if not dl: return ''
    now = datetime.datetime.now()
    d = (dl - now).total_seconds()
    if d < 0:
        h = int(-d // 3600)
        return f'逾期 {h} 小时' if h < 48 else f'逾期 {h // 24} 天'
    if d < 3600: return f'{int(d // 60)} 分钟后到期'
    if dl.date() == now.date(): return f'今天 {dl:%H:%M}'
    if (dl.date() - now.date()).days == 1: return f'明天 {dl:%H:%M}'
    return f'{dl:%m-%d %H:%M}'


def parse_when(s):
    """把用户手写的截止时间转成 'YYYY-MM-DD HH:MM'；空串=不设截止；看不懂返回 None。
    支持：2026-10-28 17:00 / 10-28 17:00 / 10月28日 17:00 / 明天下午5点 / 周五下班前 / 17:00
    """
    s = (s or '').strip().replace('：', ':')
    if not s: return ''
    now = datetime.datetime.now()

    def hm(seg, dh=17, dm=0):
        m = re.search(r'(\d{1,2})\s*[:点时]\s*(\d{1,2})?\s*(半)?', seg or '')
        if not m: return dh, dm
        h = int(m.group(1))
        mi = 30 if m.group(3) else int(m.group(2) or 0)
        if seg and any(k in seg for k in ('下午', '晚上', '中午')) and h < 12: h += 12
        return min(h, 23), min(mi, 59)

    def out(dt): return dt.strftime('%Y-%m-%d %H:%M')

    m = re.search(r'(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})', s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        h, mi = hm(s[m.end():])
        try: return out(datetime.datetime(y, mo, d, h, mi))
        except ValueError: return None
    m = re.search(r'(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?', s)
    if m and not re.search(r'\d{1,2}:\d{2}', s[:m.start()] or ''):
        mo, d = int(m.group(1)), int(m.group(2))
        h, mi = hm(s[m.end():])
        try: return out(datetime.datetime(now.year, mo, d, h, mi))
        except ValueError: return None
    m = re.search(r'(今天|明天|后天)', s)
    if m:
        day = now.date() + datetime.timedelta(days={'今天': 0, '明天': 1, '后天': 2}[m.group(1)])
        h, mi = hm(s[m.end():])
        return out(datetime.datetime.combine(day, datetime.time(h, mi)))
    m = re.search(r'(?:本周|这周|下周)?\s*(?:周|星期|礼拜)([一二三四五六日天])', s)
    if m:
        idx = '一二三四五六日天'.index(m.group(1)) + 1
        add = (idx - now.isoweekday()) % 7
        if '下周' in s: add += 7
        day = now.date() + datetime.timedelta(days=add)
        h, mi = hm(s[m.end():])
        return out(datetime.datetime.combine(day, datetime.time(h, mi)))
    if re.search(r'\d', s):
        h, mi = hm(s)
        return out(now.replace(hour=h, minute=mi, second=0, microsecond=0))
    return None


def make_manual(text, deadline, who='自建', tid=''):
    """造一条与台账同结构的自建待办"""
    now = datetime.datetime.now()
    return {
        'id': tid or ('m' + uuid.uuid4().hex[:11]),
        'group': MANUAL_GROUP, 'from': (who or '自建')[:20],
        'time': now.strftime('%Y-%m-%d %H:%M'), 'end_time': now.strftime('%Y-%m-%d %H:%M'),
        'text': text.strip()[:1200], 'deadline': deadline or '',
        'replied': '', 'rtext': '', 'ctx': [],
        'img': '', 'img_text': '', 'att': '', 'att_name': '',
        'done': False, 'done_at': '', 'note': '', 'prio': 'open',
        'age_days': 0, 'manual': True,
    }


class Widget:
    def __init__(self, root):
        self.root = root
        self.cfg = self.load_cfg()
        self.items, self.filter, self.expanded = [], 'open', set()
        today = datetime.date.today().isoformat()
        saved = self.load_cfg().get('reminded', {})
        self.reminded = {k for k, v in saved.items() if v == today}
        self.fetching, self.last_gen = False, ''
        self.banner_on = False
        self._seq, self._imgrefs, self._cw = 0, [], 0
        self.closed_days, self._grp_init = set(), False
        self.bg = apply_theme(self.cfg.get('bg', DEFAULT_BG))
        try: self.alpha = min(max(float(self.cfg.get('alpha', 1.0)), 0.35), 1.0)
        except Exception: self.alpha = 1.0

        root.overrideredirect(True)
        root.attributes('-topmost', bool(self.cfg.get('topmost', True)))
        root.geometry(f'{W}x{H}+{self.cfg.get("x", 1180)}+{self.cfg.get("y", 120)}')
        root.configure(bg=BG)
        try: root.attributes('-alpha', self.alpha)
        except Exception: pass

        self.f_title = tkfont.Font(family=FONT, size=11, weight='bold')
        self.f_body = tkfont.Font(family=FONT, size=10)
        self.f_small = tkfont.Font(family=FONT, size=9)

        self.build()
        self.reload()
        self.root.after(1500, self.tick)

    # ---------- 配置 ----------
    def load_cfg(self):
        try: return json.load(open(CFG, encoding='utf-8'))
        except Exception: return {}

    def save_cfg(self):
        self.cfg.update(x=self.root.winfo_x(), y=self.root.winfo_y(),
                        topmost=bool(self.root.attributes('-topmost')))
        try: json.dump(self.cfg, open(CFG, 'w', encoding='utf-8'), ensure_ascii=False)
        except Exception: pass

    # ---------- 骨架 ----------
    def build(self):
        r = self.root
        # 标题栏
        self.head = tk.Frame(r, bg=BG); self.head.pack(fill='x', padx=PAD, pady=(14, 0))
        left = tk.Frame(self.head, bg=BG); left.pack(side='left')
        self.lb_title = tk.Label(left, text='工作交办', font=self.f_title, fg=TXT, bg=BG)
        self.lb_title.pack(anchor='w')
        self.lb_date = tk.Label(left, text='', font=self.f_small, fg=MUT, bg=BG)
        self.lb_date.pack(anchor='w', pady=(3, 0))

        right = tk.Frame(self.head, bg=BG); right.pack(side='right', pady=(1, 0))
        self.btn_close = self.hdr_icon(right, '✕', self.quit)
        self.btn_min = self.hdr_icon(right, '－', self.collapse)
        self.btn_menu = self.hdr_icon(right, '⚙', self.menu)
        self.btn_refresh = self.hdr_icon(right, '⟳', self.do_refresh, AMBER)
        self.add_cv = tk.Canvas(right, width=104, height=28, bg=BG, highlightthickness=0, bd=0,
                                cursor='hand2')
        self.add_cv.pack(side='right', padx=(2, 10))
        self.add_cv.bind('<Button-1>', lambda e: self.todo_dialog())
        self.draw_add_btn()

        for w in (self.head, left, right, self.lb_title, self.lb_date):
            w.bind('<Button-1>', self.drag_start)
            w.bind('<B1-Motion>', self.drag_move)
            w.bind('<ButtonRelease-1>', self.head_click)

        # 分段筛选条（待办 / 今日 / 逾期 / 完成）
        self.stats = tk.Canvas(r, height=34, bg=BG, highlightthickness=0, bd=0)
        self.stats.pack(fill='x', padx=PAD, pady=(10, 0))
        self.stats.bind('<Button-1>', self.seg_click)
        self._seg_rects = []

        # 顶部提示条
        self.banner = tk.Canvas(r, height=34, bg=BG, highlightthickness=0, bd=0)
        self.banner.bind('<Button-1>', self.banner_click)

        # 列表
        self.wrap = tk.Frame(r, bg=BG); self.wrap.pack(fill='both', expand=True, padx=(PAD, 10), pady=(10, 0))
        self.canvas = tk.Canvas(self.wrap, bg=BG, highlightthickness=0, bd=0, cursor='hand2')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.sb = tk.Canvas(self.wrap, width=8, bg=BG, highlightthickness=0, bd=0)
        self.sb.bind('<Configure>', lambda e: self.draw_sb())
        self.sb.bind('<Button-1>', self.sb_jump)
        self.sb.bind('<B1-Motion>', self.sb_jump)
        self.canvas.configure(yscrollcommand=self.on_scroll)
        self.canvas.bind_all('<MouseWheel>', self.on_wheel)
        self.canvas.bind('<Configure>', self.on_canvas_resize)
        r.bind_all('<Control-n>', lambda e: self.todo_dialog())

        # 底栏
        self.foot = tk.Frame(r, bg=BG); self.foot.pack(fill='x', padx=PAD, pady=(10, 12))
        self.lb_foot = tk.Label(self.foot, text='', font=self.f_small, fg=MUT, bg=BG)
        self.lb_foot.pack(side='left')
        self.btn_filter = tk.Label(self.foot, text='显示全部', font=self.f_small, fg=SUB, bg=BG,
                                   cursor='hand2', padx=6, pady=2)
        self.btn_filter.pack(side='right')
        self.btn_filter.bind('<Button-1>', lambda e: self.toggle_filter())
        self.btn_filter.bind('<Enter>', lambda e: self.btn_filter.configure(fg=BLUE))
        self.btn_filter.bind('<Leave>', lambda e: self.btn_filter.configure(fg=SUB))

    def hdr_icon(self, parent, ch, cmd, hover_color=TXT):
        b = tk.Label(parent, text=ch, font=(FONT, 10), fg=SUB, bg=BG, cursor='hand2',
                     padx=6, pady=3)
        b.pack(side='right')
        b.bind('<Button-1>', lambda e: cmd())
        b.bind('<Enter>', lambda e, w=b, c=hover_color:
               w.configure(fg=c, bg=CARD))
        b.bind('<Leave>', lambda e, w=b: w.configure(fg=SUB, bg=BG))
        return b

    def draw_add_btn(self, compact=False):
        cv = self.add_cv
        cv.delete('all')
        w, h = (34, 28) if compact else (98, 28)
        cv.configure(width=w, height=h)
        rid = cv.create_polygon(rr_pts(1, 1, w - 1, h - 1, 14 if compact else 9),
                                smooth=True, fill=BLUE, outline='')
        txt = '＋' if compact else '＋ 新增'
        cv.create_text(w / 2, h / 2, text=txt, font=(FONT, 10),
                       fill='#FFFFFF')
        cv.bind('<Enter>', lambda e: cv.itemconfigure(rid, fill='#629BFF'))
        cv.bind('<Leave>', lambda e: cv.itemconfigure(rid, fill=BLUE))

    def mk_chip(self, parent, text, cmd, fg='#FFFFFF', bg=None, h=28, padx=14, font=None,
                hover=None, r=8):
        """给普通 Frame 弹窗用的圆角按钮（Canvas 实现）"""
        font = font or self.f_small
        bg = BLUE if bg is None else bg
        hov = hover or ('#629BFF' if bg == BLUE else HOVER)
        w = font.measure(text) + padx * 2
        cv = tk.Canvas(parent, width=w, height=h, bg=parent.cget('bg'),
                       highlightthickness=0, bd=0, cursor='hand2')
        rid = cv.create_polygon(rr_pts(0.5, 0.5, w - 0.5, h - 0.5, r), smooth=True,
                                fill=bg, outline='')
        cv.create_text(w / 2, h / 2, text=text, fill=fg, font=font)
        cv.bind('<Button-1>', lambda e: cmd())
        cv.bind('<Enter>', lambda e: cv.itemconfigure(rid, fill=hov))
        cv.bind('<Leave>', lambda e: cv.itemconfigure(rid, fill=bg))
        return cv

    # ---------- 分段筛选 ----------
    SEGS = [('open', '待办', BLUE), ('today', '今日', AMBER),
            ('overdue', '逾期', RED), ('done', '完成', GREEN)]

    def draw_segments(self, c):
        cv = self.stats
        cv.delete('all')
        w = cv.winfo_width()
        if w < 80: w = W - 2 * PAD
        h = 34
        cv.create_polygon(rr_pts(0, 2, w, h - 2, 10), smooth=True, fill=CARD, outline='')
        vals = {'open': len(self.items) - c['done'], 'today': c['today'],
                'overdue': c['overdue'], 'done': c['done']}
        seg_w = w / len(self.SEGS)
        self._seg_rects = []
        for i, (key, label, color) in enumerate(self.SEGS):
            x0 = i * seg_w
            active = (self.filter == key)
            if active:
                cv.create_polygon(rr_pts(x0 + 3, 5, x0 + seg_w - 3, h - 5, 8), smooth=True,
                                  fill=HOVER, outline='#3A4756')
            txt = f'{label} {vals.get(key, 0)}'
            tw = self.f_small.measure(txt)
            gx = x0 + seg_w / 2 - (tw + 12) / 2
            cv.create_oval(gx, h / 2 - 3, gx + 6, h / 2 + 3, fill=color, outline='')
            cv.create_text(gx + 12, h / 2, text=txt, font=self.f_small, anchor='w',
                           fill=(TXT if active else SUB))
            self._seg_rects.append((x0, x0 + seg_w, key))

    def seg_click(self, e):
        for x0, x1, key in getattr(self, '_seg_rects', []):
            if x0 <= e.x < x1:
                self.set_filter(key)
                return

    # ---------- 顶部提示条 ----------
    def draw_banner(self, text):
        cv = self.banner
        cv.delete('all')
        w = cv.winfo_width()
        if w < 80: w = W - 2 * PAD
        h = 34
        cv.create_polygon(rr_pts(0, 2, w, h - 2, 10), smooth=True, fill='#2B1D1E',
                          outline='#55302F')
        cv.create_text(12, h / 2, text=text, font=self.f_small, fill='#FFB7B2', anchor='w',
                       width=w - 44)
        cv.create_text(w - 13, h / 2, text='✕', font=self.f_small, fill='#B98D8A', anchor='e',
                       tags=('bannerx',))

    def banner_click(self, e):
        w = self.banner.winfo_width() or W - 2 * PAD
        if e.x > w - 30:
            self.hide_banner()

    def hide_banner(self):
        self.banner.pack_forget(); self.banner_on = False

    # ---------- 交互 ----------
    def drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._moved = 0

    def drag_move(self, e):
        self._moved = getattr(self, '_moved', 0) + 1
        self.root.geometry(f'+{e.x_root - self._dx}+{e.y_root - self._dy}')

    def head_click(self, e):
        # 收起状态下，点一下标题栏就展开
        if getattr(self, '_col', False) and getattr(self, '_moved', 0) < 3:
            self.collapse()

    def on_wheel(self, e):
        try: self.canvas.yview_scroll(int(-e.delta / 120), 'units')
        except Exception: pass

    def on_scroll(self, first, last):
        first, last = float(first), float(last)
        self._yview = (first, last)
        if first <= 0 and last >= 1:
            if self.sb.winfo_ismapped():
                self.sb.pack_forget()
            return
        if not self.sb.winfo_ismapped():
            self.sb.pack(side='right', fill='y', padx=(2, 0))
        self.draw_sb()

    def draw_sb(self):
        """自绘细滑条：6px 圆角，跟着滚动位置走"""
        cv = self.sb
        cv.delete('all')
        h = cv.winfo_height()
        if h < 24: return
        first, last = getattr(self, '_yview', (0.0, 1.0))
        top, bot = first * h + 4, last * h - 4
        if bot - top < 26:
            mid = (top + bot) / 2
            top, bot = mid - 13, mid + 13
        top, bot = max(top, 3), min(bot, h - 3)
        cv.create_polygon(rr_pts(2, top, 6, bot, 2), smooth=True, fill='#313D4B', outline='')

    def sb_jump(self, e):
        h = self.sb.winfo_height() or 1
        first, last = getattr(self, '_yview', (0.0, 1.0))
        span = last - first
        frac = max(0.0, min(e.y / h - span / 2, 1 - span))
        self.canvas.yview_moveto(frac)

    def on_canvas_resize(self, e):
        if abs(e.width - getattr(self, '_cw', 0)) < 3:
            return
        self._cw = e.width
        self.render()

    def hide_banner(self):
        self.banner.pack_forget(); self.banner_on = False

    def collapse(self):
        self.set_collapsed(not getattr(self, '_col', False))

    def set_collapsed(self, on):
        self._col = bool(on)
        if self._col:
            for f in (self.stats, self.wrap, self.foot): f.pack_forget()
            if self.banner_on: self.banner.pack_forget()
            try: self.draw_add_btn(compact=True)
            except Exception: pass
            self.root.geometry(f'{COL_W}x80')
        else:
            try: self.draw_add_btn(compact=False)
            except Exception: pass
            self.stats.pack(fill='x', padx=PAD, pady=(10, 0))
            if self.banner_on: self.banner.pack(fill='x', padx=PAD, pady=(8, 0))
            self.wrap.pack(fill='both', expand=True, padx=(PAD, 10), pady=(10, 0))
            self.foot.pack(fill='x', padx=PAD, pady=(10, 12))
            self.root.geometry(f'{W}x{H}')
        self.render()

    def rebuild(self):
        """换主题后重建界面（不碰已打开的弹窗）"""
        col = bool(getattr(self, '_col', False))
        for name in ('head', 'stats', 'banner', 'wrap', 'foot'):
            w = getattr(self, name, None)
            if w is not None:
                try: w.destroy()
                except Exception: pass
        self.banner_on = False
        self.root.configure(bg=BG)
        self.build()
        self.set_collapsed(col)
        try: self.root.attributes('-alpha', self.alpha)
        except Exception: pass

    def menu(self):
        m = tk.Menu(self.root, tearoff=0, bg=CARD2, fg=TXT, activebackground=BLUE,
                    activeforeground='white', bd=0)
        m.add_command(label='新增待办…（Ctrl+N）', command=self.todo_dialog)
        m.add_separator()
        m.add_command(label=('取消置顶' if self.root.attributes('-topmost') else '窗口置顶'),
                      command=self.toggle_top)
        m.add_command(label='外观设置（背景色 / 透明度）…', command=self.settings)
        m.add_command(label='刷新数据（重新抓微信群）', command=self.do_refresh)
        m.add_command(label='打开台账文件夹', command=lambda: os.startfile(BASE))
        m.add_command(label='重置全部完成标记（保留备注）', command=self.reset_done)
        m.add_separator()
        m.add_command(label='退出', command=self.quit)
        try: m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally: m.grab_release()

    def reset_done(self):
        from tkinter import messagebox
        if not messagebox.askyesno('确认', '把全部“已完成”标记清掉？（备注会保留）'):
            return
        st = {}
        if os.path.exists(DONE):
            try: st = json.load(open(DONE, encoding='utf-8'))
            except Exception: st = {}
        keep = {k: {'note': v['note']} for k, v in st.items() if v.get('note')}
        json.dump(keep, open(DONE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        self.reload()
        self.lb_foot.configure(text='已重置完成标记')

    def toggle_top(self):
        v = not bool(self.root.attributes('-topmost'))
        self.root.attributes('-topmost', v); self.save_cfg()

    # ---------- 外观 ----------
    def settings(self):
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        top.configure(bg=CARD)
        w, h = 396, 300
        x = max(self.root.winfo_x() - w - 24, 16)
        y = max(min(self.root.winfo_y() + 40, self.root.winfo_screenheight() - h - 40), 16)
        top.geometry(f'{w}x{h}+{x}+{y}')
        FG, SUB2, SF, HOV = TXT, SUB, CARD, HOVER
        tk.Label(top, text='外观设置', font=self.f_title, fg=FG, bg=SF).pack(anchor='w', padx=14, pady=(12, 1))
        tk.Label(top, text='改完立即生效，保存在 挂件配置.json', font=self.f_small,
                 fg=MUT, bg=SF).pack(anchor='w', padx=14)

        tk.Label(top, text='背景色（卡片、分割线自动跟着调）', font=self.f_small,
                 fg=SUB2, bg=SF).pack(anchor='w', padx=14, pady=(12, 5))
        prow = tk.Frame(top, bg=SF); prow.pack(fill='x', padx=14)
        e_var = tk.StringVar(value=self.bg)

        def use(hexv):
            self.bg = apply_theme(hexv)
            self.cfg['bg'] = self.bg
            e_var.set(self.bg)
            self.rebuild()
            self.save_cfg()

        for name, hexv in BG_PRESETS:
            sw = tk.Frame(prow, bg=CARD2, highlightthickness=1, highlightbackground=LINE,
                          cursor='hand2')
            sw.pack(side='left', padx=(0, 6))
            chip = tk.Canvas(sw, width=12, height=12, bg=CARD2, highlightthickness=0, bd=0)
            chip.create_polygon(rr_pts(0, 0, 12, 12, 3), smooth=True, fill=hexv, outline=LINE)
            chip.pack(side='left', padx=(8, 5), pady=7)
            lb = tk.Label(sw, text=name, font=self.f_small, fg=TXT, bg=CARD2, cursor='hand2')
            lb.pack(side='left', padx=(0, 9), pady=5)
            for c in (sw, lb, chip):
                c.bind('<Button-1>', lambda e, v=hexv: use(v))

        erow = tk.Frame(top, bg=SF); erow.pack(fill='x', padx=14, pady=(9, 0))
        tk.Label(erow, text='自定义', font=self.f_small, fg=SUB2, bg=SF).pack(side='left')
        tk.Entry(erow, textvariable=e_var, width=10, bg=HOV, fg=FG, insertbackground=FG, bd=0,
                 font=self.f_small, highlightthickness=1,
                 highlightbackground=LINE).pack(side='left', padx=(6, 6), ipady=3)
        self.mk_chip(erow, '应用', lambda: use(e_var.get()), h=26, padx=12).pack(side='left')

        tk.Label(top, text='透明度（往左拖透出桌面，文字会一起变淡）', font=self.f_small,
                 fg=SUB2, bg=SF).pack(anchor='w', padx=14, pady=(16, 0))
        arow = tk.Frame(top, bg=SF); arow.pack(fill='x', padx=14)
        a_lb = tk.Label(arow, text=f'{int(self.alpha * 100)}%', font=self.f_small, fg=FG, bg=SF,
                        width=5, anchor='e')
        a_lb.pack(side='right')

        def on_alpha(v):
            a = max(0.35, min(float(v) / 100.0, 1.0))
            self.alpha = a
            try: self.root.attributes('-alpha', a)
            except Exception: pass
            a_lb.configure(text=f'{int(a * 100)}%')

        s_cv = tk.Canvas(arow, width=232, height=22, bg=SF, highlightthickness=0, bd=0,
                         cursor='hand2')
        s_cv.pack(side='left', pady=(1, 0))

        def draw_slider():
            s_cv.delete('all')
            x0, x1, cy = 6, 226, 11
            s_cv.create_line(x0, cy, x1, cy, fill='#2C3947', width=4, capstyle='round')
            frac = (self.alpha * 100 - 35) / 65
            kx = x0 + (x1 - x0) * max(0.0, min(frac, 1.0))
            s_cv.create_line(x0, cy, kx, cy, fill=BLUE, width=4, capstyle='round')
            s_cv.create_oval(kx - 6, cy - 6, kx + 6, cy + 6, fill='#FFFFFF', outline='')

        def set_from_x(x):
            frac = min(max((x - 6) / 220.0, 0.0), 1.0)
            on_alpha(35 + frac * 65)
            draw_slider()

        s_cv.bind('<Button-1>', lambda e: set_from_x(e.x))
        s_cv.bind('<B1-Motion>', lambda e: set_from_x(e.x))
        draw_slider()

        bar = tk.Frame(top, bg=SF); bar.pack(fill='x', padx=14, pady=(16, 12), side='bottom')

        def close():
            self.cfg['bg'] = self.bg
            self.cfg['alpha'] = round(self.alpha, 2)
            self.save_cfg()
            top.destroy()

        self.mk_chip(bar, '保存并关闭', close, h=30, padx=16).pack(side='right')

        def reset(_=None):
            use(DEFAULT_BG)
            on_alpha(100)
            draw_slider()

        self.mk_chip(bar, '恢复默认', reset, fg=SUB, bg=CARD2, h=30, padx=14).pack(side='right', padx=(0, 8))
        tk.Label(bar, text='收起时是小条，尺寸沿用上次', font=self.f_small, fg=MUT,
                 bg=SF).pack(side='left')
        top.bind('<Escape>', lambda e: close())

    def set_filter(self, key):
        self.filter = key; self.render()

    def toggle_filter(self):
        self.filter = 'all' if self.filter == 'open' else 'open'; self.render()

    # ---------- 数据 ----------
    def reload(self):
        try:
            d = json.load(open(LEDGER, encoding='utf-8'))
            self.items, self.last_gen = d.get('items', []), d.get('generated', '')
        except Exception:
            self.items = []
        self.merge_manual()
        self.render()

    # ---------- 自建待办 ----------
    @staticmethod
    def is_manual(it):
        return bool(it.get('manual')) or str(it.get('id', '')).startswith('m')

    def load_manual(self):
        if not os.path.exists(MANUAL):
            return []
        try:
            d = json.load(open(MANUAL, encoding='utf-8'))
        except Exception:
            return []
        if isinstance(d, dict):
            d = d.get('items') or []
        return [x for x in d if isinstance(x, dict) and (x.get('text') or '').strip()]

    def save_manual(self, lst):
        try:
            tmp = MANUAL + '.tmp'
            json.dump({'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                       'items': lst}, open(tmp, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
            os.replace(tmp, MANUAL)
        except Exception as e:
            self.lb_foot.configure(text=f'保存自建待办失败：{e}')

    def merge_manual(self):
        st = {}
        if os.path.exists(DONE):
            try: st = json.load(open(DONE, encoding='utf-8'))
            except Exception: st = {}
        for it in self.load_manual():
            it['manual'] = True
            if it.get('deadline') and not parse_dt(it['deadline']):
                it['deadline'] = parse_when(it['deadline']) or ''   # 手改文件也认
            e = st.get(it.get('id')) or {}
            it['done'] = bool(e.get('done', it.get('done')))
            it['done_at'] = e.get('at', it.get('done_at') or '')
            it['note'] = e.get('note', it.get('note') or '')
            for k, v in (('group', MANUAL_GROUP), ('from', '自建'), ('time', ''), ('deadline', ''),
                         ('replied', ''), ('rtext', ''), ('ctx', []), ('img', ''),
                         ('img_text', ''), ('att', ''), ('att_name', '')):
                it.setdefault(k, v)
            it['prio'] = self._prio(it)
            self.items.append(it)
        self.sort_items()

    def sort_items(self):
        self.items.sort(key=lambda x: (ORDER.get(x.get('prio'), 3),
                                       x.get('deadline') or '9999', x.get('time') or ''))

    def todo_dialog(self, it=None):
        """新增 / 编辑自建待办"""
        editing = it is not None and self.is_manual(it)
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        top.configure(bg=CARD)
        w, h = 440, 348
        x = max(self.root.winfo_x() - w - 24, 16)
        y = max(min(self.root.winfo_y() + 40, self.root.winfo_screenheight() - h - 40), 16)
        top.geometry(f'{w}x{h}+{x}+{y}')
        tk.Label(top, text=('编辑自建待办' if editing else '新增待办'),
                 font=self.f_title, fg=TXT, bg=CARD).pack(anchor='w', padx=14, pady=(12, 2))
        tk.Label(top, text='事项（写清楚要做什么、交给谁/报给谁）', font=self.f_small,
                 fg=MUT, bg=CARD).pack(anchor='w', padx=14)
        txt = tk.Text(top, height=4, bg=CARD2, fg=TXT, insertbackground=TXT, bd=0,
                      font=self.f_small, wrap='word', padx=8, pady=6)
        txt.pack(fill='x', padx=14, pady=(4, 0))
        if editing:
            txt.insert('1.0', it.get('text', ''))
        txt.focus_set()

        tk.Label(top, text='截止时间（2026-10-28 17:00 / 明天下午5点 / 周五下班前；留空=不设）',
                 font=self.f_small, fg=MUT, bg=CARD, wraplength=w - 28,
                 justify='left').pack(anchor='w', padx=14, pady=(9, 2))
        dl_var = tk.StringVar(value=(it.get('deadline', '') if editing else ''))
        ent = tk.Entry(top, textvariable=dl_var, bg=HOVER, fg=TXT, insertbackground=TXT,
                       bd=0, font=self.f_small, highlightthickness=1,
                       highlightbackground=LINE, highlightcolor=BLUE)
        ent.pack(fill='x', padx=14, ipady=6)

        quick = tk.Frame(top, bg=CARD); quick.pack(fill='x', padx=14, pady=(6, 0))
        for lb, val in [('今天17:00', '今天17:00'), ('明天17:00', '明天17:00'),
                        ('本周五下班前', '本周五下班前'), ('清空', '')]:
            self.mk_chip(quick, lb, lambda v=val: dl_var.set(v), fg=BLUE, bg=CARD2,
                         h=24, padx=9).pack(side='left', padx=(0, 6))

        row = tk.Frame(top, bg=CARD); row.pack(fill='x', padx=14, pady=(9, 0))
        tk.Label(row, text='交办人/来源', font=self.f_small, fg=MUT, bg=CARD).pack(side='left')
        who_var = tk.StringVar(value=(it.get('from', '') if editing else ''))
        tk.Entry(row, textvariable=who_var, bg=HOVER, fg=TXT, insertbackground=TXT, bd=0,
                 font=self.f_small, width=16, highlightthickness=1,
                 highlightbackground=LINE).pack(side='left', padx=(8, 0), ipady=4)
        tk.Label(row, text='可写：领导口头 / 自己想到', font=self.f_small,
                 fg=MUT, bg=CARD).pack(side='left', padx=(8, 0))

        tip = tk.Label(top, text='', font=self.f_small, fg=RED, bg=CARD, anchor='w',
                       wraplength=w - 28, justify='left')
        tip.pack(anchor='w', padx=14, pady=(4, 0))
        bar = tk.Frame(top, bg=CARD); bar.pack(fill='x', padx=14, pady=(4, 12))

        def save(_=None):
            text = txt.get('1.0', 'end').strip()
            if not text:
                tip.configure(text='事项不能空'); return
            dl = parse_when(dl_var.get())
            if dl is None:
                tip.configure(text='时间看不懂：可写 2026-10-28 17:00、明天17:00、周五下班前'); return
            self.save_todo(it if editing else None, text, dl, who_var.get().strip())
            top.destroy()

        self.mk_chip(bar, ('保存' if editing else '添加待办'), save, h=30, padx=18).pack(side='right')
        self.mk_chip(bar, '取消', top.destroy, fg=SUB, bg=CARD2, h=30, padx=14).pack(
            side='right', padx=(0, 8))
        tk.Label(bar, text='Ctrl+Enter 保存　Esc 取消', font=self.f_small, fg=MUT,
                 bg=CARD).pack(side='left')
        top.bind('<Escape>', lambda e: top.destroy())
        txt.bind('<Control-Return>', save)
        ent.bind('<Return>', save)

    def save_todo(self, it, text, deadline, who):
        p = parse_when(deadline)
        deadline = p if p else ''
        lst = self.load_manual()
        if it:
            hit = False
            for i, x in enumerate(lst):
                if x.get('id') == it.get('id'):
                    x['text'] = text[:1200]
                    x['deadline'] = deadline
                    if who: x['from'] = who[:20]
                    lst[i] = x
                    hit = True
                    break
            if not hit:
                lst.append(make_manual(text, deadline, who, tid=it.get('id')))
            msg = '待办已更新'
        else:
            new = make_manual(text, deadline, who)
            lst.append(new)
            self.expanded.add(new['id'])
            msg = '已新增待办：' + (text[:16] + ('…' if len(text) > 16 else ''))
        self.save_manual(lst)
        self.reload()
        self.lb_foot.configure(text=msg)

    def delete_todo(self, it):
        from tkinter import messagebox
        was = bool(self.root.attributes('-topmost'))
        self.root.attributes('-topmost', False)
        try:
            ok = messagebox.askyesno('删除待办', '删掉这条自建待办？\n\n' + it.get('text', '')[:60])
        finally:
            self.root.attributes('-topmost', was)
        if not ok: return
        self.save_manual([x for x in self.load_manual() if x.get('id') != it.get('id')])
        try:
            if os.path.exists(DONE):
                st = json.load(open(DONE, encoding='utf-8'))
                if st.pop(it.get('id'), None) is not None:
                    json.dump(st, open(DONE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception:
            pass
        self.expanded.discard(it.get('id'))
        self.reload()
        self.lb_foot.configure(text='已删除自建待办')

    def do_refresh(self):
        if self.fetching: return
        self.fetching = True; self.btn_refresh.configure(fg=AMBER)

        def work():
            try: subprocess.run([PY, ENGINE, '--fetch'], capture_output=True, timeout=900)
            except Exception: pass
            self.root.after(0, self.after_fetch)
        threading.Thread(target=work, daemon=True).start()

    def after_fetch(self):
        self.fetching = False; self.btn_refresh.configure(fg=SUB); self.reload()

    @staticmethod
    def is_today(it):
        # 今日 = 今天发布的消息；昨天发的、今天到期的用卡片上的截止时间提示，不算“今日”
        # 自建待办没有“发布”一说，按截止日期算今天
        if it.get('manual') or str(it.get('id', '')).startswith('m'):
            d = parse_dt(it.get('deadline', ''))
            return bool(d) and d.date() == datetime.date.today()
        return it['time'][:10] == datetime.date.today().isoformat()

    def counts(self):
        c = {'overdue': 0, 'today': 0, 'open': 0, 'done': 0, 'new': 0}
        for it in self.items: c[it['prio']] = c.get(it['prio'], 0) + 1
        c['today'] = sum(1 for it in self.items if self.is_today(it))
        return c

    # ---------- 渲染 ----------
    def visible_items(self):
        f = self.filter
        if f == 'all': return list(self.items)
        if f == 'done': return [i for i in self.items if i['done']]
        if f == 'today': return [i for i in self.items if self.is_today(i)]
        if f == 'overdue': return [i for i in self.items if i['prio'] == 'overdue']
        return [i for i in self.items if not i['done']]

    def render(self):
        c = self.counts()
        opens = len(self.items) - c['done']
        if getattr(self, '_col', False):
            bits = [f'待办 {opens}']
            if c['overdue']: bits.append(f'逾期 {c["overdue"]}')
            if c['today']: bits.append(f'今日 {c["today"]}')
            self.lb_date.configure(text=' · '.join(bits),
                                   fg=RED if c['overdue'] else (AMBER if c['today'] else SUB))
        else:
            gen = f' · 更新 {self.last_gen[11:16]}' if self.last_gen else ' · 未取数'
            self.lb_date.configure(text=datetime.date.today().strftime('%Y年%m月%d日') + gen, fg=MUT)

        self.draw_segments(c)
        self.btn_filter.configure(text='只看待办' if self.filter == 'all' else '显示全部')

        cv = self.canvas
        cv.delete('all')
        self._imgrefs = []
        cw = cv.winfo_width()
        if cw < 120: cw = W - PAD - 10 - 6
        y = 6
        show = self.visible_items()
        if not show:
            y = self.draw_empty(y, cw)
        elif self.filter == 'done':
            y = self.draw_done_groups(show, y, cw)
        else:
            for it in show:
                y = self.draw_card(it, y, cw)
        cv.configure(scrollregion=(0, 0, cw, y + 6))
        self.lb_foot.configure(text=f'共 {len(self.items)} 条 · 未完成 {opens}')

        hot = [i for i in self.items if not i['done'] and i['prio'] in ('overdue', 'today')]
        if hot and not getattr(self, '_col', False):
            head = hot[0]['text'].replace('\n', ' ')
            head = head[:16] + ('…' if len(head) > 16 else '')
            self.draw_banner(f'⚠ {len(hot)} 件要处理 · {head}')
            if not self.banner_on:
                self.banner.pack(fill='x', padx=PAD, pady=(8, 0), after=self.stats)
                self.banner_on = True
        elif self.banner_on:
            self.hide_banner()

    # ---------- 画布元素 ----------
    def _next_tag(self, prefix):
        self._seq += 1
        return f'{prefix}{self._seq}'

    def _text(self, x, y, text, font=None, fill=None, width=None, anchor='nw', tags=()):
        font = font or self.f_body
        i = self.canvas.create_text(x, y, text=text, font=font, fill=fill or TXT,
                                    width=width, anchor=anchor, justify='left', tags=tags)
        return i, self.canvas.bbox(i)

    def _chip(self, x, y, text, font=None, fg=SUB, bg=None, padx=10, h=24, r=8,
              cmd=None, hbg=None, hfg=None):
        font = font or self.f_small
        bg = CARD2 if bg is None else bg
        w = font.measure(text) + padx * 2
        tg = self._next_tag('chip')
        rid = self.canvas.create_polygon(rr_pts(x, y, x + w, y + h, r), smooth=True,
                                         fill=bg, outline='', tags=(tg,))
        tid = self.canvas.create_text(x + w / 2, y + h / 2, text=text, fill=fg, font=font,
                                      tags=(tg,))
        if cmd is not None:
            self.canvas.tag_bind(tg, '<Button-1>', lambda e, c=cmd: c())
            self.canvas.tag_bind(tg, '<Enter>', lambda e, rid=rid, tid=tid:
                                 (self.canvas.itemconfigure(rid, fill=hbg or HOVER),
                                  self.canvas.itemconfigure(tid, fill=hfg or TXT)))
            self.canvas.tag_bind(tg, '<Leave>', lambda e, rid=rid, tid=tid:
                                 (self.canvas.itemconfigure(rid, fill=bg),
                                  self.canvas.itemconfigure(tid, fill=fg)))
        return w

    def _block(self, y, cw, head, text, fg=SUB, ctag=''):
        pad, inner = 14, 10
        x = pad + inner
        wid = max(cw - 2 * x, 80)
        _, hb = self._text(x, y + inner - 2, head, self.f_small, MUT, width=wid, tags=(ctag,))
        _, tb = self._text(x, hb[3] + 3, text, self.f_small, fg, width=wid, tags=(ctag,))
        bot = tb[3] + inner
        rid = self.canvas.create_polygon(rr_pts(pad, y, cw - pad, bot, 10), smooth=True,
                                         fill=CARD2, outline='', tags=(ctag,))
        self.canvas.tag_lower(rid)
        return bot

    def draw_empty(self, y, cw):
        self.canvas.create_text(cw / 2, y + 44, text='这一栏是空的', font=self.f_body, fill=SUB)
        self.canvas.create_text(cw / 2, y + 68, text='点右上角「＋ 新增」，或按 Ctrl+N',
                                font=self.f_small, fill=MUT)
        txt = '＋ 新增待办'
        w = self.f_small.measure(txt) + 28
        self._chip((cw - w) / 2, y + 88, txt, fg='#FFFFFF', bg=BLUE, padx=14, h=28,
                   cmd=self.todo_dialog, hbg='#629BFF', hfg='#FFFFFF')
        return y + 128

    # ---------- 已完成按日期折叠 ----------
    def done_day_keys(self, items):
        out = {}
        for it in items:
            d = (it.get('done_at') or '')[:10] or (it.get('time') or '')[:10] or '0000-00-00'
            out.setdefault(d, []).append(it)
        return out

    def day_label(self, key):
        try:
            d = datetime.date.fromisoformat(key)
        except Exception:
            return '完成时间未记录'
        today = datetime.date.today()
        wd = '一二三四五六日'[d.weekday()]
        s = f'{d.month}月{d.day}日 周{wd}'
        if d.year != today.year:
            s = f'{d.year}年' + s
        if d == today:
            return '今天 · ' + s
        if (today - d).days == 1:
            return '昨天 · ' + s
        return s

    def toggle_day(self, key):
        if key in self.closed_days:
            self.closed_days.discard(key)
        else:
            self.closed_days.add(key)
        self.render()

    def draw_done_groups(self, items, y, cw):
        groups = self.done_day_keys(items)
        keys = sorted(groups.keys(), reverse=True)
        if not getattr(self, '_grp_init', False) and keys:
            self.closed_days = set(keys[1:])      # 默认只展开最近一天
            self._grp_init = True
        for i, k in enumerate(keys):
            grp = groups[k]
            open_ = k not in self.closed_days
            if i:
                y += 4
            y = self.draw_group_header(i, k, len(grp), y, cw, open_)
            if open_:
                for it in grp:
                    y = self.draw_card(it, y, cw)
        return y

    def draw_group_header(self, idx, key, n, y, cw, open_):
        cv = self.canvas
        hh = 34
        mid = y + hh / 2
        chev = cv.create_text(3, mid, text=('▾' if open_ else '▸'), font=self.f_small,
                              fill=(SUB if open_ else MUT), anchor='w')
        lab = cv.create_text(20, mid, text=self.day_label(key), font=self.f_small,
                             fill=(TXT if open_ else SUB), anchor='w')
        cnt = cv.create_text(cw - 2, mid, text=f'{n} 条', font=self.f_small, fill=MUT, anchor='e')
        ln = cv.create_line(0, y + hh - 1, cw, y + hh - 1, fill=LINE)

        def click(_e=None, k=key):
            self.toggle_day(k)
        for i in (chev, lab, cnt, ln):
            cv.tag_bind(i, '<Button-1>', click)
        cv.tag_bind(lab, '<Enter>', lambda e, i=lab: cv.itemconfigure(i, fill=BLUE))
        cv.tag_bind(lab, '<Leave>', lambda e, i=lab:
                    cv.itemconfigure(i, fill=(TXT if open_ else SUB)))
        return y + hh + (2 if open_ else 4)

    # ---------- 卡片 ----------
    def _meta_color(self, it):
        if it['done']: return SUB
        if it['prio'] == 'overdue': return RED
        if it['prio'] == 'today': return AMBER
        return SUB

    def _meta(self, it, tail, expanded):
        in_done_view = (self.filter == 'done')
        pub = it['time'][5:16] if len(it['time']) >= 16 else it['time']
        if in_done_view and (it.get('done_at') or '').strip():
            pub = '完成于 ' + it['done_at'][5:16]
        grp = (it['group'].replace('运管投诉件处理', '投诉件')
                         .replace('（港航中心航运处）水运管理群', '水运管理群')
                         .replace('港海地海防汛防台风', '防汛防台风')
                         .replace('平潭综合实验区船东协会', '船东协会')
                         .replace('全区客渡船停复航群', '客渡船停复航'))
        if len(grp) > 9: grp = grp[:9] + '…'
        parts = ['已办结'] if (it['done'] and not in_done_view) else []
        if not expanded:                      # 展开时详情里已经有这些，标题区只留截止
            parts.append(f'{pub} · {it["from"]}')
            if not self.is_manual(it):
                parts.append(grp)
            if it.get('note'):
                parts.append('有备注')
        if tail:
            parts.append(tail)
        return ' · '.join(parts)

    def draw_card(self, it, y, cw):
        cv = self.canvas
        ctag = 'c' + str(it['id'])
        expanded = it['id'] in self.expanded
        tail = rel_deadline(it['deadline'])
        pad, left = 14, 28

        # 右上角：完成 / 已完成
        done = it['done']
        dtxt = '✓ 已完成' if done else '完成'
        dw = self.f_small.measure(dtxt) + 18
        dx = cw - pad - dw
        self._chip(dx, y + 12, dtxt, fg=(GREEN if done else SUB),
                   bg=('#15251B' if done else CARD2), padx=9, h=24,
                   cmd=lambda i=it: self.toggle_done(i),
                   hbg=('#1B3325' if done else HOVER), hfg=GREEN)
        hx = dx - 6
        if self.is_manual(it):
            w2 = self.f_small.measure('自建') + 14
            hx -= w2
            self._chip(hx, y + 12, '自建', fg=BLUE, bg='#18263B', padx=7, h=24)
            hx -= 6

        # 状态点 + 标题
        cv.create_oval(left - 13, y + 16, left - 6, y + 23,
                       fill=PRIO_COLOR[it['prio']], outline='', tags=(ctag,))
        twrap = max(hx - left - 6, 120)
        title = it['text'].replace('\n', ' ')
        if not expanded and len(title) > 54:
            title = title[:54] + '…'
        _, tb = self._text(left, y + 12, title, self.f_body, MUT if done else TXT,
                           width=twrap, tags=(ctag,))

        # 元信息
        meta = self._meta(it, tail, expanded) or '\u00a0'
        _, mb = self._text(left, tb[3] + 5, meta, self.f_small,
                           self._meta_color(it), width=max(cw - left - pad, 140), tags=(ctag,))
        bottom = mb[3] + 12
        if expanded:
            bottom = self._draw_detail(it, mb[3] + 10, cw, ctag, tail)

        bgid = cv.create_polygon(rr_pts(0, y, cw, bottom, 12), smooth=True,
                                 fill=CARD, outline='', tags=(ctag,))
        cv.tag_lower(bgid)
        cv.tag_bind(ctag, '<Enter>', lambda e, b=bgid: cv.itemconfigure(b, fill=CARD_HOVER))
        cv.tag_bind(ctag, '<Leave>', lambda e, b=bgid: cv.itemconfigure(b, fill=CARD))
        cv.tag_bind(ctag, '<Button-1>', lambda e, i=it: self.toggle_detail(i))
        cv.tag_bind(ctag, '<Button-3>', lambda e, i=it: self.card_menu(e, i))
        return bottom + GAP

    def _draw_detail(self, it, y, cw, ctag, tail):
        cv = self.canvas
        pad, left = 14, 28
        inner = cw - pad
        cv.create_line(left, y, inner - 6, y, fill=LINE, tags=(ctag,))
        yy = y + 12

        _, b = self._text(left, yy, it['text'], self.f_body, TXT, width=inner - left, tags=(ctag,))
        yy = b[3] + 10

        src = (f'来源 {it["from"]} · 建立 {it["time"]}' if self.is_manual(it)
               else f'群 {it["group"]} · 交办 {it["from"]}')
        _, b = self._text(left, yy, src, self.f_small, MUT, width=inner - left, tags=(ctag,))
        yy = b[3] + 2
        if not self.is_manual(it):
            _, b = self._text(left, yy, f'发布 {it["time"]}', self.f_small, MUT,
                              width=inner - left, tags=(ctag,))
            yy = b[3] + 2
        if it['deadline']:
            _, b = self._text(left, yy, f'截止 {it["deadline"]}' + (f' · {tail}' if tail else ''),
                              self.f_small, self._meta_color(it), width=inner - left, tags=(ctag,))
            yy = b[3] + 2
        if it['done_at']:
            _, b = self._text(left, yy, f'完成于 {it["done_at"]}', self.f_small, MUT,
                              width=inner - left, tags=(ctag,))
            yy = b[3] + 2
        yy += 8

        if it.get('rtext'):
            yy = self._block(yy, cw, '我的回应 · ' + (it['replied'] or ''), it['rtext'],
                             BLUE, ctag) + 8
        note = (it.get('note') or '').strip()
        if note:
            yy = self._block(yy, cw, '完成备注', note, SUB, ctag) + 8

        img = it.get('img') or ''
        if img and os.path.exists(img):
            yy = self._draw_img(it, img, yy, cw, ctag) + 8

        att = it.get('att') or ''
        if att and os.path.exists(att):
            name = (it.get('att_name') or os.path.basename(att))
            name = name[:38] + ('…' if len(name) > 38 else '')
            self._chip(left, yy, '📄 ' + name, fg=BLUE, bg=CARD2, padx=10, h=26,
                       cmd=lambda p=att: os.startfile(p), hbg=HOVER, hfg=TXT)
            yy += 26 + 8

        ops = [(('撤销完成' if it['done'] else '标记完成'),
                lambda i=it: self.toggle_done(i), (SUB if it['done'] else GREEN))]
        ops.append(('复制原文', lambda i=it: self.copy_text(it), SUB))
        ops.append((('修改备注' if note else '写备注'), lambda i=it: self.edit_note(it), BLUE))
        if self.is_manual(it):
            ops.append(('编辑', lambda i=it: self.todo_dialog(i), BLUE))
            ops.append(('删除', lambda i=it: self.delete_todo(i), RED))
        x = left
        for txt, cmd, fg in ops:
            w = self._chip(x, yy, txt, fg=fg, bg=CARD2, padx=10, h=26, cmd=cmd,
                           hbg=HOVER, hfg=TXT)
            x += w + 6
        return yy + 26 + 12

    def _draw_img(self, it, path, y, cw, ctag):
        cv = self.canvas
        left, pad = 28, 14
        ytag = self._next_tag('img')
        try:
            from PIL import Image, ImageStat, ImageTk
            im0 = Image.open(path)
            if ImageStat.Stat(im0.convert('L')).stddev[0] < 2:
                _, b = self._text(left, y, '⚠ 这张本机只是空白占位图，原图需在微信里重新下载',
                                  self.f_small, AMBER, width=cw - left - pad, tags=(ctag,))
                return b[3] + 4
            im0.thumbnail((280, 190))
            ph = ImageTk.PhotoImage(im0)
            self._imgrefs.append(ph)
            cv.create_image(left, y, image=ph, anchor='nw', tags=(ctag, ytag))
            cv.tag_bind(ytag, '<Button-1>', lambda e, p=path: self.show_image(p))
            yy = y + im0.size[1] + 8
        except Exception as e:
            _, b = self._text(left, y, f'缩略图打不开：{e}', self.f_small, MUT,
                              width=cw - left - pad, tags=(ctag,))
            return b[3] + 4
        x = left
        for txt, cmd in [('看大图', lambda p=path: self.show_image(p)),
                         ('外部打开', lambda p=path: os.startfile(p))]:
            w = self._chip(x, yy, txt, fg=BLUE, bg=CARD2, padx=10, h=24, cmd=cmd,
                           hbg=HOVER, hfg=TXT)
            x += w + 6
        return yy + 24 + 2

    def edit_note(self, it):
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        top.configure(bg=CARD)
        w, h = 420, 250
        top.geometry(f'{w}x{h}+{max(self.root.winfo_x() - w - 20, 20)}+{self.root.winfo_y() + 80}')
        tk.Label(top, text='完成情况备注', font=self.f_small, fg=TXT, bg=CARD).pack(anchor='w', padx=12, pady=(10, 2))
        tk.Label(top, text='写清楚怎么处理的、结果如何、还差什么', font=self.f_small, fg=MUT,
                 bg=CARD).pack(anchor='w', padx=12)
        txt = tk.Text(top, height=6, bg=CARD2, fg=TXT, insertbackground=TXT, bd=0,
                      font=self.f_small, wrap='word', padx=8, pady=6)
        txt.pack(fill='both', expand=True, padx=12, pady=8)
        txt.insert('1.0', (it.get('note') or ''))
        txt.focus_set()
        bar = tk.Frame(top, bg=CARD); bar.pack(fill='x', padx=12, pady=(0, 10))
        def save(_=None):
            val = txt.get('1.0', 'end').strip()
            self.save_note(it, val)
            top.destroy()
        self.mk_chip(bar, '保存备注', save, h=30, padx=16).pack(side='right')
        self.mk_chip(bar, '取消', top.destroy, fg=SUB, bg=CARD2, h=30, padx=14).pack(
            side='right', padx=(0, 8))
        top.bind('<Escape>', lambda e: top.destroy())
        txt.bind('<Control-Return>', save)

    def save_note(self, it, val):
        st = {}
        if os.path.exists(DONE):
            try: st = json.load(open(DONE, encoding='utf-8'))
            except Exception: st = {}
        ent = st.get(it['id']) or {}
        ent['note'] = val
        if it['done']:
            ent['done'] = True
            ent['at'] = it.get('done_at') or ent.get('at') or ''
        st[it['id']] = ent
        json.dump(st, open(DONE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        it['note'] = val
        self.lb_foot.configure(text='备注已保存')
        self.render()

    def show_image(self, path):
        try:
            from PIL import Image, ImageTk
        except Exception as e:
            self.lb_foot.configure(text=f'预览失败：{e}')
            return
        try:
            im = Image.open(path)
            im.thumbnail((760, 900))
            top = tk.Toplevel(self.root)
            top.overrideredirect(True)
            top.attributes('-topmost', True)
            top.configure(bg=BG)
            tkimg = ImageTk.PhotoImage(im)
            lb = tk.Label(top, image=tkimg, bg=BG, bd=0)
            lb.image = tkimg
            lb.pack(padx=8, pady=(8, 4))
            tk.Label(top, text=os.path.basename(path) + '   点一下关闭', font=self.f_small,
                     fg=MUT, bg=BG).pack(pady=(0, 8))
            x = max(self.root.winfo_x() - im.width - 30, 20)
            top.geometry(f'+{x}+{max(self.root.winfo_y() - 60, 20)}')
            top.bind('<Button-1>', lambda e: top.destroy())
            for w in top.winfo_children():
                w.bind('<Button-1>', lambda e: top.destroy())
            top.after(60000, top.destroy)
        except Exception as e:
            self.lb_foot.configure(text=f'预览失败：{e}')

    def card_menu(self, e, it):
        m = tk.Menu(self.root, tearoff=0, bg=CARD2, fg=TXT, activebackground=BLUE,
                    activeforeground='white', bd=0)
        m.add_command(label='撤销完成' if it['done'] else '标记完成',
                      command=lambda: self.toggle_done(it))
        m.add_command(label='复制原文', command=lambda: self.copy_text(it))
        m.add_command(label='写/改完成备注', command=lambda: self.edit_note(it))
        if self.is_manual(it):
            m.add_separator()
            m.add_command(label='编辑这条待办…', command=lambda: self.todo_dialog(it))
            m.add_command(label='删除这条待办', command=lambda: self.delete_todo(it))
        try: m.tk_popup(e.x_root, e.y_root)
        finally: m.grab_release()

    def toggle_detail(self, it):
        self.expanded.symmetric_difference_update({it['id']})
        self.render()

    def copy_text(self, it):
        self.root.clipboard_clear(); self.root.clipboard_append(it['text'])
        self.lb_foot.configure(text='已复制到剪贴板')

    def _prio(self, it):
        if it['done']: return 'done'
        dl = parse_dt(it['deadline'])
        if dl:
            now = datetime.datetime.now()
            if dl < now: return 'overdue'
            if dl.date() == now.date(): return 'today'
        if not it['replied'] and it.get('age_days', 99) <= 2: return 'new'
        return 'open'

    def toggle_done(self, it):
        st = {}
        if os.path.exists(DONE):
            try: st = json.load(open(DONE, encoding='utf-8'))
            except Exception: st = {}
        it['done'] = not it['done']
        ent = dict(st.get(it['id']) or {})     # 保留已有备注，别被完成标记冲掉
        if it['done']:
            it['done_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            ent['done'] = True; ent['at'] = it['done_at']
            st[it['id']] = ent
        else:
            it['done_at'] = ''
            ent.pop('done', None); ent.pop('at', None)
            if (ent.get('note') or '').strip(): st[it['id']] = ent
            else: st.pop(it['id'], None)
        it['prio'] = self._prio(it)
        json.dump(st, open(DONE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        self.sort_items()
        self.render()
        self.lb_foot.configure(text=('已标记完成 ✓' if it['done'] else '已撤销完成'))

    # ---------- 定时 ----------
    def tick(self):
        try:
            mt = os.path.getmtime(LEDGER)
            if mt != getattr(self, '_mt', 0):
                self._mt = mt; self.reload()
            else:
                self.render()
        except Exception:
            pass
        self.check_reminders()
        self.root.after(60_000, self.tick)

    def check_reminders(self):
        hot = []
        for it in self.items:
            if it['done'] or it['id'] in self.reminded: continue
            if it['prio'] == 'overdue':
                dl0 = parse_dt(it['deadline'])
                if dl0 and (datetime.datetime.now() - dl0).days <= 3:
                    hot.append(it)
                continue
            dl = parse_dt(it['deadline'])
            if dl:
                sec = (dl - datetime.datetime.now()).total_seconds()
                if 0 <= sec <= 1800: hot.append(it)
        if not hot: return
        self.reminded.update(i['id'] for i in hot)
        today = datetime.date.today().isoformat()
        self.cfg['reminded'] = {i: today for i in self.reminded}
        try: json.dump(self.cfg, open(CFG, 'w', encoding='utf-8'), ensure_ascii=False)
        except Exception: pass
        try: winsound.MessageBeep(0x40)
        except Exception: pass
        self.popup(hot[:4])

    def popup(self, hot):
        p = tk.Toplevel(self.root); p.overrideredirect(True); p.attributes('-topmost', True)
        h = 46 + 26 * len(hot)
        p.geometry(f'{W + 24}x{h}+{max(self.root.winfo_x() - 12, 8)}+{self.root.winfo_y() + 70}')
        p.configure(bg='#2A1B1B')
        tk.Label(p, text='⏰  到期提醒', font=self.f_title, fg='#FFB4AE', bg='#2A1B1B',
                 anchor='w').pack(fill='x', padx=14, pady=(10, 4))
        for it in hot:
            t = it['text'][:30] + ('…' if len(it['text']) > 30 else '')
            tk.Label(p, text=f"· {t}（{rel_deadline(it['deadline']) or it['from']}）", font=self.f_small,
                     fg='#FFD9D6', bg='#2A1B1B', anchor='w').pack(fill='x', padx=14)
        tk.Label(p, text='点一下关闭', font=self.f_small, fg='#8C6B6B', bg='#2A1B1B').pack(pady=5)
        def close(e=None): p.destroy()
        p.bind('<Button-1>', close)
        for w in p.winfo_children(): w.bind('<Button-1>', close)
        p.after(20000, p.destroy)

    def quit(self):
        self.save_cfg(); self.root.destroy()


def already_running():
    """同一会话只让跑一个挂件，快捷方式点两下不会开出两个"""
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\gongzuo-jiaoban-widget')
        return ctypes.windll.kernel32.GetLastError() == 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def main():
    if already_running() and '--force' not in sys.argv:
        return
    root = tk.Tk(); root.title('工作交办挂件')
    Widget(root); root.mainloop()


if __name__ == '__main__':
    main()