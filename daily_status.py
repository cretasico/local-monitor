#!/usr/bin/env python3
"""Send a daily Raspberry Pi and user-automation status to Telegram."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode


CONFIG_DIR = Path.home() / ".config" / "telegram-bot"
STATUS_TIMER = "raspberry-status.timer"
COPILOT_CLI = Path.home() / ".local" / "bin" / "copilot"
LOCAL_MODEL = "qwen2.5-coder:1.5b"
GLOBAL_ENVIRONMENT = Path("/etc/environment")
WEATHER_CODES = {
    0: "despejado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "niebla",
    48: "niebla con escarcha",
    51: "llovizna ligera",
    53: "llovizna moderada",
    55: "llovizna intensa",
    56: "llovizna helada ligera",
    57: "llovizna helada intensa",
    61: "lluvia ligera",
    63: "lluvia moderada",
    65: "lluvia intensa",
    66: "lluvia helada ligera",
    67: "lluvia helada intensa",
    71: "nevadas ligeras",
    73: "nevadas moderadas",
    75: "nevadas intensas",
    77: "granos de nieve",
    80: "chubascos ligeros",
    81: "chubascos moderados",
    82: "chubascos intensos",
    85: "chubascos de nieve ligeros",
    86: "chubascos de nieve intensos",
    95: "tormenta",
    96: "tormenta con granizo ligero",
    99: "tormenta con granizo intenso",
}


def run_command(*args: str, user: bool = False) -> subprocess.CompletedProcess[str]:
    command = ["systemctl"]
    if user:
        command.append("--user")
    command.extend(args)
    return subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)


def properties(unit: str, *names: str, user: bool = False) -> dict[str, str]:
    args = ["show", unit]
    for name in names:
        args.extend(["--property", name])
    result = run_command(*args, user=user)
    if result.returncode != 0:
        return {}
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def uptime_text() -> str:
    seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    parts = []
    if days:
        parts.append(f"{days} d")
    if hours:
        parts.append(f"{hours} h")
    parts.append(f"{minutes} min")
    return " ".join(parts)


def memory_status() -> tuple[int, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable"}:
            values[key] = int(value.strip().split()[0]) * 1024
    return values["MemAvailable"], values["MemTotal"]


def temperature_celsius() -> float | None:
    sensor = Path("/sys/class/thermal/thermal_zone0/temp")
    if not sensor.is_file():
        return None
    return int(sensor.read_text().strip()) / 1000


def weather_city() -> str:
    city = os.environ.get("WEATHER_CITY", "").strip()
    if not city and GLOBAL_ENVIRONMENT.is_file():
        for line in GLOBAL_ENVIRONMENT.read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "WEATHER_CITY":
                parsed = shlex.split(value, comments=True)
                city = parsed[0] if parsed else ""
                break
    if not city:
        raise RuntimeError("WEATHER_CITY no está configurada en el entorno global")
    return city


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "local-monitor/1.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def current_weather(city: str) -> str:
    geocoding_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode(
        {"name": city, "count": 1, "language": "es", "format": "json", "countryCode": "DE"}
    )
    locations = fetch_json(geocoding_url).get("results") or []
    if not locations:
        raise RuntimeError(f"No se encontró la ciudad {city}")

    location = locations[0]
    query = urlencode(
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
        }
    )
    current = fetch_json(f"https://api.open-meteo.com/v1/forecast?{query}")["current"]
    condition = WEATHER_CODES.get(int(current["weather_code"]), "condiciones no identificadas")
    city_label = location["name"]
    if location.get("admin1"):
        city_label += f", {location['admin1']}"
    if location.get("country"):
        city_label += f", {location['country']}"
    return (
        f"Clima actual en {city_label}: {condition}, "
        f"{float(current['temperature_2m']):.1f} °C "
        f"(sensación {float(current['apparent_temperature']):.1f} °C), "
        f"humedad {int(current['relative_humidity_2m'])}%, "
        f"viento {float(current['wind_speed_10m']):.1f} km/h"
    )


def user_automations() -> tuple[list[str], list[str]]:
    result = run_command("list-timers", "--all", "--no-pager", "--no-legend", user=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or "systemctl --user falló"
        return [], [f"No se pudieron consultar las automatizaciones: {detail}"]

    lines = []
    alerts = []
    for row in result.stdout.splitlines():
        columns = row.split()
        if len(columns) < 2:
            continue
        timer, service = columns[-2:]
        if not timer.endswith(".timer") or timer == STATUS_TIMER:
            continue

        timer_info = properties(
            timer,
            "ActiveState",
            "UnitFileState",
            "LastTriggerUSec",
            user=True,
        )
        service_info = properties(
            service,
            "Result",
            "ExecMainStatus",
            "ExecMainStartTimestamp",
            user=True,
        )
        active = timer_info.get("ActiveState", "desconocido")
        enabled = timer_info.get("UnitFileState", "desconocido")
        result_state = service_info.get("Result", "sin ejecución registrada")
        exit_status = service_info.get("ExecMainStatus", "desconocido")
        last_run = service_info.get("ExecMainStartTimestamp", "desconocida")

        healthy = (
            active == "active"
            and enabled in {"enabled", "enabled-runtime"}
            and result_state == "success"
            and exit_status == "0"
        )
        label = "OK" if healthy else "ALERTA"
        icon = "✅" if healthy else "⚠️"
        lines.append(
            f"{icon} {timer.removesuffix('.timer')}: {label} · "
            f"temporizador {active}/{enabled}, última ejecución {last_run}, "
            f"resultado {result_state} (código {exit_status})"
        )
        if not healthy:
            alerts.append(f"{timer} no está completamente saludable")

    if not lines and not alerts:
        lines.append("ℹ️ No hay otros temporizadores personales configurados")
    return lines, alerts


def make_report() -> str:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    load = os.getloadavg()
    cores = os.cpu_count() or 1
    available, total = memory_status()
    disk = shutil.disk_usage("/")
    disk_percent = round((disk.total - disk.free) * 100 / disk.total)
    temperature = temperature_celsius()
    try:
        weather_line = current_weather(weather_city())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"Clima no disponible: {error}", file=sys.stderr)
        weather_line = "Clima actual: no disponible"
    ollama_state = run_command("is-active", "ollama").stdout.strip() or "desconocido"
    automation_lines, alerts = user_automations()

    if disk_percent >= 90:
        alerts.append(f"disco raíz al {disk_percent}%")
    if available < 512 * 1024 * 1024:
        alerts.append("menos de 512 MiB de memoria disponible")
    if temperature is not None and temperature >= 80:
        alerts.append(f"temperatura alta: {temperature:.1f} °C")
    if ollama_state != "active":
        alerts.append(f"Ollama está {ollama_state}")

    overall = "ALERTA" if alerts else "OK"
    status_icon = "🚨" if alerts else "✅"
    memory_gib = 1024**3
    lines = [
        f"{status_icon} Raspberry Pi — estado {overall}",
        f"🕒 Actualizado: {now}",
        "",
        "📊 SISTEMA",
        f"⏱️ Uptime: {uptime_text()}",
        f"📈 Carga CPU (1/5/15 min): {load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f} ({cores} núcleos)",
        f"🧠 Memoria disponible: {available / memory_gib:.1f}/{total / memory_gib:.1f} GiB",
        f"💾 Disco raíz: {disk_percent}% usado ({disk.free / memory_gib:.1f} GiB libres)",
        f"🌡️ Temperatura CPU: {temperature:.1f} °C" if temperature is not None else "🌡️ Temperatura CPU: no disponible",
        f"🤖 Ollama: {ollama_state}",
        "",
        "🌦️ CLIMA",
        weather_line,
        "",
        "⚙️ AUTOMATIZACIONES",
        *automation_lines,
    ]
    if alerts:
        lines.extend(["", "🚨 PARA REVISAR"])
        lines.extend(f"• {alert}" for alert in alerts)
    else:
        lines.extend(["", "✅ Sin alertas detectadas"])
    return "\n".join(lines)


def ai_summary(report: str) -> str:
    status_match = re.search(r"Raspberry Pi — estado (OK|ALERTA)", report)
    if not status_match:
        raise RuntimeError("No se pudo determinar el estado general")
    weather_line = next((line for line in report.splitlines() if line.startswith("Clima actual")), "")
    weather_match = re.match(r"Clima actual en ([^:]+): ([^,]+),", weather_line)
    if weather_match:
        city = weather_match.group(1).split(",", 1)[0].strip()
        condition = weather_match.group(2).strip()
    else:
        city = weather_city()
        condition = "no disponible"
    prompt = (
        "Devuelve una sola línea siguiendo exactamente este patrón y sustituyendo solo los marcadores: "
        f"La Raspberry Pi está {status_match.group(1)}. En {city}, el clima está {condition}. "
        "No añadas nada. Devuelve texto plano, sin JSON ni herramientas.\n"
        "Datos del sistema para contextualizar: "
        + " | ".join(report.splitlines())
    )
    environment = os.environ.copy()
    environment.update(
        {
            "COPILOT_PROVIDER_BASE_URL": "http://127.0.0.1:11434/v1",
            "COPILOT_MODEL": LOCAL_MODEL,
            "COPILOT_PROVIDER_MAX_PROMPT_TOKENS": "32768",
            "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": "32",
        }
    )
    try:
        result = subprocess.run(
            [
                str(COPILOT_CLI),
                "--model",
                LOCAL_MODEL,
                "--available-tools",
                "",
                "--prompt",
                prompt,
                "--silent",
                "--no-auto-update",
                "--no-ask-user",
            ],
            capture_output=True,
            cwd="/tmp",
            env=environment,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Copilot CLI agotó el tiempo al generar el resumen") from None
    if result.returncode != 0:
        raise RuntimeError(f"Copilot CLI terminó con código {result.returncode}")

    summary = result.stdout.strip()
    expected = f"La Raspberry Pi está {status_match.group(1)}. En {city}, el clima está {condition}."
    if summary != expected:
        raise RuntimeError("Copilot no devolvió el resumen fiel esperado")
    return summary


def create_message() -> str:
    report = make_report()
    try:
        summary = ai_summary(report)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"Resumen de IA no disponible: {error}", file=sys.stderr)
        summary = "Buenos días. Aquí tienes el informe de hoy."
    return f"{summary}\n\n{report}"


def send_report(message: str) -> None:
    token = (CONFIG_DIR / "token").read_text().strip()
    chat_id = (CONFIG_DIR / "chat_id").read_text().strip()
    if not token or not chat_id:
        raise RuntimeError("El token o el chat_id de Telegram está vacío")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Telegram devolvió HTTP {error.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("No se pudo conectar con la API de Telegram") from None
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rechazó el envío (código {result.get('error_code', 'desconocido')})")


def main() -> int:
    try:
        send_report(create_message())
    except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as error:
        print(f"Error al generar o enviar el informe: {error}", file=sys.stderr)
        return 1
    print("Informe de estado enviado a Telegram")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
