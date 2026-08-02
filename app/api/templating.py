"""Shared Jinja2 environment for the server-rendered pages."""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, settings

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
templates.env.globals["app_name"] = settings.app_name


def money(value: float, currency: str = "USD") -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(currency, "")
    if not value:
        return "Free"
    return f"{symbol}{value:,.0f}" if symbol else f"{value:,.0f} {currency}"


def stars(rating: float) -> str:
    full = int(round(rating))
    return "★" * full + "☆" * (5 - full)


templates.env.filters["money"] = money
templates.env.filters["stars"] = stars
