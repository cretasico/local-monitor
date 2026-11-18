import contextlib
import io
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from transitous import TransitousError, safe_transport_incidents


NOW = datetime(2026, 1, 5, 6, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def settings(via_station="", via_mode=""):
    return {
        "TRANSIT_ENABLED": "true",
        "TRANSIT_ORIGIN": "Origin Station",
        "TRANSIT_DESTINATION": "Destination Station",
        "TRANSIT_VIA_STATION": via_station,
        "TRANSIT_VIA_MODE": via_mode,
        "TRANSIT_DAYS": "mon,tue,wed,thu,fri,sat,sun",
        "TRANSIT_DEPARTURE_TIME": "07:15",
    }


def transit_leg(
    trip_id="trip-1",
    from_id="origin",
    to_id="destination",
    **overrides,
):
    leg = {
        "mode": "SUBURBAN",
        "tripId": trip_id,
        "displayName": "S-Bahn",
        "from": {"id": from_id, "name": from_id},
        "to": {"id": to_id, "name": to_id},
        "startTime": "2026-01-05T07:20:00+01:00",
        "scheduledStartTime": "2026-01-05T07:15:00+01:00",
        "endTime": "2026-01-05T07:35:00+01:00",
        "scheduledEndTime": "2026-01-05T07:30:00+01:00",
        "realTime": True,
        "interlineWithPreviousLeg": False,
    }
    leg.update(overrides)
    return leg


def api_responses(plan):
    return [
        [{"id": "origin", "name": "Origin Station", "type": "STOP", "score": 1}],
        [
            {
                "id": "destination",
                "name": "Destination Station",
                "type": "STOP",
                "score": 1,
            }
        ],
        plan,
    ]


class TransitousTests(unittest.TestCase):
    @patch("transitous.fetch_json")
    def test_plans_without_optional_via(self, fetch_json):
        fetch_json.side_effect = api_responses(
            {"itineraries": [{"legs": [transit_leg()]}]}
        )
        result = safe_transport_incidents(settings(), NOW)
        self.assertIn("retraso de 5 min", result[0])
        query = parse_qs(urlparse(fetch_json.call_args_list[-1].args[0]).query)
        self.assertNotIn("via", query)
        self.assertEqual(query["realtimeMode"], ["REALTIME_ANNOTATION_ONLY"])

    @patch("transitous.fetch_json")
    def test_passes_optional_via_station_to_plan(self, fetch_json):
        leg = transit_leg(
            intermediateStops=[
                {"id": "via-platform", "parentId": "via", "name": "Via Station"}
            ]
        )
        fetch_json.side_effect = [
            [{"id": "origin", "name": "Origin Station", "type": "STOP", "score": 1}],
            [
                {
                    "id": "destination",
                    "name": "Destination Station",
                    "type": "STOP",
                    "score": 1,
                }
            ],
            [{"id": "via", "name": "Via Station", "type": "STOP", "score": 1}],
            {"itineraries": [{"legs": [leg]}]},
        ]
        result = safe_transport_incidents(
            settings("Via Station", "pass_through"), NOW
        )
        self.assertIn("retraso de 5 min", result[0])
        query = parse_qs(urlparse(fetch_json.call_args_list[-1].args[0]).query)
        self.assertEqual(query["via"], ["via"])
        self.assertEqual(query["viaMinimumStay"], ["0"])

    @patch("transitous.fetch_json")
    def test_transfer_via_requires_two_verified_distinct_trips(self, fetch_json):
        inbound = transit_leg(
            trip_id="trip-in",
            from_id="origin",
            to_id="via-platform-in",
            to={"id": "via-platform-in", "parentId": "via", "name": "Via Station"},
            realTime=False,
        )
        outbound = transit_leg(
            trip_id="trip-out",
            from_id="via-platform-out",
            **{
                "from": {
                    "id": "via-platform-out",
                    "parentId": "via",
                    "name": "Via Station",
                }
            },
            to_id="destination",
            interlineWithPreviousLeg=False,
            cancelled=True,
        )
        fetch_json.side_effect = [
            [{"id": "origin", "name": "Origin Station", "type": "STOP", "score": 1}],
            [
                {
                    "id": "destination",
                    "name": "Destination Station",
                    "type": "STOP",
                    "score": 1,
                }
            ],
            [{"id": "via", "name": "Via Station", "type": "STOP", "score": 1}],
            {
                "itineraries": [
                    {"legs": [inbound, {**outbound, "interlineWithPreviousLeg": True}]},
                    {"legs": [inbound, outbound]},
                ]
            },
        ]
        result = safe_transport_incidents(settings("Via Station", "transfer"), NOW)
        self.assertTrue(any("cancelado" in line for line in result))
        query = parse_qs(urlparse(fetch_json.call_args_list[-1].args[0]).query)
        self.assertEqual(query["viaMinimumStay"], ["5"])

    @patch("transitous.fetch_json")
    def test_reports_only_realtime_delays_and_explicit_cancellations(self, fetch_json):
        delayed = transit_leg()
        cancelled = transit_leg(
            trip_id="trip-cancelled",
            cancelled=True,
            realTime=False,
        )
        unverified = transit_leg(
            trip_id="trip-unverified",
            realTime=False,
            startTime="2026-01-05T07:00:00+01:00",
            endTime="2026-01-05T07:20:00+01:00",
        )
        fetch_json.side_effect = api_responses(
            {"itineraries": [{"legs": [delayed, cancelled, unverified]}]}
        )
        result = safe_transport_incidents(settings(), NOW)
        self.assertEqual(len(result), 2)
        self.assertTrue(any("retraso de 5 min" in line for line in result))
        self.assertTrue(any("cancelado" in line for line in result))

    @patch("transitous.fetch_json")
    def test_skips_service_during_temporary_location(self, fetch_json):
        config = settings()
        config.update(
            {
                "WEATHER_TEMP_CITY": "Temporary City",
                "WEATHER_TEMP_COUNTRY_CODE": "DE",
                "WEATHER_TEMP_EXPIRES_ON": "2026-01-05",
            }
        )
        self.assertEqual(safe_transport_incidents(config, NOW), [])
        fetch_json.assert_not_called()

    @patch("transitous.fetch_json")
    def test_skips_outside_configured_day_and_after_departure(self, fetch_json):
        config = settings()
        config["TRANSIT_DAYS"] = "tue"
        self.assertEqual(safe_transport_incidents(config, NOW), [])
        config.update({"TRANSIT_DAYS": "mon", "TRANSIT_DEPARTURE_TIME": "05:50"})
        self.assertEqual(safe_transport_incidents(config, NOW), [])
        fetch_json.assert_not_called()

    @patch("transitous.fetch_json", side_effect=TransitousError("private detail"))
    def test_api_failure_is_logged_and_does_not_raise(self, _fetch_json):
        error_output = io.StringIO()
        with contextlib.redirect_stderr(error_output):
            result = safe_transport_incidents(settings(), NOW)
        self.assertEqual(result, [])
        self.assertIn("Transporte no disponible", error_output.getvalue())
        self.assertNotIn("private detail", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
