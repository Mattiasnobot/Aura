"""Optional network services, added without touching the core agent.

A service is a small object describing one capability that lives outside this
machine: the tool the model sees, the domains it needs, and a handler that turns
arguments into a plain result. `AuraAgent` reads this registry, so a new
integration is a new module plus one `register()` call — never an edit to the
tool loop.

Two rules hold for every service, and are enforced here rather than trusted to
each implementation:

* **It cannot open its own socket.** The handler is given a `fetch` callable
  rather than any networking of its own, so what it reads goes through Aura's
  one HTTP path — which refuses a name resolving onto the local network, and
  records every URL as a source. The domain allowlist that used to gate this
  was removed at Mat's request; the single path was always the load-bearing
  half, and it remains.
* **It reports what it read.** Every fetch is recorded as a source, and the
  reply cites it.

Written against the standard library only, in keeping with the rest of Aura.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import quote


class Fetch(Protocol):
    def __call__(self, url: str, timeout: float = 10.0) -> dict: ...


@dataclass(frozen=True)
class Service:
    """One outside-the-machine capability offered to the model as a tool."""

    name: str
    description: str
    domains: tuple[str, ...]
    parameters: dict
    required: tuple[str, ...]
    handler: Callable[[Fetch, dict], dict]
    #: One line about what the service reaches, shown in the network panel.
    #: It used to tell the user which domains to grant; there is nothing to
    #: grant now, so it describes rather than instructs.
    grant_hint: str = ""

    def tool_definition(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": dict(self.parameters),
                           "required": list(self.required), "additionalProperties": False}}}


_REGISTRY: dict[str, Service] = {}


def register(service: Service) -> Service:
    _REGISTRY[service.name] = service
    return service


def services() -> list[Service]:
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def get(name: str) -> Service | None:
    return _REGISTRY.get(str(name))


def domains() -> list[str]:
    """Every domain any registered service could need, for the Permissions UI."""
    seen: list[str] = []
    for service in services():
        for domain in service.domains:
            if domain not in seen:
                seen.append(domain)
    return seen


# --------------------------------------------------------------------- weather

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?name={name}&count=1&language=en&format=json"
FORECAST_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
                "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=2")

# Open-Meteo's documented codes, condensed to what a person would actually say.
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _decode(response: dict) -> dict:
    try:
        payload = json.loads(response.get("content") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("The weather service returned something unreadable.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("The weather service returned an unexpected shape.")
    return payload


def _weather(fetch: Fetch, arguments: dict) -> dict:
    place = str(arguments.get("place", "")).strip()
    if not place:
        raise ValueError("Name the place to look up.")
    # Percent-encoded, not merely space-substituted. Every Estonian place name
    # Mat is likely to ask about carries a letter that has no business going
    # raw into a URL — "Jõgeva" came back empty, and the model recovered by
    # guessing "Jogeva", which cost a round trip and only worked by luck.
    found = _decode(fetch(GEOCODE_URL.format(name=quote(place, safe="")), timeout=10.0))
    results = found.get("results") or []
    if not results:
        return {"ok": False, "error": f"No place called {place!r} was found."}
    first = results[0]
    report = _decode(fetch(FORECAST_URL.format(lat=first["latitude"], lon=first["longitude"]),
                           timeout=10.0))
    current = report.get("current") or {}
    daily = report.get("daily") or {}
    code = int(current.get("weather_code", -1))
    where = ", ".join(str(part) for part in
                      (first.get("name"), first.get("admin1"), first.get("country")) if part)
    return {
        "ok": True,
        "place": where,
        "observed": current.get("time"),
        "condition": WEATHER_CODES.get(code, "unknown conditions"),
        "temperature_c": current.get("temperature_2m"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
        "today_high_c": (daily.get("temperature_2m_max") or [None])[0],
        "today_low_c": (daily.get("temperature_2m_min") or [None])[0],
    }


register(Service(
    name="get_weather",
    description=("Look up current weather for a named place using the open, keyless "
                 "Open-Meteo service."),
    domains=("geocoding-api.open-meteo.com", "api.open-meteo.com"),
    parameters={"place": {"type": "string",
                          "description": "Town, city, or region name, e.g. 'Tartu'"}},
    required=("place",),
    handler=_weather,
    grant_hint="Reads geocoding-api.open-meteo.com and api.open-meteo.com.",
))
