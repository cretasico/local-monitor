# Herramientas del monitor local

Base instalada en la Raspberry Pi. Envía un informe diario de salud a Telegram
a las 06:00 (hora local). No integra rastreo web; Open-Meteo y Transitous se
consultan mediante sus API públicas.

## Componentes

- Python 3.13 con entorno virtual `.venv`.
- Crawlee con BeautifulSoup para HTTP y soporte para Playwright.
- Playwright, probado con Chromium del sistema en `/usr/bin/chromium`.
- SQLite mediante el módulo `sqlite3` incluido en Python.
- Informe diario en `daily_status.py`, enviado por el bot de Telegram local.
- Informe breve redactado por Copilot; el respaldo determinista conserva el panel
  con métricas y alertas.
- GitHub Copilot CLI redacta un mensaje breve usando `--model auto`, que selecciona
  un modelo disponible en la cuenta. Las herramientas están deshabilitadas; Python
  valida la respuesta y conserva el informe determinista como respaldo.
- La ciudad y el país se configuran en `Monitor local - configuración.md` en el
  vault y se sincronizan a `~/.config/local-monitor/monitor.env` después del pull
  diario de las 03:00.
- El pronóstico diario de Open-Meteo y las incidencias verificadas de Transitous
  forman parte del mensaje matutino.
- Transitous/MOTIS consulta una vez al día los servicios a la hora configurada.
  Solo se muestran retrasos/cancelaciones marcados en tiempo real; los datos
  ausentes no se interpretan como ausencia de incidencias. La consulta se omite
  durante una ubicación temporal activa. Atribución: [Transitous](https://transitous.org/sources/).
- Temporizador de usuario `raspberry-status.timer`, persistente y programado a las 06:00.
- Estado de automatizaciones basado en los temporizadores systemd del usuario; actualmente incluye `sync-obsidian`.
- `sync_monitor_config.py` valida el YAML del vault y conserva la última configuración local válida si la nota no está disponible o no valida.

Las pruebas locales no envían mensajes a Telegram:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile daily_status.py transitous.py sync_monitor_config.py
```

## Uso

```bash
cd ~/projects/local-monitor
source .venv/bin/activate
python check_install.py
```

La comprobación crea una página HTTP temporal en localhost, verifica el rastreador,
prueba una base SQLite en memoria y abre y cierra Chromium sin interfaz gráfica.
No visita webs externas ni usa perfiles de navegador personales.

Para enviar manualmente un informe de estado:

```bash
cd ~/projects/local-monitor
python3 daily_status.py
```

Python recopila uptime, carga de CPU, memoria disponible, espacio en disco,
temperatura, pronóstico, incidencias verificadas y resultado de los temporizadores
personales. Copilot CLI solo redacta el texto; no recibe herramientas. Si falla o
su respuesta no supera las validaciones, se envía el informe determinista completo.
El token y el `chat_id` se guardan en
`~/.config/telegram-bot/` con permisos privados; no deben añadirse al repositorio.
El informe lee primero la configuración local del monitor; si falta, mantiene
compatibilidad con `WEATHER_CITY` en `/etc/environment`.

Transitous es un servicio comunitario best-effort, sin clave API. El cliente usa
un `User-Agent` identificable y limita las consultas a la planificación diaria y
la geocodificación necesaria. El proyecto no debe usar este servicio con fines
comerciales ni generar tráfico elevado; sus condiciones están en
[transitous.org](https://transitous.org/).

Para utilizar Chromium en los futuros scripts de Playwright:

```python
browser = await playwright.chromium.launch(
    executable_path="/usr/bin/chromium",
    headless=True,
    chromium_sandbox=True,
)
```

Empezar con un navegador y una petición simultánea para ajustar el uso a la Raspberry.
El Chromium del sistema pasó la comprobación actual; repetirla tras actualizaciones
de Playwright o Chromium. No se han descargado Firefox, WebKit ni otro Chromium.

## Dependencias

`requirements.txt` recoge los componentes principales y `requirements.lock.txt`
las versiones exactas instaladas, incluida la versión de Playwright.

Para reproducir las bibliotecas en otro entorno Python compatible:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
```

No se ha instalado un servidor MCP: el rastreo programado usará las bibliotecas
directamente. Si más adelante hace falta controlar el navegador desde el chat,
se puede añadir Playwright MCP como integración separada.
