import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sync_monitor_config import (
    ConfigError,
    parse_configuration,
    update_environment_file,
)


def configuration(temporary_location: str = "null", expires_on: str = "null") -> str:
    return f"""---
schema_version: 1
monitor:
  weather:
    home:
      city: Sampletown
      country_code: DE
    temporary:
      city: {temporary_location}
      country_code: {"US" if temporary_location != "null" else "null"}
      expires_on: {expires_on}
  transit:
    enabled: false
    origin: Origin Station
    destination: Destination Station
    via_station: null
    via_mode: null
    days: []
    departure_time: null
  report:
    language: es
    verbosity: alert_adaptive
---
# Settings
"""


class SyncMonitorConfigTests(unittest.TestCase):
    def test_selects_temporary_location_until_expiry(self):
        today = datetime.now().astimezone().date()
        tomorrow = today + timedelta(days=1)
        values = parse_configuration(
            configuration('"Boston"', tomorrow.isoformat()),
            today=today,
        )
        self.assertEqual(values["WEATHER_CITY"], "Boston")
        self.assertEqual(values["WEATHER_COUNTRY_CODE"], "US")

    def test_reverts_to_home_after_temporary_location_expires(self):
        today = datetime.now().astimezone().date()
        yesterday = today - timedelta(days=1)
        values = parse_configuration(
            configuration('"Boston"', yesterday.isoformat()),
            today=today,
        )
        self.assertEqual(values["WEATHER_CITY"], "Sampletown")
        self.assertEqual(values["WEATHER_COUNTRY_CODE"], "DE")

    def test_rejects_secret_fields(self):
        document = configuration().replace(
            "    enabled: false",
            "    enabled: false\n    api_key: should-not-be-accepted",
        )
        with self.assertRaises(ConfigError):
            parse_configuration(document)

    def test_rejects_duplicate_yaml_keys(self):
        document = configuration().replace(
            "    enabled: false", "    enabled: false\n    enabled: true"
        )
        with self.assertRaises(ConfigError):
            parse_configuration(document)

    def test_updates_managed_values_and_preserves_unrelated_lines(self):
        values = parse_configuration(configuration())
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / "monitor.env"
            env_file.write_text(
                "# Keep this comment\nOTHER_SETTING=keep-me\nWEATHER_CITY=Oldtown\n"
            )
            update_environment_file(env_file, values)
            updated = env_file.read_text()
            self.assertIn("OTHER_SETTING=keep-me", updated)
            self.assertIn("# Keep this comment", updated)
            self.assertIn("WEATHER_CITY=Sampletown", updated)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)

    def test_requires_valid_departure_time_when_transit_is_enabled(self):
        document = configuration().replace(
            "    enabled: false", "    enabled: true"
        ).replace(
            "    days: []",
            "    days: [mon]\n    departure_time: '07:15'",
        ).replace("    departure_time: null", "")
        self.assertEqual(
            parse_configuration(document)["TRANSIT_DEPARTURE_TIME"],
            "07:15",
        )

    def test_rejects_invalid_departure_time(self):
        document = configuration().replace(
            "    departure_time: null", "    departure_time: '6:50'"
        )
        with self.assertRaises(ConfigError):
            parse_configuration(document)

    def test_invalid_existing_environment_is_not_overwritten(self):
        values = parse_configuration(configuration())
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / "monitor.env"
            env_file.write_text("OTHER_SETTING='unterminated\n")
            with self.assertRaises(ConfigError):
                update_environment_file(env_file, values)
            self.assertEqual(env_file.read_text(), "OTHER_SETTING='unterminated\n")


if __name__ == "__main__":
    unittest.main()
