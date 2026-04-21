from typing import Optional, List, Tuple
"""
Arizona public-record research tools for ownership and management lookups.
"""

import json
import re
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup


_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CroweLogic/0.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_MARICOPA_BASE_URL = "https://mcassessor.maricopa.gov"
_ADRE_ENTITY_SEARCH_URL = "https://services.azre.gov/PdbWeb/EntityLicense/SearchEntityLicenses"
_ADRE_BASE_URL = "https://services.azre.gov"
_RECORDER_DETAIL_URL_TEMPLATE = (
    "https://recorder.maricopa.gov/recording/document-details.html?recordingNumber={recording_number}"
)
_TRAILING_STREET_TYPES = {"AVE", "AV", "ST", "RD", "DR", "LN", "PL", "BLVD", "CIR", "CT", "PKWY", "WAY"}


def _http_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=20,
        headers=_HTTP_HEADERS,
    )


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _compact_whitespace(text: str) -> str:
    return _normalize_whitespace(text).upper()


def _unique_strings(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        normalized = _normalize_whitespace(value)
        if not normalized:
            continue
        key = normalized.upper()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _looks_like_apn(query: str) -> bool:
    compact = re.sub(r"[^0-9A-Za-z]", "", query or "").upper()
    return bool(compact) and " " not in _normalize_whitespace(query) and len(compact) >= 7 and any(
        ch.isdigit() for ch in compact
    )


def _normalize_apn(apn: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", apn or "").upper()


def _format_assessor_address(street: str, city: str, zip_code: str) -> str:
    parts = [_normalize_whitespace(street), _normalize_whitespace(city), _normalize_whitespace(zip_code)]
    return ", ".join(part for part in parts if part)


def _assessor_query_candidates(query: str) -> List[str]:
    cleaned = _compact_whitespace((query or "").replace(",", " "))
    if not cleaned:
        return []
    if _looks_like_apn(cleaned):
        return [_normalize_apn(cleaned)]

    candidates = [cleaned]

    without_zip = re.sub(r"\b\d{5}(?:-\d{4})?\b$", "", cleaned).strip()
    candidates.append(without_zip)

    without_state = re.sub(r"\bAZ\b$", "", without_zip).strip()
    candidates.append(without_state)

    tokens = without_state.split()
    if len(tokens) >= 5:
        candidates.append(" ".join(tokens[:-1]))
    if tokens and tokens[-1] in _TRAILING_STREET_TYPES:
        candidates.append(" ".join(tokens[:-1]))

    return _unique_strings(candidates)


def _extract_maricopa_token(html: str) -> str:
    match = re.search(r"var g_token = '([^']+)'", html)
    if not match:
        raise ValueError("Maricopa Assessor search token not found in page response.")
    return match.group(1)


def _extract_lines_from_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return [_normalize_whitespace(line) for line in soup.get_text("\n", strip=True).splitlines() if _normalize_whitespace(line)]


def _line_after(lines: List[str], label: str) -> str:
    for idx, line in enumerate(lines):
        if line == label and idx + 1 < len(lines):
            return lines[idx + 1]
    return ""


def _normalize_property_result(item: dict) -> dict:
    return {
        "apn": _normalize_apn(str(item.get("APN", ""))),
        "owner": _normalize_whitespace(str(item.get("Ownership", ""))),
        "address": _format_assessor_address(
            str(item.get("SitusAddress", "")),
            str(item.get("SitusCity", "")),
            str(item.get("SitusZip", "")),
        ),
        "subdivision": _normalize_whitespace(str(item.get("SubdivisonName", ""))),
        "mcr": _normalize_whitespace(str(item.get("MCR", ""))),
        "section_township_range": _normalize_whitespace(str(item.get("SectionTownshipRange", ""))),
        "property_type": _normalize_whitespace(str(item.get("PropertyType", ""))),
        "rental_registered": bool(item.get("RentalID")),
    }


def _normalize_rental_result(item: dict) -> dict:
    return {
        "apn": _normalize_apn(str(item.get("APN", ""))),
        "address": _format_assessor_address(
            str(item.get("Address", "")),
            str(item.get("City", "")),
            str(item.get("Zip", "")),
        ),
        "owner": _normalize_whitespace(str(item.get("Owner", ""))),
        "contact": _normalize_whitespace(str(item.get("Contact", ""))),
        "agent": _normalize_whitespace(str(item.get("Agent", ""))),
        "property": _normalize_whitespace(str(item.get("Property", ""))),
    }


def _extract_search_result_address(item: dict) -> str:
    return _format_assessor_address(
        str(item.get("SitusAddress") or item.get("Address") or ""),
        str(item.get("SitusCity") or item.get("City") or ""),
        str(item.get("SitusZip") or item.get("Zip") or ""),
    )


def _score_search_candidate(query: str, results: List[dict], total: int, order: int) -> Tuple[int, int, int, int]:
    normalized_query = _normalize_address_for_match(query)
    normalized_apn = _normalize_apn(query) if _looks_like_apn(query) else ""

    exact_apn = int(bool(normalized_apn) and any(
        _normalize_apn(str(item.get("APN", ""))) == normalized_apn
        for item in results
    ))
    exact_address = int(bool(normalized_query) and any(
        _normalize_address_for_match(_extract_search_result_address(item)) == normalized_query
        for item in results
    ))
    return (exact_apn, exact_address, -total, -order)


def _maricopa_search(endpoint: str, query: str, limit: int = 10) -> dict:
    candidates = _assessor_query_candidates(query)
    if not candidates:
        raise ValueError("Query is required.")

    limit = max(1, min(limit, 25))

    with _http_client() as client:
        search_page = client.get(f"{_MARICOPA_BASE_URL}/mcs/", params={"q": candidates[0]})
        search_page.raise_for_status()
        token = _extract_maricopa_token(search_page.text)

        attempts: List[dict] = []
        candidate_matches: List[dict] = []
        for order, candidate in enumerate(candidates):
            response = client.get(
                f"{_MARICOPA_BASE_URL}/search/{endpoint}/",
                params={"q": candidate},
                headers={
                    **_HTTP_HEADERS,
                    "Authorization": token,
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("Results", []) if isinstance(payload, dict) else []
            attempts.append({
                "query": candidate,
                "total": int(payload.get("TOTAL", len(results))) if isinstance(payload, dict) else len(results),
            })
            if results:
                candidate_matches.append({
                    "order": order,
                    "query": candidate,
                    "total": int(payload.get("TOTAL", len(results))),
                    "results": results,
                })

        if candidate_matches:
            best_match = max(
                candidate_matches,
                key=lambda match: _score_search_candidate(
                    query,
                    match["results"],
                    match["total"],
                    match["order"],
                ),
            )
            return {
                "query": query,
                "query_used": best_match["query"],
                "attempts": attempts,
                "total": best_match["total"],
                "results": best_match["results"][:limit],
            }

    return {
        "query": query,
        "query_used": candidates[-1],
        "attempts": attempts,
        "total": 0,
        "results": [],
    }


def _parse_parcel_detail_text(raw_text: str, apn: str) -> dict:
    lines = _extract_lines_from_html(raw_text)
    joined = "\n".join(lines)

    summary_match = re.search(
        r"This is a (?P<parcel_type>.+?) parcel located at\s+(?P<address>.+?)\.\s+"
        r"The current owner is (?P<owner>.+?)\.\s+It is located in the (?P<subdivision>.+?) subdivision,\s+and MCR\s+"
        r"(?P<mcr>[A-Z0-9/-]+)\.\s+It was last sold on (?P<sale_date>\d{1,2}/\d{1,2}/\d{4}) for "
        r"(?P<sale_price>\$[\d,]+(?:\.\d{2})?)\.",
        joined,
        flags=re.IGNORECASE | re.DOTALL,
    )

    formatted_apn = next((line for line in lines if re.fullmatch(r"\d{3}-\d{2}-[0-9A-Z]+", line)), "")
    parcel_type = ""
    if formatted_apn:
        try:
            parcel_type = lines[lines.index(formatted_apn) + 1]
        except (ValueError, IndexError):
            parcel_type = ""

    detail = {
        "apn": _normalize_apn(apn),
        "formatted_apn": formatted_apn,
        "parcel_type": summary_match.group("parcel_type").strip().title() if summary_match else parcel_type,
        "property_address": summary_match.group("address").strip() if summary_match else _line_after(lines, "Property Information"),
        "current_owner": summary_match.group("owner").strip() if summary_match else _line_after(lines, "Owner Information"),
        "subdivision": summary_match.group("subdivision").strip() if summary_match else "",
        "mcr": summary_match.group("mcr").strip() if summary_match else _line_after(lines, "MCR #"),
        "mailing_address": _line_after(lines, "Mailing Address"),
        "deed_number": _line_after(lines, "Deed Number"),
        "last_deed_date": _line_after(lines, "Last Deed Date"),
        "sale_date": summary_match.group("sale_date").strip() if summary_match else _line_after(lines, "Sale Date"),
        "sale_price": summary_match.group("sale_price").strip() if summary_match else "",
        "detail_url": f"{_MARICOPA_BASE_URL}/mcs/?q={quote(_normalize_apn(apn))}&mod=pd",
    }

    if detail["deed_number"]:
        detail["recorder_document_url"] = _RECORDER_DETAIL_URL_TEMPLATE.format(
            recording_number=quote(detail["deed_number"])
        )
    else:
        detail["recorder_document_url"] = ""

    return detail


def _normalize_address_for_match(value: str) -> str:
    normalized = _compact_whitespace(value)
    normalized = normalized.replace(",", " ")
    normalized = re.sub(r"\bAZ\b", "", normalized)
    normalized = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", normalized)
    normalized = re.sub(r"[^0-9A-Z ]", " ", normalized)
    return _normalize_whitespace(normalized)


def _select_best_property_match(address: str, results: List[dict]) -> Optional[dict]:
    if not results:
        return None

    target = _normalize_address_for_match(address)
    target_tokens = set(target.split())

    best = None
    best_score = -1

    for result in results:
        candidate = _normalize_address_for_match(result.get("address", ""))
        candidate_tokens = set(candidate.split())
        score = len(target_tokens & candidate_tokens)
        if candidate == target:
            score += 100
        elif candidate.startswith(target) or target.startswith(candidate):
            score += 20

        if score > best_score:
            best = result
            best_score = score

    return best


def maricopa_assessor_search_property(query: str, limit: int = 10) -> str:
    """
    Search Maricopa County Assessor parcel records by address, APN, owner, or subdivision.

    Uses the official Maricopa County Assessor search endpoint and returns structured
    parcel matches with APN, owner, address, subdivision, and property type.

    :param query: Address, APN, owner name, or other parcel search query.
    :param limit: Maximum number of results to return (default 10, max 25).
    :return: JSON with normalized parcel search results from the official assessor source.
    :rtype: str
    """
    try:
        payload = _maricopa_search("rp", query, limit=limit)
        payload["results"] = [_normalize_property_result(item) for item in payload.get("results", [])]
        payload["source"] = "Maricopa County Assessor"
        return json.dumps(payload)
    except Exception as exc:
        return json.dumps({"error": f"Maricopa property search failed: {type(exc).__name__}: {exc}"})


def maricopa_assessor_search_rental(query: str, limit: int = 10) -> str:
    """
    Search Maricopa County rental-registration records by address.

    This uses the official Maricopa County Assessor rental-registration endpoint,
    which may expose owner/contact/agent fields when populated.

    :param query: Address or parcel search string.
    :param limit: Maximum number of results to return (default 10, max 25).
    :return: JSON with normalized rental-registration matches.
    :rtype: str
    """
    try:
        payload = _maricopa_search("rental", query, limit=limit)
        payload["results"] = [_normalize_rental_result(item) for item in payload.get("results", [])]
        payload["source"] = "Maricopa County Assessor Rental Registration"
        return json.dumps(payload)
    except Exception as exc:
        return json.dumps({"error": f"Maricopa rental search failed: {type(exc).__name__}: {exc}"})


def maricopa_assessor_get_parcel_details(apn: str) -> str:
    """
    Fetch parcel detail information from the Maricopa County Assessor detail page.

    The detail page includes current owner, mailing address, deed number, last deed date,
    sale date, sale price, and subdivision metadata when available.

    :param apn: Assessor Parcel Number, with or without punctuation.
    :return: JSON with normalized parcel details and recorder document URL when available.
    :rtype: str
    """
    try:
        normalized_apn = _normalize_apn(apn)
        if not normalized_apn:
            raise ValueError("APN is required.")

        with _http_client() as client:
            response = client.get(f"{_MARICOPA_BASE_URL}/mcs/", params={"q": normalized_apn, "mod": "pd"})
            response.raise_for_status()
        details = _parse_parcel_detail_text(response.text, normalized_apn)
        details["source"] = "Maricopa County Assessor"
        return json.dumps(details)
    except Exception as exc:
        return json.dumps({"error": f"Maricopa parcel detail lookup failed: {type(exc).__name__}: {exc}"})


def maricopa_recorder_document_url(recording_number: str) -> str:
    """
    Build the official Maricopa County Recorder document-details URL for a deed or recording number.

    The recorder site often requires an interactive browser session, so this helper returns the
    canonical official URL the agent or user should open next.

    :param recording_number: Recorder document or deed number (for example 20210264544).
    :return: JSON with the canonical recorder document-details URL.
    :rtype: str
    """
    normalized = re.sub(r"[^0-9]", "", recording_number or "")
    if not normalized:
        return json.dumps({"error": "Recording number is required."})

    return json.dumps({
        "recording_number": normalized,
        "document_details_url": _RECORDER_DETAIL_URL_TEMPLATE.format(recording_number=quote(normalized)),
        "source": "Maricopa County Recorder",
        "note": "The recorder site may require an interactive browser session due to bot protection.",
    })


def adre_entity_license_search(
    query: str,
    search_field: str = "business_name",
    limit: int = 10,
) -> str:
    """
    Search Arizona Department of Real Estate entity licenses by company DBA, legal name, or license number.

    This is useful for finding whether a management company is licensed, and for getting the
    official detail page that includes phone, address, and designated broker information.

    :param query: Search value for the requested field.
    :param search_field: One of "business_name", "legal_name", or "license_number".
    :param limit: Maximum number of results to return (default 10).
    :return: JSON with parsed ADRE entity-license search results.
    :rtype: str
    """
    field_map = {
        "business_name": "BusinessName",
        "legal_name": "LegalName",
        "license_number": "LicenseNo",
    }

    try:
        search_key = field_map.get((search_field or "").strip().lower())
        if not search_key:
            raise ValueError('search_field must be "business_name", "legal_name", or "license_number".')
        if not _normalize_whitespace(query):
            raise ValueError("Query is required.")

        with _http_client() as client:
            response = client.get(_ADRE_ENTITY_SEARCH_URL)
            response.raise_for_status()

            token_match = re.search(
                r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"',
                response.text,
            )
            if not token_match:
                raise ValueError("ADRE request verification token not found.")

            form_data = {
                "__RequestVerificationToken": token_match.group(1),
                "LicenseNo": "",
                "LegalName": "",
                "BusinessName": "",
                "City": "",
                "Zip": "",
                "County": "",
                "LicenseStatusId": "1",
                "LicenseTypeId": "1",
                "OfficeTypeId": "1",
            }
            form_data[search_key] = _normalize_whitespace(query)

            results_page = client.post(_ADRE_ENTITY_SEARCH_URL, data=form_data)
            results_page.raise_for_status()

        soup = BeautifulSoup(results_page.text, "html.parser")
        table = soup.find("table", id="dataTableEntityLicenses")
        results: List[dict] = []
        if table:
            for row in table.find_all("tr")[1:]:
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                if len(cells) < 7:
                    continue
                link = row.find("a", href=True)
                results.append({
                    "license_number": cells[1],
                    "legal_name": cells[2],
                    "business_name": cells[3],
                    "status": cells[4],
                    "city": cells[5],
                    "zip": cells[6],
                    "detail_url": f"{_ADRE_BASE_URL}{link['href']}" if link else "",
                })

        return json.dumps({
            "query": query,
            "search_field": search_field,
            "total": len(results),
            "results": results[: max(1, min(limit, 25))],
            "source": "Arizona Department of Real Estate",
        })
    except Exception as exc:
        return json.dumps({"error": f"ADRE entity-license search failed: {type(exc).__name__}: {exc}"})


def adre_entity_license_details(record_id: str = "", detail_url: str = "") -> str:
    """
    Fetch detailed Arizona Department of Real Estate entity-license information.

    The detail page includes license status, phone, business address, mailing address,
    and designated broker details when available.

    :param record_id: ADRE entity-license record ID from the search result detail URL.
    :param detail_url: Full ADRE entity-license detail URL. Use this or record_id.
    :return: JSON with parsed entity-license details.
    :rtype: str
    """
    try:
        target_url = _normalize_whitespace(detail_url)
        if not target_url:
            normalized_id = re.sub(r"[^0-9]", "", record_id or "")
            if not normalized_id:
                raise ValueError("record_id or detail_url is required.")
            target_url = f"{_ADRE_BASE_URL}/PdbWeb/EntityLicense/ViewEntityLicense/{normalized_id}"

        with _http_client() as client:
            response = client.get(target_url)
            response.raise_for_status()

        lines = _extract_lines_from_html(response.text)
        details = {
            "detail_url": target_url,
            "license_number": _line_after(lines, "License Number"),
            "legal_name": _line_after(lines, "Legal Name"),
            "business_name": _line_after(lines, "Business (DBA) Name"),
            "license_status": _line_after(lines, "License Status"),
            "license_type": _line_after(lines, "License Type"),
            "original_date": _line_after(lines, "Original Date"),
            "expiration_date": _line_after(lines, "Expiration Date"),
            "phone": _line_after(lines, "Phone"),
            "business_address": _normalize_whitespace(
                " ".join(
                    part for part in [
                        _line_after(lines, "Business Address"),
                        lines[lines.index("Business Address") + 2] if "Business Address" in lines and lines.index("Business Address") + 2 < len(lines) else "",
                    ] if part
                )
            ),
            "mailing_address": _normalize_whitespace(
                " ".join(
                    part for part in [
                        _line_after(lines, "Mailing Address"),
                        lines[lines.index("Mailing Address") + 2] if "Mailing Address" in lines and lines.index("Mailing Address") + 2 < len(lines) else "",
                    ] if part
                )
            ),
            "designated_broker_license_number": "",
            "designated_broker_name": "",
            "designated_broker_license_type": "",
            "source": "Arizona Department of Real Estate",
        }

        if "Designated Broker Details" in lines:
            start = lines.index("Designated Broker Details")
            broker_lines = lines[start:]
            details["designated_broker_license_number"] = _line_after(broker_lines, "License Number")
            details["designated_broker_name"] = _line_after(broker_lines, "Name")
            details["designated_broker_license_type"] = _line_after(broker_lines, "License Type")

        return json.dumps(details)
    except Exception as exc:
        return json.dumps({"error": f"ADRE entity-license detail lookup failed: {type(exc).__name__}: {exc}"})


def arizona_apartment_public_records_lookup(address: str, management_name: str = "") -> str:
    """
    Run a bundled Arizona apartment public-record lookup for ownership, deed, rental, and management data.

    This one-shot helper combines Maricopa Assessor parcel search, parcel detail lookup, rental registration,
    recorder document URL generation, and optional ADRE management-company licensing research.

    :param address: Apartment property address or parcel query.
    :param management_name: Optional management-company name to search in ADRE.
    :return: JSON bundle with the best parcel match, owner detail, deed link, rental registration, and ADRE results.
    :rtype: str
    """
    try:
        property_search = json.loads(maricopa_assessor_search_property(address, limit=5))
        if property_search.get("error"):
            return json.dumps(property_search)

        best_match = _select_best_property_match(address, property_search.get("results", []))
        parcel_details = {}
        recorder = {}
        if best_match and best_match.get("apn"):
            parcel_details = json.loads(maricopa_assessor_get_parcel_details(best_match["apn"]))
            if parcel_details.get("deed_number"):
                recorder = json.loads(maricopa_recorder_document_url(parcel_details["deed_number"]))

        rental_search = json.loads(maricopa_assessor_search_rental(address, limit=5))

        adre_search = {}
        adre_details = {}
        if _normalize_whitespace(management_name):
            adre_search = json.loads(
                adre_entity_license_search(management_name, search_field="business_name", limit=5)
            )
            first_adre = (adre_search.get("results") or [None])[0]
            if first_adre and first_adre.get("detail_url"):
                adre_details = json.loads(adre_entity_license_details(detail_url=first_adre["detail_url"]))

        return json.dumps({
            "address": address,
            "best_property_match": best_match,
            "property_search": property_search,
            "parcel_details": parcel_details,
            "rental_registration": rental_search,
            "recorder_document": recorder,
            "management_name": management_name,
            "adre_entity_search": adre_search,
            "adre_entity_details": adre_details,
            "source_summary": [
                "Maricopa County Assessor",
                "Maricopa County Recorder",
                "Arizona Department of Real Estate",
            ],
        })
    except Exception as exc:
        return json.dumps({"error": f"Arizona apartment public-record lookup failed: {type(exc).__name__}: {exc}"})
