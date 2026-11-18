import contextlib
import io
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from daily_status import _validate_ai_summary, ai_summary, create_message, weather_forecast


REPORT = """✅ Raspberry Pi — estado OK
🌦️ CLIMA
Pronóstico de hoy en Ejemploville: despejado, máxima 18.0 °C, mínima 7.0 °C
⚙️ AUTOMATIZACIONES
✅ backup: OK
✅ Sin alertas detectadas"""

REPORT_WITH_TRANSIT = REPORT.replace(
    "⚙️ AUTOMATIZACIONES",
    "🚆 TRANSPORTE · Transitous\n"
    "• S-Bahn · salida 07:15: retraso de 4 min\n"
    "Fuente: https://transitous.org/sources/\n"
    "⚙️ AUTOMATIZACIONES",
)

SUMMARY = (
    "Buenos días. La Raspberry Pi está OK; en Ejemploville el pronóstico "
    "es despejado, con máxima de 18.0 °C y mínima de 7.0 °C."
)
TRANSIT_SUMMARY = (
    "Buenos días. La Raspberry Pi está OK; en Ejemploville el pronóstico "
    "es despejado, con máxima de 18.0 °C y mínima de 7.0 °C. "
    "El S-Bahn presenta 4 min de retraso."
)


class AiSummaryTests(unittest.TestCase):
    @patch("daily_status.fetch_json")
    def test_weather_forecast_uses_open_meteo_daily_values(self, fetch_json):
        fetch_json.side_effect = [
            {
                "results": [
                    {
                        "latitude": 48.0,
                        "longitude": 11.0,
                        "name": "Ejemploville",
                        "country": "Germany",
                    }
                ]
            },
            {
                "daily": {
                    "time": ["2026-11-18"],
                    "weather_code": [0],
                    "temperature_2m_max": [18.0],
                    "temperature_2m_min": [7.0],
                }
            },
        ]
        result = weather_forecast("Ejemploville")
        query = parse_qs(urlparse(fetch_json.call_args_list[1].args[0]).query)
        self.assertIn("daily", query)
        self.assertEqual(
            query["daily"],
            ["weather_code,temperature_2m_max,temperature_2m_min"],
        )
        self.assertIn("máxima 18.0 °C, mínima 7.0 °C", result)

    @patch("daily_status.fetch_json")
    def test_selects_requested_forecast_date(self, fetch_json):
        tomorrow = datetime.now().astimezone().date() + timedelta(days=1)
        fetch_json.side_effect = [
            {
                "results": [
                    {
                        "latitude": 48.0,
                        "longitude": 11.0,
                        "name": "Ejemploville",
                        "country": "Germany",
                    }
                ]
            },
            {
                "daily": {
                    "time": [
                        (tomorrow - timedelta(days=1)).isoformat(),
                        tomorrow.isoformat(),
                    ],
                    "weather_code": [3, 0],
                    "temperature_2m_max": [12.0, 18.0],
                    "temperature_2m_min": [4.0, 7.0],
                }
            },
        ]
        result = weather_forecast("Ejemploville", forecast_date=tomorrow)
        query = parse_qs(urlparse(fetch_json.call_args_list[1].args[0]).query)
        self.assertEqual(query["forecast_days"], ["2"])
        self.assertIn("máxima 18.0 °C, mínima 7.0 °C", result)

    @patch("daily_status.fetch_json")
    def test_rejects_invalid_weather_temperatures(self, fetch_json):
        fetch_json.side_effect = [
            {"results": [{"latitude": 48.0, "longitude": 11.0, "name": "Ejemploville"}]},
            {
                "daily": {
                    "weather_code": [0],
                    "temperature_2m_max": ["nan"],
                    "temperature_2m_min": [7.0],
                }
            },
        ]
        with self.assertRaises(RuntimeError):
            weather_forecast("Ejemploville")

    def test_validates_cloud_summary_against_report_facts(self):
        self.assertEqual(
            _validate_ai_summary(SUMMARY, REPORT),
            SUMMARY,
        )
        with self.assertRaises(RuntimeError):
            _validate_ai_summary(
                SUMMARY.replace("despejado", "lluvioso"),
                REPORT,
            )

    def test_rejects_transport_claim_without_verified_incidents(self):
        with self.assertRaises(RuntimeError):
            _validate_ai_summary(
                SUMMARY + " No hay incidencias de transporte.",
                REPORT,
            )

    def test_requires_verified_transit_incident_in_summary(self):
        with self.assertRaises(RuntimeError):
            _validate_ai_summary(SUMMARY, REPORT_WITH_TRANSIT)

    @patch.dict(
        os.environ,
        {
            "COPILOT_MODEL": "local/test-model",
            "COPILOT_PROVIDER_BASE_URL": "http://127.0.0.1:9999/v1",
            "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": "32",
            "COPILOT_OFFLINE": "true",
            "GH_TOKEN": "test-token",
        },
        clear=False,
    )
    @patch("daily_status.subprocess.run")
    def test_uses_cloud_auto_with_tools_disabled_and_cleans_local_overrides(
        self, run
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = TRANSIT_SUMMARY
        self.assertEqual(ai_summary(REPORT_WITH_TRANSIT), TRANSIT_SUMMARY)

        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--model") + 1], "auto")
        self.assertIn("--disable-builtin-mcps", args)
        self.assertEqual(args[-1], "--available-tools")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["GH_TOKEN"], "test-token")
        self.assertNotIn("COPILOT_MODEL", environment)
        self.assertNotIn("COPILOT_OFFLINE", environment)
        self.assertFalse(any(key.startswith("COPILOT_PROVIDER_") for key in environment))
        prompt = args[args.index("--prompt") + 1]
        self.assertIn("Pronóstico de hoy en Ejemploville", prompt)
        self.assertNotIn("backup: OK", prompt)

    @patch("daily_status.make_report", return_value=REPORT_WITH_TRANSIT)
    @patch("daily_status.ai_summary", return_value=TRANSIT_SUMMARY)
    def test_returns_brief_summary_with_transit_attribution(self, _ai, _report):
        message = create_message()
        self.assertEqual(
            message,
            TRANSIT_SUMMARY
            + "\n\nFuente del transporte: https://transitous.org/sources/",
        )
        self.assertNotIn("backup: OK", message)


    @patch("daily_status.make_report", return_value=REPORT)
    @patch("daily_status.ai_summary", side_effect=RuntimeError("test failure"))
    def test_uses_deterministic_report_when_copilot_fails(self, _ai, _report):
        error_output = io.StringIO()
        with contextlib.redirect_stderr(error_output):
            message = create_message()
        self.assertEqual(message, REPORT)
        self.assertIn("Resumen de IA no disponible", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
