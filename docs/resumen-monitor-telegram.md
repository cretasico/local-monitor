# Resumen del monitor de Raspberry Pi y Telegram

## Objetivo

Enviar cada día a las 06:00 (hora local) un estado de la Raspberry Pi por
Telegram. El mensaje combina un resumen breve generado por Copilot CLI, métricas
deterministas del sistema y el clima actual de la ciudad configurada, Bayern.

## Flujo del informe

1. `daily_status.py` reúne uptime, carga de CPU, memoria disponible, uso de disco,
   temperatura, estado de Ollama y resultado de los temporizadores personales de
   systemd.
2. Lee `WEATHER_CITY` del entorno global (`/etc/environment`) y obtiene geocodificación
   y clima actual de Open-Meteo. En esta máquina está configurada como `la ciudad configurada`.
3. Envía los datos a Copilot CLI, configurado con el modelo local
   `qwen2.5-coder:1.5b` de Ollama en `127.0.0.1:11434`. El resumen debe conservar el
   estado general y la ciudad/condición meteorológica exactos; si la salida no
   coincide con el formato esperado o Copilot falla, se usa un saludo de respaldo.
   El informe detallado se añade sin reescritura para no alterar métricas.
4. Envía el mensaje al bot de Telegram. El formato presenta resumen, estado,
   sistema, clima y automatizaciones en secciones con iconos; las alertas aparecen
   separadas.

## Archivos y configuración

- `daily_status.py`: recolección de datos, consulta del clima, resumen y envío a Telegram.
- `check_install.py`: prueba SQLite, Crawlee y Playwright/Chromium usando una página
  local; no envía mensajes ni visita sitios externos.
- `requirements.txt` y `requirements.lock.txt`: dependencias directas y versiones fijadas.
- `~/.config/systemd/user/raspberry-status.service`: ejecuta el informe como servicio
  `oneshot`; lee `/etc/environment` y aplica aislamiento de sistema de archivos.
- `~/.config/systemd/user/raspberry-status.timer`: ejecución diaria a las 06:00 con
  `Persistent=true`.
- `/etc/environment`: define `WEATHER_CITY=la ciudad configurada`; el servicio también carga este
  archivo mediante `EnvironmentFile`.
- `~/.config/telegram-bot/token` y `~/.config/telegram-bot/chat_id`: credenciales
  locales del bot. No deben copiarse al proyecto ni subirse a GitHub.

El resumen de Copilot usa el endpoint local de Ollama y se ejecuta sin herramientas
disponibles. La consulta meteorológica usa Open-Meteo; el mensaje final se entrega
por Telegram.

## Alertas

El estado general cambia a `ALERTA` si un temporizador personal no está activo,
habilitado y con última ejecución exitosa; el disco raíz alcanza el 90%, hay menos
de 512 MiB de memoria disponible, la temperatura llega a 80 °C o más, u Ollama no
está activo. El clima no modifica el estado de salud del sistema.

## Operación

```bash
# Enviar el informe manualmente
cd ~/projects/local-monitor
python3 daily_status.py

# Comprobar el temporizador y su próxima ejecución
systemctl --user status raspberry-status.timer
systemctl --user list-timers raspberry-status.timer

# Ejecutar una prueba desde systemd
systemctl --user start raspberry-status.service
systemctl --user show raspberry-status.service \
  --property=Result,ExecMainStatus,ExecMainStartTimestamp
```

Si se modifica un archivo de unidad systemd, recargar la configuración con
`systemctl --user daemon-reload`. La ciudad se puede cambiar en `/etc/environment`;
el servicio vuelve a leer ese archivo en cada ejecución.

## Estado confirmado

El 17-11-2026 se verificó un envío correcto a Telegram y que el temporizador estaba
activo y habilitado para las 06:00. El proyecto está en el repositorio privado
<https://github.com/cretasico/local-monitor>.
