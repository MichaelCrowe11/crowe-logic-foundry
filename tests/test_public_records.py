"""Tests for Arizona public-record lookup tools."""

from __future__ import annotations

import json

import httpx

import tools.public_records as records


class _FakeResponse:
    def __init__(self, *, text: str = "", status_code: int = 200, json_data=None, url: str = "https://example.com"):
        self.text = text
        self.status_code = status_code
        self._json_data = json_data
        self.url = url

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


class _MaricopaSearchClient:
    def __init__(self):
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None, headers=None):
        params = params or {}
        headers = headers or {}
        self.calls.append({"url": url, "params": dict(params), "headers": dict(headers)})

        if url.endswith("/mcs/"):
            return _FakeResponse(
                text="""
                    <script>
                    var g_token = 'token-123';
                    </script>
                """,
                url=f"{url}?q={params.get('q', '')}",
            )

        if url.endswith("/search/rp/"):
            query = params.get("q", "")
            if query == "3601 N 5TH AVE":
                return _FakeResponse(json_data={
                    "TOTAL": 1,
                    "Results": [{
                        "APN": "11829005E",
                        "Ownership": "BK HOLDINGS II LLLP",
                        "SitusAddress": "3601 N 5TH AVE",
                        "SitusCity": "PHOENIX",
                        "SitusZip": "85013",
                        "SubdivisonName": "PARK NORTH",
                        "MCR": "6927",
                        "SectionTownshipRange": "292N3E",
                        "PropertyType": "MULTI FAMILY",
                        "RentalID": "Y",
                    }],
                })
            return _FakeResponse(json_data={"TOTAL": 0, "Results": []})

        raise AssertionError(f"Unexpected GET {url} {params}")


class _StaticClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None, headers=None):
        self.calls.append({"method": "GET", "url": url, "params": params or {}, "headers": headers or {}})
        if not self.responses:
            raise AssertionError(f"No fake response left for GET {url}")
        return self.responses.pop(0)

    def post(self, url, data=None, headers=None):
        self.calls.append({"method": "POST", "url": url, "data": data or {}, "headers": headers or {}})
        if not self.responses:
            raise AssertionError(f"No fake response left for POST {url}")
        return self.responses.pop(0)


def test_maricopa_assessor_search_property_normalizes_query(monkeypatch):
    client = _MaricopaSearchClient()
    monkeypatch.setattr(records, "_http_client", lambda: client)

    payload = json.loads(records.maricopa_assessor_search_property("3601 N 5th Ave Phoenix AZ 85013"))

    assert payload["query_used"] == "3601 N 5TH AVE"
    assert payload["total"] == 1
    assert payload["results"] == [{
        "apn": "11829005E",
        "owner": "BK HOLDINGS II LLLP",
        "address": "3601 N 5TH AVE, PHOENIX, 85013",
        "subdivision": "PARK NORTH",
        "mcr": "6927",
        "section_township_range": "292N3E",
        "property_type": "MULTI FAMILY",
        "rental_registered": True,
    }]
    assert any(call["params"].get("q") == "3601 N 5TH AVE" for call in client.calls if call["url"].endswith("/search/rp/"))


def test_maricopa_assessor_get_parcel_details_parses_owner_and_deed(monkeypatch):
    html = """
        <html><body>
        <div>118-29-005E</div>
        <div>Multi Family Parcel</div>
        <div>This is a Multi Family parcel located at 3601 N 5TH AVE PHOENIX, AZ 85013.
        The current owner is BK HOLDINGS II LLLP. It is located in the PARK NORTH subdivision,
        and MCR 6927. It was last sold on 12/01/2020 for $4,900,000.</div>
        <div>Owner Information</div><div>BK HOLDINGS II LLLP</div>
        <div>Mailing Address</div><div>10 MOUNTAIN COVE CT, HENDERSON, NV 89052</div>
        <div>Deed Number</div><div>20210264544</div>
        <div>Last Deed Date</div><div>03/09/2021</div>
        <div>Sale Date</div><div>12/01/2020</div>
        </body></html>
    """
    client = _StaticClient([_FakeResponse(text=html)])
    monkeypatch.setattr(records, "_http_client", lambda: client)

    payload = json.loads(records.maricopa_assessor_get_parcel_details("118-29-005E"))

    assert payload["apn"] == "11829005E"
    assert payload["formatted_apn"] == "118-29-005E"
    assert payload["property_address"] == "3601 N 5TH AVE PHOENIX, AZ 85013"
    assert payload["current_owner"] == "BK HOLDINGS II LLLP"
    assert payload["deed_number"] == "20210264544"
    assert payload["recorder_document_url"].endswith("recordingNumber=20210264544")


