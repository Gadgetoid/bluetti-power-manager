"""BLUETTI-style dashboard rendering for the Tufty 2350 (320x240 HIRES).

DSEG7 digits with ghosted unlit segments, a 10-segment charge ring (12 slices,
bottom two omitted for the charge time), and four corner power readouts.
"""
import math

badge.mode(HIRES)

def _load_font(name):
    for path in ("fonts/" + name, "/" + name, name):
        try:
            return font.load(path)
        except Exception:
            pass
    raise OSError(name + " not found")

DSEG = _load_font("dseg7-classic.af")   # 7-segment digits
UI = _load_font("coda-symbols.af")      # label text + Material Symbols

screen.antialias = image.X4
screen.fill_rule = image.EVEN_ODD

W, H = 320, 240
CX, CY = 160, 118
R_IN, R_OUT = 76, 84          # thin band; sized to clear the corner readouts
SLICE = 30.0
RING_FROM = 210.0             # lower-left; 12 slices, bottom two omitted
NSEG = 10
CUT_Y = CY + 53               # horizontal cut for the two gap-adjacent slices
CUT_HALF = 48                 # half-width of that cut; trims around the charge time
HOURS_SIZE = 24               # charge-time digits
ECO_SIZE = 18                 # the ECO indicator

ICON_SOLAR = "\uec0f"
ICON_ECO = "\uec1a"        # energy_savings_leaf
ICON_FAN = "\uf168"
ICON_SIZE = 21
BATT_SIZE = 22                # battery beside the charge time
# battery_android_frame_1..6 then _full; _1 is the emptiest
BATTERY = ("\uf257", "\uf256", "\uf255", "\uf254", "\uf253", "\uf252", "\uf24f")


def battery_glyph(soc):
    if soc >= 98:
        return BATTERY[6]
    return BATTERY[max(0, min(5, int(soc / 100 * 6)))]

# Palettes. Roles: bg, ink (lit digits), grey (labels), lit (charged ring
# slices), dim (uncharged slices), ghost (unlit "off" segments), accent.
# The last three borrow statsbadge's themes (statsbadge/themes.toml).
PALETTES = (
    ("BLUETTI", {
        "bg": (9, 15, 25), "ink": (240, 248, 255), "grey": (140, 158, 180),
        "lit": (45, 165, 255), "dim": (22, 50, 86), "ghost": (28, 42, 64),
        "accent": (70, 195, 255)}),
    ("LUMINESCENCE", {
        "bg": (158, 240, 206), "ink": (34, 60, 52), "grey": (86, 146, 126),
        "lit": (18, 96, 80), "dim": (124, 208, 176), "ghost": (136, 226, 192),
        "accent": (18, 96, 80)}),
    ("DARK", {
        "bg": (18, 20, 28), "ink": (242, 245, 255), "grey": (139, 147, 171),
        "lit": (56, 232, 209), "dim": (44, 51, 70), "ghost": (26, 30, 43),
        "accent": (56, 232, 209)}),
    ("LIGHT", {
        "bg": (250, 247, 242), "ink": (30, 26, 20), "grey": (102, 94, 82),
        "lit": (16, 145, 157), "dim": (216, 209, 195), "ghost": (240, 236, 228),
        "accent": (16, 145, 157)}),
)

LIT = DIM = INK = GREY = CYAN = GHOST = BG = None
theme_index = 0
theme_name = PALETTES[0][0]


def apply_theme(index):
    global LIT, DIM, INK, GREY, CYAN, GHOST, BG, theme_index, theme_name
    theme_index = index % len(PALETTES)
    theme_name, p = PALETTES[theme_index]
    BG = color.rgb(*p["bg"])
    INK = color.rgb(*p["ink"])
    GREY = color.rgb(*p["grey"])
    LIT = color.rgb(*p["lit"])
    DIM = color.rgb(*p["dim"])
    GHOST = color.rgb(*p["ghost"])
    CYAN = color.rgb(*p["accent"])


def cycle_theme(step):
    apply_theme(theme_index + step)
    return theme_name


