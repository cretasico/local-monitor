"""Validate the Obsidian monitor settings and update the private local env file."""

from __future__ import annotations

import os
import re
import shlex
import stat
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

VAULT = Path.home() / "Documents" / "Obsidian" / "my_brain"
CONFIG_NOTE = (
    VAULT
    / "my_obsidian"
    / "200_Projects"
    / "202_Raspberrypi"
    / "Monitor local - configuración.md"
)
ENV_FILE = Path.home() / ".config" / "local-monitor" / "monitor.env"
MANAGED_KEYS = (
    "WEATHER_CITY",
    "WEATHER_COUNTRY_CODE",
    "WEATHER_HOME_CITY",
    "WEATHER_HOME_COUNTRY_CODE",
    "WEATHER_TEMP_CITY",
    "WEATHER_TEMP_COUNTRY_CODE",
    "WEATHER_TEMP_EXPIRES_ON",
    "TRANSIT_ENABLED",
    "TRANSIT_ORIGIN",
    "TRANSIT_DESTINATION",
    "TRANSIT_VIA_STATION",
    "TRANSIT_VIA_MODE",
    "TRANSIT_DAYS",
    "TRANSIT_DEPARTURE_TIME",
    "REPORT_LANGUAGE",
    "REPORT_VERBOSITY",
)
ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
DEFAULT_VALUES = {
    "WEATHER_CITY": "",
    "WEATHER_COUNTRY_CODE": "DE",
    "WEATHER_HOME_CITY": "",
    "WEATHER_HOME_COUNTRY_CODE": "DE",
    "WEATHER_TEMP_CITY": "",
    "WEATHER_TEMP_COUNTRY_CODE": "",
    "WEATHER_TEMP_EXPIRES_ON": "",
    "TRANSIT_ENABLED": "false",
    "TRANSIT_ORIGIN": "",
    "TRANSIT_DESTINATION": "",
    "TRANSIT_VIA_STATION": "",
    "TRANSIT_VIA_MODE": "",
    "TRANSIT_DAYS": "",
    "TRANSIT_DEPARTURE_TIME": "",
    "REPORT_LANGUAGE": "es",
    "REPORT_VERBOSITY": "alert_adaptive",
}