def test_maricopa_assessor_get_parcel_details_accepts_slashed_mcr_and_cents(monkeypatch):
    html = """
        <html><body>
        <div>118-29-005E</div>
        <div>Multi Family Parcel</div>
        <div>This is a Multi Family parcel located at 3601 N 5TH AVE PHOENIX, AZ 85013.
        The current owner is BK HOLDINGS II LLLP. It is located in the PARK NORTH subdivision,
        and MCR 6927/12. It was last sold on 12/01/2020 for $4,900,000.50.</div>
        <div>Owner Information</div><div>BK HOLDINGS II LLLP</div>
        <div>Mailing Address</div><div>10 MOUNTAIN COVE CT, HENDERSON, NV 89052</div>
        <div>Deed Number</div><div>20210264544</div>
        <div>Last Deed Date</div><div>03/09/2021</div>
        <div>Sale Date</div><div>12/01/2020</div>
        </body></html>
    """
    client = _StaticClient([_FakeResponse(text=html)])
    monkeypatch.setattr(records, "_http_client", lambda: client)

    payload = json.loads(records.maricopa_assessor_get_parcel_details("118-29-005E"))

    assert payload["mcr"] == "6927/12"
    assert payload["sale_price"] == "$4,900,000.50"


def test_maricopa_assessor_search_property_prefers_more_specific_candidate(monkeypatch):
    class _DisambiguationClient(_MaricopaSearchClient):
        def get(self, url, params=None, headers=None):
            params = params or {}
            headers = headers or {}
            self.calls.append({"url": url, "params": dict(params), "headers": dict(headers)})

            if url.endswith("/mcs/"):
                return _FakeResponse(
                    text="""
                        <script>
                        var g_token = 'token-123';
                        </script>
                    """,
                    url=f"{url}?q={params.get('q', '')}",
                )

            if url.endswith("/search/rp/"):
                query = params.get("q", "")
                if query == "3601 N 5TH AVE PHOENIX":
                    return _FakeResponse(json_data={
                        "TOTAL": 2,
                        "Results": [
                            {
                                "APN": "11829005A",
                                "Ownership": "OTHER OWNER LLC",
                                "SitusAddress": "3601 N 5TH AVE",
                                "SitusCity": "PHOENIX",
                                "SitusZip": "85013",
                                "SubdivisonName": "OTHER",
                                "MCR": "1111",
                                "SectionTownshipRange": "292N3E",
                                "PropertyType": "MULTI FAMILY",
                                "RentalID": "Y",
                            },
                            {
                                "APN": "11829005E",
                                "Ownership": "BK HOLDINGS II LLLP",
                                "SitusAddress": "3601 N 5TH AVE",
                                "SitusCity": "PHOENIX",
                                "SitusZip": "85013",
                                "SubdivisonName": "PARK NORTH",
                                "MCR": "6927",
                                "SectionTownshipRange": "292N3E",
                                "PropertyType": "MULTI FAMILY",
                                "RentalID": "Y",
                            },
                        ],
                    })
                if query == "3601 N 5TH AVE":
                    return _FakeResponse(json_data={
                        "TOTAL": 1,
                        "Results": [{
                            "APN": "11829005E",
                            "Ownership": "BK HOLDINGS II LLLP",
                            "SitusAddress": "3601 N 5TH AVE",
                            "SitusCity": "PHOENIX",
                            "SitusZip": "85013",
                            "SubdivisonName": "PARK NORTH",
                            "MCR": "6927",
                            "SectionTownshipRange": "292N3E",
                            "PropertyType": "MULTI FAMILY",
                            "RentalID": "Y",
                        }],
                    })
                return _FakeResponse(json_data={"TOTAL": 0, "Results": []})

            raise AssertionError(f"Unexpected GET {url} {params}")

    client = _DisambiguationClient()
    monkeypatch.setattr(records, "_http_client", lambda: client)

    payload = json.loads(records.maricopa_assessor_search_property("3601 N 5th Ave Phoenix AZ 85013"))

    assert payload["query_used"] == "3601 N 5TH AVE"
    assert payload["total"] == 1
    assert payload["results"][0]["apn"] == "11829005E"


