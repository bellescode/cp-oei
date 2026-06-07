"""
dashboard/qr.py
CPOI Platform -- inline SVG QR codes for TOTP enrollment.

Uses qrcode's SVG path factory (no Pillow dependency). Returns an inline
<svg> string suitable for st.markdown(unsafe_allow_html=True). Falls back to
None if qrcode is unavailable, so callers can show the manual-entry secret.
"""

from __future__ import annotations

import io


def qr_svg(data: str, size_px: int = 200) -> str | None:
    """Return an inline SVG QR for the given data, or None on failure."""
    try:
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        svg = buf.getvalue().decode("utf-8")
        # Constrain rendered size; the factory emits its own width/height.
        return (
            f'<div style="width:{size_px}px;background:#fff;padding:8px;'
            f'border:1px solid #E6E1D5;border-radius:8px;">{svg}</div>'
        )
    except Exception:
        return None
