import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.models import Watch
from openclaw.providers import (
    BlsInternationalProvider,
    ProviderError,
    TlscontactProvider,
    VfsGlobalProvider,
    get_provider,
)
from openclaw.providers.base import AuthenticationError
from openclaw.providers.bls_international import parse_bls_availability
from openclaw.providers.http_json import HttpJsonProvider
from openclaw.providers.mock import MockProvider
from openclaw.providers.tlscontact import parse_tls_availability
from openclaw.providers.vfs_global import parse_vfs_availability

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def load_fixture(*parts: str):
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


class RegistryTests(unittest.TestCase):
    def test_known_providers(self):
        self.assertIsInstance(get_provider("mock"), MockProvider)
        self.assertIsInstance(get_provider("HTTP-JSON"), HttpJsonProvider)
        self.assertIsInstance(get_provider("vfs-global"), VfsGlobalProvider)
        self.assertIsInstance(get_provider("tlscontact"), TlscontactProvider)
        self.assertIsInstance(get_provider("bls-international"), BlsInternationalProvider)

    def test_unknown_provider(self):
        with self.assertRaises(ProviderError):
            get_provider("does-not-exist")


class MockProviderTests(unittest.TestCase):
    def test_inline_slots(self):
        watch = Watch(
            "IE",
            "FR",
            "Dublin",
            options={
                "booking_url": "https://book.invalid",
                "slots": [{"date": "2026-09-14", "time": "09:20", "seats": 2}],
            },
        )
        slots = MockProvider().fetch(watch)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].slot_date, date(2026, 9, 14))
        self.assertEqual(slots[0].seats, 2)
        self.assertEqual(slots[0].booking_url, "https://book.invalid")

    def test_slots_from_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            path.write_text(json.dumps([{"date": "2026-10-01"}]), encoding="utf-8")
            slots = MockProvider().fetch(Watch("IE", "ES", "Dublin", options={"file": str(path)}))
        self.assertEqual(slots[0].slot_date, date(2026, 10, 1))

    def test_missing_file(self):
        with self.assertRaises(ProviderError):
            MockProvider().fetch(Watch("IE", "ES", "Dublin", options={"file": "/no/such.json"}))

    def test_bad_date(self):
        watch = Watch("IE", "ES", "Dublin", options={"slots": [{"date": "14/09/2026"}]})
        with self.assertRaises(ProviderError):
            MockProvider().fetch(watch)

    def test_bad_seats(self):
        for seats in ("invalid", None):
            watch = Watch(
                "IE",
                "ES",
                "Dublin",
                options={"slots": [{"date": "2026-09-14", "seats": seats}]},
            )
            with self.subTest(seats=seats), self.assertRaises(ProviderError):
                MockProvider().fetch(watch)

    def test_no_slots(self):
        self.assertEqual(MockProvider().fetch(Watch("IE", "ES", "Dublin")), [])


class HttpJsonProviderTests(unittest.TestCase):
    def _provider(self, payload):
        provider = HttpJsonProvider()
        provider._get_json = lambda url, headers: payload  # type: ignore[method-assign]
        return provider

    def _watch(self, **options):
        base = {"url": "https://portal.invalid/api", "items_key": "dates"}
        base.update(options)
        return Watch("IE", "FR", "Dublin", provider="http-json", options=base)

    def test_parses_payload(self):
        payload = {"dates": [{"date": "2026-09-14", "time": "09:20", "available": 3}]}
        provider = self._provider(payload)
        slots = provider.fetch(self._watch(time_key="time", seats_key="available"))
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].slot_time, "09:20")
        self.assertEqual(slots[0].seats, 3)

    def test_skips_zero_seat_entries(self):
        payload = {"dates": [{"date": "2026-09-14", "available": 0}]}
        provider = self._provider(payload)
        self.assertEqual(provider.fetch(self._watch(seats_key="available")), [])

    def test_requires_url(self):
        with self.assertRaises(ProviderError):
            HttpJsonProvider().fetch(Watch("IE", "FR", "Dublin", provider="http-json"))

    def test_rejects_non_http_scheme(self):
        watch = Watch("IE", "FR", "Dublin", provider="http-json", options={"url": "file:///etc/passwd"})
        with self.assertRaises(ProviderError):
            HttpJsonProvider().fetch(watch)

    def test_missing_items_key(self):
        provider = self._provider({"other": []})
        with self.assertRaises(ProviderError):
            provider.fetch(self._watch())

    def test_non_list_payload(self):
        provider = self._provider({"dates": {"date": "2026-09-14"}})
        with self.assertRaises(ProviderError):
            provider.fetch(self._watch())

    def test_bad_date_format(self):
        provider = self._provider({"dates": [{"date": "14-09-2026"}]})
        with self.assertRaises(ProviderError):
            provider.fetch(self._watch())