def test_adre_entity_license_search_parses_results_table(monkeypatch):
    search_html = """
        <form action="/PdbWeb/EntityLicense/SearchEntityLicenses" method="post">
            <input name="__RequestVerificationToken" type="hidden" value="token-456" />
        </form>
    """
    results_html = """
        <table id="dataTableEntityLicenses" class="table table-bordered">
            <tr>
                <th></th><th>License No</th><th>Legal Name</th><th>Business Name</th><th>Status</th><th>City</th><th>Zip</th>
            </tr>
            <tr>
                <td><a href="/PdbWeb/EntityLicense/ViewEntityLicense/38505">View</a></td>
                <td>LC711042000</td>
                <td>QUARTR LIVING LLC</td>
                <td>QUARTR LIVING</td>
                <td>Active</td>
                <td>FOUNTAIN HILLS</td>
                <td>85268</td>
            </tr>
        </table>
    """
    client = _StaticClient([
        _FakeResponse(text=search_html),
        _FakeResponse(text=results_html),
    ])
    monkeypatch.setattr(records, "_http_client", lambda: client)

    payload = json.loads(records.adre_entity_license_search("QUARTR", search_field="business_name"))

    assert payload["total"] == 1
    assert payload["results"][0] == {
        "license_number": "LC711042000",
        "legal_name": "QUARTR LIVING LLC",
        "business_name": "QUARTR LIVING",
        "status": "Active",
        "city": "FOUNTAIN HILLS",
        "zip": "85268",
        "detail_url": "https://services.azre.gov/PdbWeb/EntityLicense/ViewEntityLicense/38505",
    }
    assert client.calls[1]["data"]["BusinessName"] == "QUARTR"


def test_adre_entity_license_details_parses_designated_broker(monkeypatch):
    detail_html = """
        <html><body>
        <div>Entity License Details</div>
        <div>License Number</div><div>LC711042000</div>
        <div>Legal Name</div><div>QUARTR LIVING LLC</div>
        <div>Business (DBA) Name</div><div>QUARTR LIVING</div>
        <div>License Status</div><div>Active</div>
        <div>License Type</div><div>Real Estate Limited Liability</div>
        <div>Original Date</div><div>11/2/2023</div>
        <div>Expiration Date</div><div>10/31/2027</div>
        <div>Phone</div><div>480-660-3400</div>
        <div>Business Address</div><div>17100 E. Shea Blvd, Suite 300</div><div>FOUNTAIN HILLS AZ 85268</div>
        <div>Mailing Address</div><div>PO BOX 20065</div><div>FOUNTAIN HILLS AZ 85269</div>
        <div>Designated Broker Details</div>
        <div>License Number</div><div>BR690826000</div>
        <div>Name</div><div>DELDEBBIO, SAMANTHA ELIZABETH</div>
        <div>License Type</div><div>Real Estate Broker</div>
        </body></html>
    """
    client = _StaticClient([_FakeResponse(text=detail_html)])
    monkeypatch.setattr(records, "_http_client", lambda: client)

    payload = json.loads(records.adre_entity_license_details(record_id="38505"))

    assert payload["license_number"] == "LC711042000"
    assert payload["business_address"] == "17100 E. Shea Blvd, Suite 300 FOUNTAIN HILLS AZ 85268"
    assert payload["designated_broker_license_number"] == "BR690826000"
    assert payload["designated_broker_name"] == "DELDEBBIO, SAMANTHA ELIZABETH"
