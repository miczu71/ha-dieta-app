"""Wykres wagi jako SVG renderowany po stronie serwera (bez JS)."""

from __future__ import annotations

from datetime import date

W, H = 600, 180
PAD_L, PAD_R, PAD_T, PAD_B = 40, 12, 12, 24


def _path(pts: list[tuple[float, float]]) -> str:
    return " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))


def weight_chart(points: list[tuple[date, float]], trend: list[tuple[date, float]]) -> str:
    """SVG: punkty wagi + linia średniej 7-dniowej; pusty ciąg, gdy brak danych."""
    if not points:
        return ""
    days = [p[0] for p in points]
    d0, d1 = min(days), max(days)
    span = max((d1 - d0).days, 1)
    values = [p[1] for p in points] + [t[1] for t in trend]
    lo, hi = min(values), max(values)
    if hi - lo < 1.0:
        mid = (hi + lo) / 2
        lo, hi = mid - 0.5, mid + 0.5
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def xy(d: date, v: float) -> tuple[float, float]:
        return PAD_L + pw * (d - d0).days / span, PAD_T + ph * (1 - (v - lo) / (hi - lo))

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="wc-dot"/>' for x, y in (xy(d, v) for d, v in points)
    )
    line = f'<path d="{_path([xy(d, v) for d, v in trend])}" class="wc-line" fill="none"/>' if trend else ""
    labels = (
        f'<text x="{PAD_L - 6}" y="{PAD_T + 4}" class="wc-t" text-anchor="end">{hi:.1f}</text>'
        f'<text x="{PAD_L - 6}" y="{H - PAD_B}" class="wc-t" text-anchor="end">{lo:.1f}</text>'
        f'<text x="{PAD_L}" y="{H - 6}" class="wc-t">{d0.strftime("%d.%m")}</text>'
        f'<text x="{W - PAD_R}" y="{H - 6}" class="wc-t" text-anchor="end">{d1.strftime("%d.%m")}</text>'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" class="wc" role="img" aria-label="Wykres wagi z średnią 7 dni">'
        f'<line x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - PAD_R}" y2="{H - PAD_B}" class="wc-ax"/>'
        f"{line}{dots}{labels}</svg>"
    )