apply_theme(0)
UI_CAP = 0.7275        # Coda cap height / point size
LABEL = 15             # label point size (thin font, so not too small)
H_SIZE = 14            # the "H" suffix on the charge time
LABEL_H = int(UI_CAP * LABEL)
LABEL_GAP = 6          # breathing room between a label and its digits
# DSEG glyphs carry more side bearing than Coda, so a label drawn at the same
# x as the digits sits proudly to their left. Nudge the label row inward to
# line them up.
LABEL_INSET = 3
# The top pair sit with their label row down beside the dial's widest point,
# where the linework's curl crosses inside the block, so whichever end faces
# the dial is held clear of it: the unit on a left-hand block, the label on a
# right-hand one. The bottom pair clear the curl on their own and just take
# the ink-alignment fudge, which keeps their unit right-aligned to the digits.
DIAL_CLEAR = 10

overlay = None
LINE_OFF = 31
RING_PAD = 5.0         # clearance between the dial and the linework
CURL = 30              # degrees the top linework wraps around the dial
CAP = 0.633            # DSEG cap height as a fraction of the point size

# The charge time sits centred in the opening below the dial: from the cut
# edge down to the outer edge of the linework ringing the dial.
HOURS_BOX_TOP = CUT_Y
HOURS_BOX_BOTTOM = CY + R_OUT + RING_PAD + 1.2
HOURS_LIFT = 4         # sits a shade above the box centre
HOURS_BASELINE = int((HOURS_BOX_TOP + HOURS_BOX_BOTTOM) / 2
                     + CAP * HOURS_SIZE / 2 - HOURS_LIFT)


def _y_for_cap_centre(cy, size):
    """Draw-y that puts the cap box's centre on cy."""
    return int(cy - size + CAP * size / 2)


def _y_for_baseline(baseline, size):
    return int(baseline - size)
          # centre divider lines above/below the number

def ui(txt, x, y, size, pen, align="l"):
    """Draw a label. `y` is the top of the cap box, not the em box."""
    screen.font = UI
    screen.pen = pen
    w = screen.measure_text(txt, size)[0]
    if align == "r": x -= w
    elif align == "c": x -= w // 2
    screen.text(txt, vec2(int(x), int(y - (1 - UI_CAP) * size)), size)
    return w

def _seg_w(txt, size):
    screen.font = DSEG
    return screen.measure_text("".join("8" if c not in " :." else c for c in txt), size)[0]

def seg(txt, x, y, size, pen, align="l"):
    ghost = "".join("8" if c not in " :." else c for c in txt)
    screen.font = DSEG
    w = screen.measure_text(ghost, size)[0]
    if align == "r": x -= w
    elif align == "c": x -= w // 2
    screen.pen = GHOST
    screen.text(ghost, vec2(int(x), int(y)), size)
    screen.pen = pen
    screen.text(txt, vec2(int(x), int(y)), size)
    return w

def draw_ring(fraction, head=None):
    global overlay
    if overlay is None:
        overlay = image(W, H)
    overlay.antialias = image.X4
    overlay.clear()
    filled = int(round(fraction * NSEG))
    inset = 2.0
    c = vec2(CX, CY)
    for i in range(NSEG):
        a0 = RING_FROM + SLICE * i + inset
        a1 = RING_FROM + SLICE * (i + 1) - inset
        lit = i < filled or (head is not None and i < head)
        overlay.pen = LIT if lit else DIM
        overlay.shape(shape.arc(c, R_IN, R_OUT, a0, a1))
    # flatten the two gap-adjacent slice bottoms with a feathered horizontal cut
    overlay.pen = brush.erase()
    # Deliberately narrower than the ring: the erase catches the two
    # gap-adjacent slices near their tips, mimicking the corner cut from the real panel.
    # Tucked in to CUT_HALF so it trims closely around the charge-time group.
    overlay.shape(shape.rectangle(CX - CUT_HALF, CUT_Y, CUT_HALF * 2, H - CUT_Y))
    screen.blit(overlay, vec2(0, 0))

