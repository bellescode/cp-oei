"""
dashboard/theme.py
CPOI Platform -- Criterion Partners brand system for the Streamlit UI.

Single source of truth for brand colors, fonts, the logo lockup, and the
shared CSS injected into every page. Import and call inject_brand_css()
once per render (cheap and idempotent), then use the helpers to render
branded headers, hero login shells, classification badges, and metric
cards.

Brand DNA
  Navy   #2E4A6A   primary
  Gold   #C9A455   accent / interactive
  White  #FFFFFF
  Fonts  Cormorant Garamond (display headings), DM Sans (body)
"""

from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Brand constants
# ---------------------------------------------------------------------------

NAVY = "#2E4A6A"
NAVY_DARK = "#243B56"
GOLD = "#C9A455"
GOLD_SOFT = "#E7D9B4"
CREAM = "#F5F2EA"
WHITE = "#FFFFFF"
INK = "#1F2A37"
MUTED = "#6B7280"

# Risk-band palette (text + background) used across MP and client views.
BAND_COLORS: dict[str, str] = {
    "Low Risk":  "#2E862E",
    "Moderate":  "#856A00",
    "Elevated":  "#CC7700",
    "High Risk": "#B52A1C",
    "Critical":  "#7B0A02",
}
BAND_BG: dict[str, str] = {
    "Low Risk":  "#E8F5E9",
    "Moderate":  "#FFFDE7",
    "Elevated":  "#FFF3E0",
    "High Risk": "#FCE4E4",
    "Critical":  "#F8D7DA",
}


# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------

_BRAND_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=DM+Sans:wght@400;500;700&display=swap');

:root {{
  --cp-navy: {NAVY};
  --cp-gold: {GOLD};
  --cp-cream: {CREAM};
  --cp-ink: {INK};
}}

html, body, [class*="css"], .stApp, .stMarkdown, p, span, div, label, input, button {{
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}}

h1, h2, h3, h4, .cp-display {{
  font-family: 'Cormorant Garamond', Georgia, serif !important;
  color: var(--cp-navy);
  letter-spacing: 0.2px;
}}
h1 {{ font-weight: 700; }}

/* Tighten Streamlit's default top padding */
.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1300px; }}

/* Primary buttons in brand gold */
.stButton > button[kind="primary"], .stFormSubmitButton > button {{
  background: var(--cp-navy);
  color: {WHITE};
  border: 1px solid var(--cp-navy);
  border-radius: 6px;
  font-weight: 600;
}}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {{
  background: {NAVY_DARK};
  border-color: {NAVY_DARK};
}}
.stButton > button[kind="secondary"] {{
  border: 1px solid var(--cp-gold);
  color: var(--cp-navy);
  border-radius: 6px;
  font-weight: 600;
}}

