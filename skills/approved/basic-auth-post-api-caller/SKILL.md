---
name: basic-auth-post-api-caller
description: Send HTTP POST requests to APIs protected by HTTP Basic Authentication
  with configurable headers and JSON or raw request bodies.
---

## Purpose
Send HTTP POST requests to APIs protected by HTTP Basic Authentication with configurable headers and JSON or raw request bodies.

## Agent instructions
The skill accepts a single JSON object as input.

Inputs:
- url (string, required): Full HTTPS or HTTP endpoint URL.
- username (string, required): Username for HTTP Basic Authentication.
- password (string, required): Password for HTTP Basic Authentication.
- body (object|string, optional): Request payload. Objects are serialized as JSON. Strings are sent as-is.
- headers (object, optional): Additional request headers.
- timeout_seconds (integer, optional): Request timeout in seconds. Defaults to 30.

Behavior:
- Construct an HTTP Basic Authentication header using the provided username and password.
- If body is an object, serialize it to JSON and set Content-Type to application/json unless already provided.
- If body is a string, send it as raw UTF-8 text.
- Merge user-provided headers with generated headers.
- Execute an HTTP POST request to the provided URL.
- Return a JSON-serializable object containing success status, HTTP status code, response headers, and response body.
- On network or serialization failure, return success=false with an error message.

Output format:
{
  "success": true,
  "status_code": 200,
  "response_headers": {"content-type": "application/json"},
  "response_body": "..."
}

Failure format:
{
  "success": false,
  "error": "..."
}

## Input contract
```json
{
  "url": {
    "type": "string",
    "required": true
  },
  "username": {
    "type": "string",
    "required": true
  },
  "password": {
    "type": "string",
    "required": true
  },
  "body": {
    "type": "object",
    "required": false,
    "default": {}
  },
  "headers": {
    "type": "object",
    "required": false,
    "default": {}
  },
  "timeout_seconds": {
    "type": "integer",
    "required": false,
    "default": 30
  }
}
```

## Output contract
A useful result that fulfills the purpose of basic-auth-post-api-caller.

## Executable contract
The Python implementation must expose `run(inputs: dict)` and return the result. Helper functions are allowed, but `run` must call them as needed. Do not use `main()` or printed text as the skill result.
