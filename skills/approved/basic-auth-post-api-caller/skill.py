import base64
import json
import urllib.request
import urllib.error


def run(inputs):
    url = inputs["url"]
    username = inputs["username"]
    password = inputs["password"]
    body = inputs.get("body", {})
    headers = dict(inputs.get("headers", {}))
    timeout_seconds = int(inputs.get("timeout_seconds", 30))

    try:
        credentials = f"{username}:{password}".encode("utf-8")
        encoded_credentials = base64.b64encode(credentials).decode("utf-8")
        headers["Authorization"] = f"Basic {encoded_credentials}"

        if isinstance(body, dict):
            data = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            return {
                "success": False,
                "error": "body must be either an object or a string"
            }

        request = urllib.request.Request(
            url=url,
            data=data,
            headers=headers,
            method="POST"
        )

        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {
                "success": True,
                "status_code": response.getcode(),
                "response_headers": dict(response.headers.items()),
                "response_body": response_body
            }

    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        return {
            "success": False,
            "status_code": error.code,
            "response_headers": dict(error.headers.items()) if error.headers else {},
            "response_body": error_body,
            "error": str(error)
        }
    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }
