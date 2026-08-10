"""Shared Jinja2 environment for the server-rendered pages."""

from __future__ import annotations

import json
from typing import Any

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

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


def attr_json(value: Any) -> Markup:
    """JSON for embedding in a **single-quoted** HTML attribute.

    Two things the stock ``|tojson`` filter will not do for us:

    * ``default=str`` — recommendation payloads carry datetimes, which plain
      ``tojson`` refuses outright.
    * escaping ``'`` — the output is full of double quotes (JSON string
      delimiters), so it can only live inside a single-quoted attribute, and a
      course title containing an apostrophe would otherwise close that attribute
      early. That failure mode is silent: the HTML still renders, Alpine just
      stops initialising and the component disappears.

    Always use it as ``x-data='comp({{ value | attr_json }})'`` — single quotes.
    """
    text = json.dumps(value, default=str)
    for raw, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("'", "\\u0027")):
        text = text.replace(raw, escaped)
    return Markup(text)


def static_url(path: str) -> str:
    """A /static URL stamped with the file's mtime.

    Without this, a returning visitor keeps whatever CSS and JS their browser
    cached — so a deploy that changes behaviour reaches new visitors only, and
    the old and new files disagree in ways that are very hard to reproduce.
    The stat is a few microseconds against a page render.
    """
    file = BASE_DIR / "app" / path.lstrip("/")
    try:
        version = int(file.stat().st_mtime)
    except OSError:
        return path
    return f"{path}?v={version}"


templates.env.filters["money"] = money
templates.env.filters["stars"] = stars
templates.env.filters["attr_json"] = attr_json
templates.env.globals["static_url"] = static_url
