---
name: basic-python-calculator
description: Reusable calculator skill that performs basic arithmetic operations including
  addition, subtraction, multiplication, and division.
---

## Purpose
Reusable calculator skill that performs basic arithmetic operations including addition, subtraction, multiplication, and division.

## Agent instructions
Use this skill when arithmetic operations need to be performed in a reusable workflow. Input must be a JSON object with keys: operation (string: add|subtract|multiply|divide), a (number), and b (number). The skill validates inputs, performs the requested arithmetic operation, and returns a JSON object with operation, operands, and result. For division, reject division by zero with a structured error response. Output format: {"success": boolean, "operation": string, "a": number, "b": number, "result": number|null, "error": string|null}.

## Input contract
```json
{
  "type": "object",
  "properties": {
    "operation": {
      "type": "string",
      "enum": [
        "add",
        "subtract",
        "multiply",
        "divide"
      ]
    },
    "a": {
      "type": "number"
    },
    "b": {
      "type": "number"
    }
  },
  "required": [
    "operation",
    "a",
    "b"
  ]
}
```

## Output contract
[object Object]

## Executable contract
The Python implementation must expose `run(inputs: dict)` and return the result. Helper functions are allowed, but `run` must call them as needed. Do not use `main()` or printed text as the skill result.