class VfsProviderTests(unittest.TestCase):
    def setUp(self):
        self.watch = Watch("IE", "FR", "Dublin", provider="vfs-global")
        self.options = {
            "base_url": "https://portal.example.invalid",
            "booking_path": "/book",
            "response": {"items_path": "data.days"},
        }

    def test_parse_available_slots(self):
        payload = load_fixture("vfs-global", "available.json")
        slots = parse_vfs_availability(
            self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
        )
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].slot_date, date(2026, 10, 11))
        self.assertEqual(slots[0].slot_time, "09:15")
        self.assertEqual(slots[0].seats, 2)
        self.assertIn("passport_number=%3Credacted%3E", slots[0].booking_url or "")

    def test_parse_empty(self):
        payload = load_fixture("vfs-global", "empty.json")
        self.assertEqual(
            parse_vfs_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            ),
            [],
        )

    def test_parse_sign_in_html_payload(self):
        payload = load_fixture("vfs-global", "signin.json")
        with self.assertRaisesRegex(ProviderError, "HTML page"):
            parse_vfs_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            )

    def test_parse_malformed(self):
        payload = load_fixture("vfs-global", "malformed.json")
        with self.assertRaises(ProviderError):
            parse_vfs_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            )


class TlscontactProviderTests(unittest.TestCase):
    def setUp(self):
        self.watch = Watch("IE", "ES", "Dublin", provider="tlscontact")
        self.options = {
            "base_url": "https://portal.example.invalid",
            "booking_path": "/book",
            "response": {"items_path": "calendar.days"},
        }

    def test_parse_available_slots(self):
        payload = load_fixture("tlscontact", "available.json")
        slots = parse_tls_availability(
            self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
        )
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].slot_date, date(2026, 11, 2))
        self.assertEqual(slots[0].slot_time, "08:45")
        self.assertTrue(all(slot.slot_date == date(2026, 11, 2) for slot in slots))
        self.assertIn("first_name=%3Credacted%3E", slots[1].booking_url or "")

    def test_parse_empty(self):
        payload = load_fixture("tlscontact", "empty.json")
        self.assertEqual(
            parse_tls_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            ),
            [],
        )

    def test_parse_sign_in_required(self):
        payload = load_fixture("tlscontact", "signin.json")
        with self.assertRaises(AuthenticationError):
            parse_tls_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            )

    def test_parse_malformed(self):
        payload = load_fixture("tlscontact", "malformed.json")
        with self.assertRaises(ProviderError):
            parse_tls_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            )


class BlsProviderTests(unittest.TestCase):
    def setUp(self):
        self.watch = Watch("IE", "PT", "Dublin", provider="bls-international")
        self.options = {
            "base_url": "https://portal.example.invalid",
            "booking_path": "/book",
            "response": {"items_path": "availability"},
        }

    def test_parse_available_slots(self):
        payload = load_fixture("bls-international", "available.json")
        slots = parse_bls_availability(
            self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
        )
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].slot_date, date(2026, 12, 1))
        self.assertEqual(slots[0].slot_time, "13:20")
        self.assertEqual(slots[0].seats, 2)
        self.assertIn("dob=%3Credacted%3E", slots[1].booking_url or "")

    def test_parse_empty(self):
        payload = load_fixture("bls-international", "empty.json")
        self.assertEqual(
            parse_bls_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            ),
            [],
        )

    def test_parse_sign_in_required(self):
        payload = load_fixture("bls-international", "signin.json")
        with self.assertRaises(AuthenticationError):
            parse_bls_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            )

    def test_parse_malformed(self):
        payload = load_fixture("bls-international", "malformed.json")
        with self.assertRaises(ProviderError):
            parse_bls_availability(
                self.watch, payload, self.options, request_url="https://portal.example.invalid/api"
            )


class AdapterHttpStatusTests(unittest.TestCase):
    def test_rate_limit_errors_are_clarified(self):
        provider = VfsGlobalProvider()
        provider._get_json_with_auth = lambda watch, url, headers, auth: (_ for _ in ()).throw(  # type: ignore[method-assign]
            ProviderError("request failed: HTTP 429")
        )
        watch = Watch(
            "IE",
            "FR",
            "Dublin",
            provider="vfs-global",
            options={
                "base_url": "https://portal.example.invalid",
                "availability_path": "/availability",
                "centre_code": "DUB",
                "category_code": "C",
                "mission_code": "FR",
            },
        )
        with self.assertRaisesRegex(ProviderError, "rate limited"):
            provider.fetch(watch)

    def test_missing_required_query_options_are_rejected(self):
        watches = [
            (
                VfsGlobalProvider(),
                Watch(
                    "IE",
                    "FR",
                    "Dublin",
                    provider="vfs-global",
                    options={"base_url": "https://x.invalid", "availability_path": "/a"},
                ),
                "VFS option",
            ),
            (
                TlscontactProvider(),
                Watch(
                    "IE",
                    "ES",
                    "Dublin",
                    provider="tlscontact",
                    options={"base_url": "https://x.invalid", "availability_path": "/a"},
                ),
                "TLScontact option",
            ),
            (
                BlsInternationalProvider(),
                Watch(
                    "IE",
                    "PT",
                    "Dublin",
                    provider="bls-international",
                    options={"base_url": "https://x.invalid", "availability_path": "/a"},
                ),
                "BLS option",
            ),
        ]
        for provider, watch, label in watches:
            with self.subTest(provider=provider.name), self.assertRaisesRegex(ProviderError, label):
                provider.fetch(watch)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
