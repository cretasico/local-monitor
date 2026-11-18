import unittest
from datetime import date

from daily_status import configured_weather_location


class WeatherConfigurationTests(unittest.TestCase):
    def test_uses_temporary_location_through_expiry_date(self):
        values = {
            "WEATHER_HOME_CITY": "Homecity",
            "WEATHER_HOME_COUNTRY_CODE": "DE",
            "WEATHER_TEMP_CITY": "Boston",
            "WEATHER_TEMP_COUNTRY_CODE": "US",
            "WEATHER_TEMP_EXPIRES_ON": "2026-11-20",
        }
        self.assertEqual(
            configured_weather_location(values, "Fallback", "DE", date(2026, 11, 20)),
            ("Boston", "US"),
        )

    def test_returns_to_home_after_expiry_date(self):
        values = {
            "WEATHER_HOME_CITY": "Homecity",
            "WEATHER_HOME_COUNTRY_CODE": "DE",
            "WEATHER_TEMP_CITY": "Boston",
            "WEATHER_TEMP_COUNTRY_CODE": "US",
            "WEATHER_TEMP_EXPIRES_ON": "2026-11-20",
        }
        self.assertEqual(
            configured_weather_location(values, "Fallback", "DE", date(2026, 11, 21)),
            ("Homecity", "DE"),
        )

    def test_falls_back_to_legacy_environment_values(self):
        self.assertEqual(
            configured_weather_location({"WEATHER_CITY": "Homecity"}, "", "DE"),
            ("Homecity", "DE"),
        )


if __name__ == "__main__":
    unittest.main()
