"""Conservative realtime disruption checks using the public Transitous API."""

from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


API_BASE = "https://api.transitous.org/api"
TIMEZONE_NAME = "Europe/Berlin"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
USER_AGENT = "local-monitor/1.1 (+https://github.com/cretasico/local-monitor)"
TIMEOUT_SECONDS = 8


class TransitousError(RuntimeError):
    pass


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise TransitousError(f"HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TransitousError("conexión no disponible") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TransitousError("respuesta no válida") from None


def _normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _geocode_stop(name: str) -> dict:
    query = urlencode(
        {"text": name, "type": "STOP", "numResults": 10, "language": "de"}
    )
    results = fetch_json(f"{API_BASE}/v1/geocode?{query}")
    if not isinstance(results, list):
        raise TransitousError("respuesta de geocodificación no válida")

    exact_matches = [
        item
        for item in results
        if isinstance(item, dict)
        and item.get("type") == "STOP"
        and isinstance(item.get("name"), str)
        and _normalized_name(item["name"]) == _normalized_name(name)
        and isinstance(item.get("id"), str)
    ]
    if not exact_matches:
        raise TransitousError("no se encontró una parada exacta")
    exact_matches.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
    if (
        len(exact_matches) > 1
        and exact_matches[0].get("score") == exact_matches[1].get("score")
        and exact_matches[0]["id"] != exact_matches[1]["id"]
    ):
        raise TransitousError("la parada es ambigua")
    return exact_matches[0]


def _same_stop(place: object, stop: dict) -> bool:
    if not isinstance(place, dict):
        return False
    if any(
        place.get(identifier) == stop["id"]
        for identifier in ("id", "stopId", "parentId")
    ):
        return True
    return (
        isinstance(place.get("name"), str)
        and _normalized_name(place["name"]) == _normalized_name(stop["name"])
    )


def _is_transit_leg(leg: object) -> bool:
    return isinstance(leg, dict) and isinstance(leg.get("tripId"), str) and bool(
        leg["tripId"]
    )


def _visits_stop(legs: list[dict], stop: dict) -> bool:
    for leg in legs:
        places = [leg.get("from"), leg.get("to")]
        intermediate = leg.get("intermediateStops")
        if isinstance(intermediate, list):
            places.extend(intermediate)
        if any(_same_stop(place, stop) for place in places):
            return True
    return False


def _transfers_at_stop(legs: list[dict], stop: dict) -> bool:
    transit_legs = [leg for leg in legs if _is_transit_leg(leg)]
    return any(
        _same_stop(previous.get("to"), stop)
        and _same_stop(current.get("from"), stop)
        and current.get("interlineWithPreviousLeg") is False
        and previous.get("tripId") != current.get("tripId")
        for previous, current in zip(transit_legs, transit_legs[1:])
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TransitousError("hora de transporte no válida")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TransitousError("hora de transporte no válida") from error
    timezone = ZoneInfo(TIMEZONE_NAME)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _incident_lines(itinerary: dict) -> list[str]:
    legs = itinerary.get("legs")
    if not isinstance(legs, list):
        raise TransitousError("itinerario no válido")

    incidents = []
    reported = set()
    for leg in legs:
        if not _is_transit_leg(leg):
            continue
        if leg.get("cancelled") is True:
            incident = "cancelado"
        elif leg.get("realTime") is True:
            scheduled_start = _parse_timestamp(leg.get("scheduledStartTime"))
            actual_start = _parse_timestamp(leg.get("startTime"))
            scheduled_end = _parse_timestamp(leg.get("scheduledEndTime"))
            actual_end = _parse_timestamp(leg.get("endTime"))
            delay_seconds = max(
                (actual_start - scheduled_start).total_seconds(),
                (actual_end - scheduled_end).total_seconds(),
            )
            if delay_seconds <= 0:
                continue
            incident = f"retraso de {math.ceil(delay_seconds / 60)} min"
        else:
            continue

        route = leg.get("displayName") or leg.get("routeShortName") or leg.get("mode")
        if not isinstance(route, str) or not route:
            route = "Servicio"
        scheduled_time = _parse_timestamp(leg.get("scheduledStartTime")).strftime(
            "%H:%M"
        )
        key = (leg["tripId"], scheduled_time, incident)
        if key in reported:
            continue
        reported.add(key)
        incidents.append(f"• {route} · salida {scheduled_time}: {incident}")
    return incidents


def _temporary_location_active(settings: dict[str, str], today: date) -> bool:
    temporary_values = [
        settings.get("WEATHER_TEMP_CITY", "").strip(),
        settings.get("WEATHER_TEMP_COUNTRY_CODE", "").strip(),
        settings.get("WEATHER_TEMP_EXPIRES_ON", "").strip(),
    ]
    if not any(temporary_values):
        return False
    if not all(temporary_values):
        raise TransitousError("configuración de ubicación temporal incompleta")
    try:
        expires_on = date.fromisoformat(temporary_values[2])
    except ValueError as error:
        raise TransitousError("fecha de ubicación temporal no válida") from error
    return today <= expires_on


def _scheduled_departure(settings: dict[str, str], now: datetime | None) -> datetime | None:
    enabled = settings.get("TRANSIT_ENABLED", "false").strip().lower()
    if enabled not in {"true", "false"}:
        raise TransitousError("estado del transporte no válido")
    if enabled == "false":
        return None
    timezone = ZoneInfo(TIMEZONE_NAME)
    current = (now or datetime.now(timezone)).astimezone(timezone)
    if _temporary_location_active(settings, current.date()):
        return None

    days = {
        day.strip()
        for day in settings.get("TRANSIT_DAYS", "").split(",")
        if day.strip()
    }
    if not days or days - set(WEEKDAYS):
        raise TransitousError("días de consulta no válidos")
    if WEEKDAYS[current.weekday()] not in days:
        return None

    departure_text = settings.get("TRANSIT_DEPARTURE_TIME", "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", departure_text):
        raise TransitousError("hora de salida no válida")
    hour, minute = map(int, departure_text.split(":"))
    departure = datetime.combine(current.date(), time(hour, minute), timezone)
    return departure if current < departure else None


def transit_incidents(
    settings: dict[str, str], now: datetime | None = None
) -> list[str]:
    departure = _scheduled_departure(settings, now)
    if departure is None:
        return []

    origin_name = settings.get("TRANSIT_ORIGIN", "").strip()
    destination_name = settings.get("TRANSIT_DESTINATION", "").strip()
    if not origin_name or not destination_name:
        raise TransitousError("origen o destino no configurado")
    origin = _geocode_stop(origin_name)
    destination = _geocode_stop(destination_name)

    via_name = settings.get("TRANSIT_VIA_STATION", "").strip()
    via_mode = settings.get("TRANSIT_VIA_MODE", "").strip()
    if via_name:
        if via_mode not in {"pass_through", "transfer"}:
            raise TransitousError("modo de estación intermedia no válido")
        via = _geocode_stop(via_name)
    else:
        if via_mode:
            raise TransitousError("modo intermedio configurado sin estación")
        via = None

    params = {
        "fromPlace": origin["id"],
        "toPlace": destination["id"],
        "time": departure.isoformat(timespec="seconds"),
        "arriveBy": "false",
        "realtimeMode": "REALTIME_ANNOTATION_ONLY",
        "searchWindow": 900,
        "numItineraries": 5,
    }
    if via is not None:
        params["via"] = via["id"]
        params["viaMinimumStay"] = "0" if via_mode == "pass_through" else "5"
        params["joinInterlinedLegs"] = "false"
    plan = fetch_json(f"{API_BASE}/v6/plan?{urlencode(params)}")
    if not isinstance(plan, dict) or not isinstance(plan.get("itineraries"), list):
        raise TransitousError("respuesta de planificación no válida")

    for itinerary in plan["itineraries"]:
        if not isinstance(itinerary, dict) or not isinstance(itinerary.get("legs"), list):
            continue
        legs = itinerary["legs"]
        if via is not None:
            if via_mode == "transfer" and not _transfers_at_stop(legs, via):
                continue
            if via_mode == "pass_through" and not _visits_stop(legs, via):
                continue
        return _incident_lines(itinerary)
    return []


def safe_transport_incidents(
    settings: dict[str, str], now: datetime | None = None
) -> list[str]:
    try:
        return transit_incidents(settings, now)
    except (TransitousError, KeyError, TypeError, ValueError):
        print("Transporte no disponible; se omite este apartado", file=sys.stderr)
        return []
