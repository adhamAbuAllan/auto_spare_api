import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, override_settings

from . import car_catalog_sync
from .car_catalog_sync import CarImagesApiClient, CarImagesApiError


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def http_error(url, status=403, body=b'{"detail":"blocked"}'):
    return HTTPError(url, status, "Forbidden", {}, io.BytesIO(body))


class CarImagesApiClientTests(SimpleTestCase):
    def setUp(self):
        car_catalog_sync._MEMORY_CACHE["makes"] = None
        car_catalog_sync._MEMORY_CACHE["models"] = {}
        car_catalog_sync._MEMORY_CACHE["model_details"] = {}

    @override_settings(
        CAR_IMAGES_API_BASE_URL="https://proxy.example/api/v1",
        CAR_IMAGES_API_FALLBACK_BASE_URLS=(),
        CAR_IMAGES_API_PROXY_TOKEN="secret-token",
    )
    @patch("api.car_catalog_sync.urlopen")
    def test_list_makes_falls_back_to_direct_api_when_proxy_is_blocked(self, urlopen):
        urlopen.side_effect = [
            http_error("https://proxy.example/api/v1/makes"),
            FakeResponse({"data": [{"slug": "tesla", "name": "Tesla"}]}),
        ]

        makes = CarImagesApiClient().list_makes()

        self.assertEqual(makes, [{"slug": "tesla", "name": "Tesla"}])
        called_urls = [
            call.args[0].full_url
            for call in urlopen.call_args_list
        ]
        self.assertEqual(
            called_urls,
            [
                "https://proxy.example/api/v1/makes",
                "https://carimagesapi.com/api/v1/makes",
            ],
        )

    @override_settings(
        CAR_IMAGES_API_BASE_URL="https://carimagesapi.com/api/v1",
        CAR_IMAGES_API_FALLBACK_BASE_URLS=("https://proxy.example/api/v1",),
        CAR_IMAGES_API_PROXY_TOKEN="secret-token",
    )
    @patch("api.car_catalog_sync.urlopen")
    def test_proxy_token_is_only_sent_to_proxy_hosts(self, urlopen):
        captured_headers = []

        def fake_urlopen(request, timeout):
            captured_headers.append(dict(request.header_items()))
            if request.full_url.startswith("https://carimagesapi.com/"):
                raise URLError("blocked")
            return FakeResponse({"data": [{"slug": "bmw", "name": "BMW"}]})

        urlopen.side_effect = fake_urlopen

        makes = CarImagesApiClient().list_makes()

        self.assertEqual(makes, [{"slug": "bmw", "name": "BMW"}])
        self.assertNotIn("X-car-images-proxy-token", captured_headers[0])
        self.assertEqual(
            captured_headers[1].get("X-car-images-proxy-token"),
            "secret-token",
        )

    @override_settings(
        CAR_IMAGES_API_BASE_URL="https://bad.example/api/v1",
        CAR_IMAGES_API_FALLBACK_BASE_URLS=("https://also-bad.example/api/v1",),
    )
    @patch("api.car_catalog_sync.urlopen")
    def test_error_mentions_all_configured_base_urls_when_everything_fails(self, urlopen):
        urlopen.side_effect = URLError("network blocked")

        with self.assertRaises(CarImagesApiError) as context:
            CarImagesApiClient().list_makes()

        message = str(context.exception)
        self.assertIn("https://bad.example/api/v1/makes", message)
        self.assertIn("https://also-bad.example/api/v1/makes", message)
        self.assertIn("https://carimagesapi.com/api/v1/makes", message)

    @override_settings(
        CAR_IMAGES_API_BASE_URL="https://proxy.example/api/v1",
        CAR_IMAGES_API_FALLBACK_BASE_URLS=(),
        CAR_IMAGES_API_PROXY_TOKEN="secret-token",
    )
    @patch("api.car_catalog_sync.urlopen")
    def test_list_makes_collects_paginated_results(self, urlopen):
        urlopen.side_effect = [
            FakeResponse(
                {
                    "data": [{"slug": "bmw", "name": "BMW"}],
                    "links": {"next": "?page=2"},
                }
            ),
            FakeResponse({"data": [{"slug": "toyota", "name": "Toyota"}]}),
        ]

        makes = CarImagesApiClient().list_makes()

        self.assertEqual(
            makes,
            [
                {"slug": "bmw", "name": "BMW"},
                {"slug": "toyota", "name": "Toyota"},
            ],
        )
        self.assertEqual(
            [call.args[0].full_url for call in urlopen.call_args_list],
            [
                "https://proxy.example/api/v1/makes",
                "https://proxy.example/api/v1/makes?page=2",
            ],
        )

    @override_settings(
        CAR_IMAGES_API_BASE_URL="https://proxy.example/api/v1",
        CAR_IMAGES_API_FALLBACK_BASE_URLS=(),
    )
    @patch("api.car_catalog_sync.urlopen")
    def test_list_models_collects_paginated_results(self, urlopen):
        urlopen.side_effect = [
            FakeResponse(
                {
                    "data": [{"slug": "camry", "name": "Camry"}],
                    "pagination": {"next": "/api/v1/makes/toyota/models?page=2"},
                }
            ),
            FakeResponse({"data": [{"slug": "corolla", "name": "Corolla"}]}),
        ]

        models = CarImagesApiClient().list_models("toyota")

        self.assertEqual(
            models,
            [
                {"slug": "camry", "name": "Camry"},
                {"slug": "corolla", "name": "Corolla"},
            ],
        )
        self.assertEqual(
            [call.args[0].full_url for call in urlopen.call_args_list],
            [
                "https://proxy.example/api/v1/makes/toyota/models",
                "https://proxy.example/api/v1/makes/toyota/models?page=2",
            ],
        )

    @override_settings(
        CAR_IMAGES_API_BASE_URL="https://proxy.example/api/v1",
        CAR_IMAGES_API_FALLBACK_BASE_URLS=(),
    )
    @patch("api.car_catalog_sync.urlopen")
    def test_absolute_upstream_next_url_stays_on_configured_base_url(self, urlopen):
        urlopen.side_effect = [
            FakeResponse(
                {
                    "data": [{"slug": "bmw", "name": "BMW"}],
                    "links": {
                        "next": "https://carimagesapi.com/api/v1/makes?page=2",
                    },
                }
            ),
            FakeResponse({"data": [{"slug": "toyota", "name": "Toyota"}]}),
        ]

        makes = CarImagesApiClient().list_makes()

        self.assertEqual(
            makes,
            [
                {"slug": "bmw", "name": "BMW"},
                {"slug": "toyota", "name": "Toyota"},
            ],
        )
        self.assertEqual(
            [call.args[0].full_url for call in urlopen.call_args_list],
            [
                "https://proxy.example/api/v1/makes",
                "https://proxy.example/api/v1/makes?page=2",
            ],
        )
