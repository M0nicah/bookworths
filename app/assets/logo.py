"""The Bookworths mark, as inline SVG.

Rebuilt as vector rather than embedding the supplied PNG: the artwork ships on
a dark background that would show as a rectangle on the cream page, and a
raster mark blurs on retina displays and in print. As SVG it stays sharp at any
size, carries no background, and takes its colours from the theme — so the mark
matches the charts instead of approximating them.

The device is an open book whose pages rise into bars: the same idea the app
runs on, that a statement becomes a picture of a business.
"""
from __future__ import annotations

from . import __name__ as _pkg  # noqa: F401  (marks this a package module)

from ..reports import theme


def logo_svg(size: int = 44, dark: bool = False) -> str:
    """Return the mark as an inline SVG string.

    `dark=True` returns the light-on-dark variant for the sidebar.
    """
    bar_top = "#FFFFFF" if dark else theme.CARD
    bar_bottom = theme.SIDEBAR_SUCCESS if dark else theme.MONEY_IN
    page = theme.SIDEBAR_SUCCESS if dark else theme.MONEY_IN
    page_deep = "#3F9E6B" if dark else "#17603C"
    uid = "d" if dark else "l"

    return f"""<svg viewBox="0 0 120 108" width="{size}" height="{int(size * 0.9)}"
     xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Bookworths">
  <defs>
    <linearGradient id="bar{uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{bar_top}"/>
      <stop offset="100%" stop-color="{bar_bottom}"/>
    </linearGradient>
    <linearGradient id="page{uid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{page}"/>
      <stop offset="100%" stop-color="{page_deep}"/>
    </linearGradient>
  </defs>
  <!-- Rising bars: the pages becoming a chart. -->
  <path d="M34 62 V40 l11 -6 V62 Z" fill="url(#bar{uid})" opacity=".72"/>
  <path d="M50 62 V22 l12 -7 V62 Z" fill="url(#bar{uid})"/>
  <path d="M67 62 V34 l11 -6 V62 Z" fill="url(#bar{uid})" opacity=".85"/>
  <path d="M83 62 V12 l12 -7 V62 Z" fill="url(#bar{uid})"/>
  <!-- Open book: two leaves meeting at the spine. -->
  <path d="M58 66 C46 56 30 54 12 57 L4 78 C24 74 44 76 58 86 Z"
        fill="url(#page{uid})"/>
  <path d="M62 66 C74 56 90 54 108 57 L116 78 C96 74 76 76 62 86 Z"
        fill="url(#page{uid})"/>
  <path d="M58 78 C46 69 30 67 14 69 L11 78 C27 76 44 78 58 86 Z"
        fill="{page_deep}" opacity=".55"/>
  <path d="M62 78 C74 69 90 67 106 69 L109 78 C93 76 76 78 62 86 Z"
        fill="{page_deep}" opacity=".55"/>
  <path d="M60 88 c-3 0 -5 2 -5 5 h10 c0 -3 -2 -5 -5 -5 Z" fill="url(#page{uid})"/>
</svg>"""


def favicon_svg() -> str:
    """A compact data-URI favicon, so the browser tab carries the mark."""
    import base64

    svg = logo_svg(size=64, dark=True)
    encoded = base64.b64encode(svg.encode()).decode()
    return f"data:image/svg+xml;base64,{encoded}"
