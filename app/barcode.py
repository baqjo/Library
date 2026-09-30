"""Code 39 barcode (the default symbology in Follett Destiny labels) -> SVG.

Pattern strings: 9 elements (bar,space,bar,...) where n = narrow, w = wide.
Scanner guns in keyboard-wedge mode type the decoded text followed by Enter;
some emit the '*' start/stop characters, which normalize_scan() strips.
"""
PATTERNS = {
 "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn", "4": "nnnwwnnnw",
 "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw", "8": "wnnwnnwnn", "9": "nnwwnnwnn",
 "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn",
 "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
 "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww", "O": "wnnnwnnwn",
 "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
 "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn",
 "Z": "nwwnwnnnn", "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn",
 "$": "nwnwnwnnn", "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn",
}
WIDE = 3  # wide:narrow ratio
QUIET = 10  # quiet zone in narrow units


def normalize_scan(raw):
    """Clean text coming from a scanner gun / typed input."""
    v = (raw or "").strip().upper()
    if len(v) > 2 and v.startswith("*") and v.endswith("*"):
        v = v[1:-1]
    return v


def is_valid(value):
    return bool(value) and all(c in PATTERNS and c != "*" for c in value)


def bars(value):
    """Return (list of (x, width) bars, total width in narrow units)."""
    value = value.upper()
    if not is_valid(value):
        raise ValueError("Unsupported character for Code 39")
    x = QUIET
    out = []
    for ci, ch in enumerate("*" + value + "*"):
        if ci:
            x += 1  # inter-character narrow gap
        for i, e in enumerate(PATTERNS[ch]):
            w = WIDE if e == "w" else 1
            if i % 2 == 0:
                out.append((x, w))
            x += w
    return out, x + QUIET


def svg(value, height=48, text=True, label=None):
    """SVG with bars and the human-readable number underneath (as on Follett labels)."""
    value = value.upper()
    b, width = bars(value)
    th = 13 if text else 0
    rects = "".join(f'<rect x="{x}" y="0" width="{w}" height="{height}"/>' for x, w in b)
    txt = ""
    if text:
        txt = (f'<text x="{width/2}" y="{height + 11}" text-anchor="middle" '
               f'font-family="Arial,Helvetica,sans-serif" font-size="11" fill="#000">{label or value}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height + th}" '
            f'preserveAspectRatio="xMidYMid meet" shape-rendering="crispEdges" role="img" '
            f'aria-label="{value}"><rect width="{width}" height="{height + th}" fill="#fff"/>'
            f'<g fill="#000">{rects}</g>{txt}</svg>')