/* Sidebar */
[data-testid="stSidebar"] {{
  background: var(--cp-navy);
}}
[data-testid="stSidebar"] * {{ color: #EAF0F7; }}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{
  color: {WHITE} !important;
}}
[data-testid="stSidebarNav"] a span {{ color: #EAF0F7 !important; }}

/* Brand header bar */
.cp-topbar {{
  display: flex; align-items: center; justify-content: space-between;
  border-bottom: 3px solid var(--cp-gold);
  padding-bottom: 14px; margin-bottom: 18px;
}}
.cp-logo {{ display: flex; align-items: center; gap: 12px; }}
.cp-logo-mark {{
  background: var(--cp-navy); color: {WHITE};
  font-family: 'Cormorant Garamond', serif; font-weight: 700;
  font-size: 22px; line-height: 1; letter-spacing: 1px;
  border: 2px solid var(--cp-gold);
  padding: 10px 12px; border-radius: 4px;
}}
.cp-logo-word {{ font-family: 'Cormorant Garamond', serif; }}
.cp-logo-word .l1 {{
  font-size: 19px; font-weight: 700; color: var(--cp-navy);
  letter-spacing: 3px; line-height: 1.05;
}}
.cp-logo-word .l2 {{ font-size: 11px; color: var(--cp-gold); letter-spacing: 4px; }}
.cp-topbar-tag {{ color: {MUTED}; font-size: 13px; text-align: right; }}

/* Page title block */
.cp-page-title {{ font-family:'Cormorant Garamond',serif; font-size: 34px;
  font-weight: 700; color: var(--cp-navy); margin: 0; }}
.cp-page-sub {{ color: {MUTED}; font-size: 14px; margin-top: 2px; }}

/* Cards */
.cp-card {{
  background: {WHITE}; border: 1px solid #E6E1D5; border-left: 4px solid var(--cp-gold);
  border-radius: 8px; padding: 16px 18px; margin-bottom: 12px;
  box-shadow: 0 1px 2px rgba(31,42,55,0.04);
}}
.cp-stat {{ font-family:'Cormorant Garamond',serif; font-size: 40px; font-weight:700;
  color: var(--cp-navy); line-height: 1; }}
.cp-stat-label {{ color:{MUTED}; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }}

/* Hero (login) */
.cp-hero {{
  background: linear-gradient(135deg, {NAVY} 0%, {NAVY_DARK} 100%);
  border-radius: 12px; padding: 34px 38px; color: {WHITE};
  border-top: 4px solid var(--cp-gold); margin-bottom: 6px;
}}
.cp-hero h1 {{ color: {WHITE} !important; font-size: 34px; margin: 0 0 6px; }}
.cp-hero p {{ color: {GOLD_SOFT}; font-size: 15px; margin: 0; max-width: 560px; }}

.cp-badge {{ padding: 3px 10px; border-radius: 4px; font-size: 0.82em; font-weight: 600; }}

footer, #MainMenu {{ visibility: hidden; }}
</style>
"""


def inject_brand_css() -> None:
    """Inject the Criterion Partners CSS. Safe to call once per page render."""
    st.markdown(_BRAND_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Reusable brand fragments
# ---------------------------------------------------------------------------

def logo_lockup_html() -> str:
    """Return the CP logo lockup as HTML (CSS-rendered, no image asset needed)."""
    return (
        '<div class="cp-logo">'
        '<div class="cp-logo-mark">CP</div>'
        '<div class="cp-logo-word">'
        '<div class="l1">CRITERION</div>'
        '<div class="l2">PARTNERS</div>'
        '</div></div>'
    )


def top_bar(tagline: str = "Operational Executive Intelligence") -> None:
    """Render the branded top bar with logo and a right-aligned tagline."""
    st.markdown(
        f'<div class="cp-topbar">{logo_lockup_html()}'
        f'<div class="cp-topbar-tag">{tagline}</div></div>',
        unsafe_allow_html=True,
    )


def page_title(title: str, subtitle: str | None = None) -> None:
    """Render a branded page title block (Cormorant heading + muted subtitle)."""
    html = f'<div class="cp-page-title">{title}</div>'
    if subtitle:
        html += f'<div class="cp-page-sub">{subtitle}</div>'
    st.markdown(html, unsafe_allow_html=True)
    st.write("")


def badge(label: str, palette: str = "band") -> str:
    """Return an inline HTML badge. palette='band' uses risk-band colors."""
    if palette == "band":
        fg = WHITE
        bg = BAND_COLORS.get(label, NAVY)
    else:
        fg = NAVY
        bg = GOLD_SOFT
    return f'<span class="cp-badge" style="background:{bg};color:{fg};">{label}</span>'


def stat_card(label: str, value, accent: str | None = None) -> None:
    """Render a single branded stat card."""
    border = accent or GOLD
    st.markdown(
        f'<div class="cp-card" style="border-left-color:{border};">'
        f'<div class="cp-stat-label">{label}</div>'
        f'<div class="cp-stat">{value}</div></div>',
        unsafe_allow_html=True,
    )
