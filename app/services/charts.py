"""Tiny server-side chart geometry (rendered as inline SVG by templates)."""


def line_chart(values, width: int = 640, height: int = 170, pad: int = 18):
    nums = [float(v) for v in values]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    n = len(nums)
    dots = []
    for i, v in enumerate(nums):
        x = pad + (width - 2 * pad) * (i / (n - 1) if n > 1 else 0.5)
        frac = (v - lo) / (hi - lo) if hi != lo else 0.5
        y = height - pad - (height - 2 * pad) * frac
        dots.append((round(x, 1), round(y, 1)))
    return {
        "width": width,
        "height": height,
        "points": " ".join(f"{x},{y}" for x, y in dots),
        "dots": dots,
        "min": lo,
        "max": hi,
    }