def draw_hours(mins, soc, cx, y):
    size = HOURS_SIZE
    whole = "%02d" % int(mins // 60)
    frac = "%d" % int((mins % 60) * 10 // 60)
    glyph = battery_glyph(soc)
    screen.font = UI
    hw = screen.measure_text("H", H_SIZE)[0]
    bw = screen.measure_text(glyph, BATT_SIZE)[0]
    total = bw + 4 + _seg_w(whole, size) + 6 + _seg_w(frac, size) + 3 + hw
    x = cx - total // 2
    icon(glyph, x, y + size - BATT_SIZE, BATT_SIZE, CYAN, "l")
    x += bw + 4
    x += seg(whole, x, y, size, CYAN, "l")
    screen.pen = CYAN
    screen.shape(shape.rectangle(x + 1, y + size - 6, 3.5, 3.5))
    x += 6
    x += seg(frac, x, y, size, CYAN, "l")
    ui("H", x + 3, int(y + size - UI_CAP * H_SIZE), H_SIZE, CYAN, "l")

VAL_SIZE = 46

def readout(name, value, ax, y, side, label_below=False):
    """One corner readout. `y` is the top of the block.

    `label_below` puts the name and unit under the digits instead of over
    them, which is what the top pair want: digits out at the screen edge,
    label turned inward.
    """
    s = "%04d" % value
    dw = _seg_w(s, VAL_SIZE)
    left = ax if side == "l" else ax - dw     # block left edge
    right = left + dw                          # block right edge
    if label_below:
        y_digits = y
        y_label = int(y + CAP * VAL_SIZE + LABEL_GAP)
    else:
        y_label = y
        y_digits = int(y + LABEL_H + LABEL_GAP)
    clear = DIAL_CLEAR if label_below else LABEL_INSET
    if side == "l":
        name_x, unit_x = left + LABEL_INSET, right - clear
    else:
        name_x, unit_x = left + clear, right - LABEL_INSET
    ui(name, name_x, y_label, LABEL, GREY, "l")
    ui("W", unit_x, y_label, LABEL, GREY, "r")
    seg(s, left, int(y_digits - (1 - CAP) * VAL_SIZE), VAL_SIZE, INK, "l")

def draw_ac_dc(cx, y, dc_on, ac_on):
    ui("DC", cx - 17, y, LABEL, CYAN if dc_on else GHOST, "c")
    ui("AC", cx + 17, y, LABEL, CYAN if ac_on else GHOST, "c")

def draw_percent(x, y, w, h, pen):
    d = w * 0.48                      # dot box
    r = d * 0.34                      # corner radius
    t = d * 0.30                      # wall thickness
    boxes = ((x, y), (x + w - d, y + h - d))
    screen.pen = pen
    screen.shape(shape.line(x + w - d * 0.3, y + d * 0.3,
                            x + d * 0.3, y + h - d * 0.3, w * 0.12))
    for bx, by in boxes:
        screen.shape(shape.rounded_rectangle(bx, by, d, d, r, r, r, r))
    screen.pen = BG
    ri = max(0.6, r - t)
    for bx, by in boxes:
        screen.shape(shape.rounded_rectangle(bx + t, by + t, d - 2 * t, d - 2 * t,
                                             ri, ri, ri, ri))

def icon(ch, x, y, size, pen, align="l"):
    """Draw a Material Symbol. Icons are fitted to a box by the font builder,
    so unlike text they need no cap-height correction."""
    screen.font = UI
    screen.pen = pen
    w = screen.measure_text(ch, size)[0]
    if align == "r":
        x -= w
    elif align == "c":
        x -= w // 2
    screen.text(ch, vec2(int(x), int(y)), size)
    return w


def draw_status_icons(y, solar_on, eco_on, fan_on):
    screen.font = UI
    gap = 8
    widths = [screen.measure_text(ch, ICON_SIZE)[0]
              for ch in (ICON_SOLAR, ICON_ECO, ICON_FAN)]
    x = CX - (sum(widths) + gap * 2) // 2
    for ch, on, w in zip((ICON_SOLAR, ICON_ECO, ICON_FAN),
                         (solar_on, eco_on, fan_on), widths):
        icon(ch, x, y, ICON_SIZE, CYAN if on else GHOST, "l")
        x += w + gap


def draw_center_lines():
    """Lines in from the screen edges that stop clear of the dial and curl
    around it, one way only: the top line toward the top, the bottom line
    toward the bottom.

    The linework rides on its own radius (RING_PAD clear of the dial), and the
    horizontal runs stop where that radius crosses their height, so line and
    curl meet instead of the line butting into the ring.
    """
    screen.pen = INK
    c = vec2(CX, CY)
    r_mid = R_OUT + RING_PAD
    r0, r1 = r_mid - 1.2, r_mid + 1.2
    # The bottom curls run round until they meet the cut-out's side edge, at
    # x = CX +/- CUT_HALF. On this radius that is where sin(theta) = CUT_HALF/r,
    # taken past the vertical so it lands below the cut's top edge.
    cut_sin = max(-1.0, min(1.0, CUT_HALF / r_mid))
    th_cut = 180.0 - math.degrees(math.asin(cut_sin))
    for dy in (-LINE_OFF, LINE_OFF):
        y = CY + dy
        ox = (r_mid * r_mid - dy * dy) ** 0.5
        screen.shape(shape.line(5, y, CX - ox, y, 2.4))
        screen.shape(shape.line(CX + ox, y, W - 5, y, 2.4))
        th_right = math.degrees(math.atan2(ox, -dy))
        # Top: run out to the outer edge of the top pair of slices. They
        # straddle the top, one SLICE each, so those edges sit at +/-SLICE.
        # Bottom: run round to the cut-out.
        curl = max(0.0, (th_right - SLICE) if dy < 0 else (th_cut - th_right))
        for sx in (1, -1):
            th = math.degrees(math.atan2(sx * ox, -dy)) % 360
            if dy < 0:                       # top line: curl toward the top
                a0, a1 = (th - curl, th) if sx == 1 else (th, th + curl)
                tip = a0 if sx == 1 else a1
            else:                            # bottom line: curl down to the cut
                a0, a1 = (th, th + curl) if sx == 1 else (th - curl, th)
                tip = a1 if sx == 1 else a0
            screen.shape(shape.arc(c, r0, r1, a0, a1))
            # round off the free end (the one away from the horizontal run)
            t = math.radians(tip)
            screen.shape(shape.circle(CX + r_mid * math.sin(t),
                                      CY - r_mid * math.cos(t),
                                      (r1 - r0) / 2))

def render(d, head=None, busy=False):
    badge.clear()
    screen.antialias = image.X4
    # Even-odd is what cuts the bolt out of the leaf and the level bar out of
    # the battery frame; non-zero floods both into solid blobs.
    screen.fill_rule = image.EVEN_ODD
    screen.pen = BG
    screen.shape(shape.rectangle(0, 0, W, H))
    draw_ring(d["soc"] / 100.0, head)
    draw_status_icons(4, d.get("dc_in", 0) > 0,
                      d.get("eco_ac", 0) or d.get("eco_dc", 0), False)
    draw_center_lines()
    ui("ECO", CX, CY - 52, LABEL, CYAN, "c")
    num_size = 80
    num_y = _y_for_cap_centre(CY, num_size)
    nw = seg("%d" % d.get("soc", 0), CX, num_y, num_size, INK, "c")
    draw_percent(CX + nw // 2 + 6, num_y + num_size - 21, 15, 21, GREY)
    draw_hours(d.get("mins", 0), d.get("soc", 0), CX,
               _y_for_baseline(HOURS_BASELINE, HOURS_SIZE))
    draw_ac_dc(CX, 209, d.get("dc_on", 0), d.get("ac_on", 0))
    if busy:
        screen.pen = CYAN
        screen.shape(shape.circle(CX, 232, 2.5))
    readout("DC INPUT",  d.get("dc_in", 0),  8,   8, "l", label_below=True)
    readout("DC OUTPUT", d.get("dc_out", 0), 8,   187, "l")
    readout("AC INPUT",  d.get("ac_in", 0),  312, 8, "r", label_below=True)
    readout("AC OUTPUT", d.get("ac_out", 0), 312, 187, "r")


def render_connecting():
    badge.clear()
    screen.antialias = image.X4
    screen.fill_rule = image.EVEN_ODD
    screen.pen = BG
    screen.shape(shape.rectangle(0, 0, W, H))
    draw_ring(0.0)
    ui("CONNECTING", CX, CY - 4, LABEL, CYAN, "c")