class ConfigError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: UniqueKeyLoader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(
                "while constructing a configuration map",
                node.start_mark,
                "configuration keys must be strings",
                key_node.start_mark,
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a configuration map",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} debe ser un mapa YAML")
    missing = keys - value.keys()
    unknown = value.keys() - keys
    if missing or unknown:
        details = []
        if missing:
            details.append(f"faltan campos: {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"campos no admitidos: {', '.join(sorted(unknown))}")
        raise ConfigError(f"{path}: {'; '.join(details)}")
    return value


def _text(value: Any, path: str, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{path} debe ser texto")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ConfigError(f"{path} está vacío o no es válido")
    return value


def _nullable_text(value: Any, path: str, maximum: int = 120) -> str | None:
    return None if value is None else _text(value, path, maximum)


def _country_code(value: Any, path: str) -> str:
    code = _text(value, path, maximum=2).upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise ConfigError(f"{path} debe ser un código de país ISO de dos letras")
    return code


def parse_configuration(document: str, today: date | None = None) -> dict[str, str]:
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ConfigError("La nota no contiene YAML front matter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ConfigError("El YAML front matter no está cerrado") from error
    try:
        data = yaml.load("\n".join(lines[1:closing]), Loader=UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ConfigError("El YAML de configuración no es válido") from error

    root = _mapping(data, {"schema_version", "monitor"}, "raíz")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ConfigError("schema_version debe ser 1")
    monitor = _mapping(root["monitor"], {"weather", "transit", "report"}, "monitor")

    weather = _mapping(monitor["weather"], {"home", "temporary"}, "monitor.weather")
    home = _mapping(weather["home"], {"city", "country_code"}, "monitor.weather.home")
    temporary = _mapping(
        weather["temporary"],
        {"city", "country_code", "expires_on"},
        "monitor.weather.temporary",
    )
    home_city = _text(home["city"], "monitor.weather.home.city")
    home_country = _country_code(
        home["country_code"], "monitor.weather.home.country_code"
    )
    temporary_city = _nullable_text(temporary["city"], "monitor.weather.temporary.city")
    temporary_country_value = temporary["country_code"]
    expires_value = temporary["expires_on"]
    if (
        temporary_city is None
        and temporary_country_value is None
        and expires_value is None
    ):
        temporary_country = ""
        expires_on = ""
    elif (
        temporary_city is not None
        and temporary_country_value is not None
        and expires_value is not None
    ):
        temporary_country = _country_code(
            temporary_country_value, "monitor.weather.temporary.country_code"
        )
        if isinstance(expires_value, datetime):
            raise ConfigError("monitor.weather.temporary.expires_on debe ser una fecha")
        if isinstance(expires_value, date):
            expires_on = expires_value.isoformat()
        elif isinstance(expires_value, str):
            try:
                expires_on = date.fromisoformat(expires_value).isoformat()
            except ValueError as error:
                raise ConfigError(
                    "monitor.weather.temporary.expires_on debe usar AAAA-MM-DD"
                ) from error
        else:
            raise ConfigError("monitor.weather.temporary.expires_on debe ser una fecha")
    else:
        raise ConfigError(
            "La ubicación temporal necesita ciudad, país y fecha o todos vacíos"
        )

    transit = _mapping(
        monitor["transit"],
        {
            "enabled",
            "origin",
            "destination",
            "via_station",
            "via_mode",
            "days",
            "departure_time",
        },
        "monitor.transit",
    )
    if type(transit["enabled"]) is not bool:
        raise ConfigError("monitor.transit.enabled debe ser booleano")
    origin = _text(transit["origin"], "monitor.transit.origin")
    destination = _text(transit["destination"], "monitor.transit.destination")
    via_station = _nullable_text(transit["via_station"], "monitor.transit.via_station")
    via_mode = transit["via_mode"]
    if via_station is None:
        if via_mode is not None:
            raise ConfigError("via_mode debe ser null si no hay via_station")
        via_mode_value = ""
    else:
        if not isinstance(via_mode, str) or via_mode not in {
            "pass_through",
            "transfer",
        }:
            raise ConfigError("via_mode debe ser pass_through o transfer")
        via_mode_value = via_mode
    days = transit["days"]
    if not isinstance(days, list) or any(
        not isinstance(day, str) or day not in WEEKDAYS for day in days
    ):
        raise ConfigError("monitor.transit.days debe contener días mon..sun")
    if len(set(days)) != len(days):
        raise ConfigError("monitor.transit.days no puede repetir días")
    if transit["enabled"] and not days:
        raise ConfigError(
            "Se necesita al menos un día si el transporte está habilitado"
        )
    departure_time_value = transit["departure_time"]
    if departure_time_value is None:
        departure_time = ""
    else:
        departure_time = _text(
            departure_time_value, "monitor.transit.departure_time", maximum=5
        )
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", departure_time):
            raise ConfigError(
                "monitor.transit.departure_time debe usar el formato HH:MM"
            )
    if transit["enabled"] and not departure_time:
        raise ConfigError(
            "Se necesita una hora de salida si el transporte está habilitado"
        )

    report = _mapping(monitor["report"], {"language", "verbosity"}, "monitor.report")
    language = _text(report["language"], "monitor.report.language", maximum=12)
    if not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language):
        raise ConfigError("monitor.report.language no tiene un formato admitido")
    verbosity = _text(report["verbosity"], "monitor.report.verbosity", maximum=32)
    if verbosity not in {"minimal", "standard", "alert_adaptive"}:
        raise ConfigError("monitor.report.verbosity no está admitido")

    today = today or datetime.now().astimezone().date()
    active_city, active_country = home_city, home_country
    if temporary_city and expires_on and today <= date.fromisoformat(expires_on):
        active_city, active_country = temporary_city, temporary_country
    return {
        "WEATHER_CITY": active_city,
        "WEATHER_COUNTRY_CODE": active_country,
        "WEATHER_HOME_CITY": home_city,
        "WEATHER_HOME_COUNTRY_CODE": home_country,
        "WEATHER_TEMP_CITY": temporary_city or "",
        "WEATHER_TEMP_COUNTRY_CODE": temporary_country,
        "WEATHER_TEMP_EXPIRES_ON": expires_on,
        "TRANSIT_ENABLED": str(transit["enabled"]).lower(),
        "TRANSIT_ORIGIN": origin,
        "TRANSIT_DESTINATION": destination,
        "TRANSIT_VIA_STATION": via_station or "",
        "TRANSIT_VIA_MODE": via_mode_value,
        "TRANSIT_DAYS": ",".join(days),
        "TRANSIT_DEPARTURE_TIME": departure_time,
        "REPORT_LANGUAGE": language,
        "REPORT_VERBOSITY": verbosity,
    }


def _parse_existing_environment(content: str) -> list[tuple[str, str, str]]:
    entries = []
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            entries.append(("", "", line))
            continue
        match = ENV_ASSIGNMENT.fullmatch(line)
        if not match:
            raise ConfigError(
                "monitor.env contiene una línea no reconocida; no se modificó"
            )
        key, raw_value = match.groups()
        try:
            parsed = shlex.split(raw_value, comments=False)
        except ValueError as error:
            raise ConfigError(
                "monitor.env contiene comillas no válidas; no se modificó"
            ) from error
        if len(parsed) > 1:
            raise ConfigError("monitor.env contiene un valor ambiguo; no se modificó")
        entries.append((key, parsed[0] if parsed else "", line))
    return entries


def _render_environment(
    entries: list[tuple[str, str, str]], updates: dict[str, str]
) -> str:
    rendered = []
    written = set()
    for key, _value, original in entries:
        if key in updates:
            if key not in written:
                rendered.append(f"{key}={shlex.quote(updates[key])}")
                written.add(key)
        else:
            rendered.append(original)
    for key in MANAGED_KEYS:
        if key in updates and key not in written:
            rendered.append(f"{key}={shlex.quote(updates[key])}")
    return "\n".join(rendered).rstrip("\n") + "\n"


def update_environment_file(path: Path, updates: dict[str, str]) -> bool:
    if set(updates) != set(MANAGED_KEYS):
        raise ConfigError(
            "El conjunto de variables generadas no coincide con el esquema"
        )
    if path.is_symlink():
        raise ConfigError("monitor.env no puede ser un enlace simbólico")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ConfigError(
            "El directorio local de configuración no puede ser un enlace simbólico"
        )
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise ConfigError("El directorio local de configuración no es privado")
    old_content = path.read_text() if path.exists() else ""
    entries = _parse_existing_environment(old_content)
    new_content = _render_environment(entries, updates)
    if new_content == old_content:
        return False

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".monitor.env.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w") as temporary:
            temporary.write(new_content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return True


def sync_configuration() -> None:
    try:
        if not CONFIG_NOTE.is_file():
            raise ConfigError("No se encontró la nota de configuración")
        updates = parse_configuration(CONFIG_NOTE.read_text())
    except (ConfigError, OSError):
        if not ENV_FILE.exists():
            update_environment_file(ENV_FILE, DEFAULT_VALUES)
            print("Se inicializaron valores predeterminados seguros", file=sys.stderr)
        raise
    changed = update_environment_file(ENV_FILE, updates)
    print(
        "Configuración local del monitor actualizada"
        if changed
        else "Configuración local del monitor sin cambios"
    )


def main() -> int:
    try:
        sync_configuration()
    except (ConfigError, OSError) as error:
        print(
            f"Error al sincronizar la configuración del monitor: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
