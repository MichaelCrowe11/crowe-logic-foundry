"""
Crowe Logic platform client — HTTP tools for ai.southwestmushrooms.com.

Provides access to CroweLM chat, Crowe Vision, grow logs, and SOP generation.
"""

import base64
import json
import os

import httpx


def crowe_chat(message: str, context: str = "") -> str:
    """
    Call the external Crowe Logic platform at ai.southwestmushrooms.com for
    *mycology and cultivation domain questions only*. Requires
    CROWE_LOGIC_KEY (or an active trial session cookie). DO NOT use this
    tool for greetings, meta-questions, or general conversation — the
    active model answers those directly.

    :param message: A mushroom-cultivation or mycology domain question.
    :param context: Optional conversation context.
    :return: JSON with the platform response.
    :rtype: str
    """
    # Hard guard: refuse non-cultivation messages without making an HTTP call.
    # The router model sometimes ignores the docstring and tries crowe_chat
    # for greetings ("hi", "who are you?"). Returning a clear refusal here
    # both prevents 400s on the upstream API and stops the model from looping
    # because the tool result is now actionable rather than a vague error.
    msg_lower = (message or "").strip().lower()
    cultivation_signals = (
        "mushroom", "shroom", "myco", "fungal", "fungi", "mycelium",
        "mycelia", "spore", "agar", "grain", "substrate", "fruiting",
        "flush", "pin", "pinning", "colonize", "colonization", "inoculat",
        "contamin", "tek", "monotub", "shotgun", "shiitake", "oyster",
        "lion's mane", "lions mane", "reishi", "cordyceps", "psilocybe",
        "cubensis", "cultivation", "grow log", "grow tent", "humidity",
        "fae", "sterile", "autoclave", "pressure cook", "petri",
    )
    looks_meta = (
        not msg_lower
        or len(msg_lower) < 4
        or msg_lower in {
            "hi", "hello", "hey", "yo", "sup", "thanks", "thank you",
            "ok", "okay", "cool", "nice", "great",
        }
        or any(msg_lower.startswith(p) for p in (
            "who are you", "whob are you", "what are you", "what is your",
            "what's your", "whats your", "how are you", "how's it going",
            "hows it going", "introduce yourself", "tell me about yourself",
            "what can you do", "what do you do", "help",
        ))
    )
    has_cultivation_term = any(sig in msg_lower for sig in cultivation_signals)
    if looks_meta or not has_cultivation_term:
        return json.dumps({
            "error": "crowe_chat is for mycology/cultivation domain questions only. "
                     "Answer this message directly without calling a tool.",
            "guidance": "Do not retry crowe_chat for this message.",
        })

    try:
        payload = {"messages": [{"role": "user", "content": message}]}
        if context:
            payload["messages"].insert(0, {"role": "system", "content": context})
        result = _crowe_request("POST", "/api/chat", json=payload)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowe_vision(image_path: str, prompt: str = "Analyze this image") -> str:
    """
    Analyze an image using Crowe Vision (photo analysis for cultivation).

    :param image_path: Path to the image file.
    :param prompt: What to analyze about the image.
    :return: JSON with vision analysis results.
    :rtype: str
    """
    try:
        if not os.path.exists(image_path):
            return json.dumps({"error": f"File not found: {image_path}"})

        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        result = _crowe_request("POST", "/api/crowe-vision/analyze", json={
            "image": image_b64,
            "prompt": prompt,
        })
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowe_grow_log(action: str, data: str = "{}") -> str:
    """
    Manage grow logs on the Crowe Logic AI platform.

    :param action: Operation — "create", "read", "update", or "list".
    :param data: JSON string with log data (for create/update) or filters (for read/list).
    :return: JSON with grow log data.
    :rtype: str
    """
    try:
        parsed_data = json.loads(data)

        if action == "list":
            result = _crowe_request("GET", "/api/conversations")
        elif action == "create":
            result = _crowe_request("POST", "/api/conversations", json=parsed_data)
        elif action == "read":
            log_id = parsed_data.get("id", "")
            result = _crowe_request("GET", f"/api/conversations/{log_id}")
        elif action == "update":
            log_id = parsed_data.pop("id", "")
            result = _crowe_request("PATCH", f"/api/conversations/{log_id}", json=parsed_data)
        else:
            return json.dumps({"error": f"Unknown action: {action}. Use: create, read, update, list"})

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowe_generate_sop(topic: str, parameters: str = "{}") -> str:
    """
    Generate a Standard Operating Procedure for cultivation tasks.

    :param topic: The SOP topic (e.g., "substrate preparation", "fruiting chamber setup").
    :param parameters: JSON string with additional parameters (species, scale, etc.).
    :return: JSON with the generated SOP document.
    :rtype: str
    """
    try:
        parsed_params = json.loads(parameters)
        parsed_params["topic"] = topic

        result = _crowe_request("POST", "/api/chat", json={
            "message": f"Generate a detailed Standard Operating Procedure for: {topic}",
            "context": json.dumps(parsed_params),
        })
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _crowe_request(method: str, path: str, **kwargs) -> dict:
    """Send an authenticated request to the Crowe Logic platform."""
    url = os.environ.get("CROWE_LOGIC_URL", "https://ai.southwestmushrooms.com")
    key = os.environ.get("CROWE_LOGIC_KEY", "")

    headers = kwargs.pop("headers", {})
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.setdefault("Content-Type", "application/json")

    response = httpx.request(method, f"{url}{path}", headers=headers, timeout=60.0, **kwargs)
    response.raise_for_status()
    return response.json()
