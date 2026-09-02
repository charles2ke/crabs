import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.models import Watch
from openclaw.providers import ProviderError, get_provider
from openclaw.providers.http_json import HttpJsonProvider
from openclaw.providers.mock import MockProvider


class RegistryTests(unittest.TestCase):
    def test_known_providers(self):
        self.assertIsInstance(get_provider("mock"), MockProvider)
        self.assertIsInstance(get_provider("HTTP-JSON"), HttpJsonProvider)

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
