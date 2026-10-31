---
name: city-weather-lookup
description: Fetch current weather details for a city using the free wttr.in weather
  API without authentication.
---

## Purpose
Fetch current weather details for a city using the free wttr.in weather API without authentication.

## Agent instructions
This skill retrieves current weather information for a provided city name using the wttr.in JSON API.

Inputs:
- city (string, required): City name to look up.

Operation:
1. URL-encode the city name.
2. Send an HTTP GET request to https://wttr.in/{city}?format=j1.
3. Parse the JSON response.
4. Extract the current weather details from current_condition[0].
5. Return a JSON object with:
   - success (boolean)
   - city (string)
   - temperature_c (string)
   - feels_like_c (string)
   - humidity (string)
   - weather_desc (string)
   - wind_kph (string)
   - observation_time (string)
6. If the request fails or the city is invalid, return:
   - success: false
   - error: error message string

Output Contract:
Returns a JSON-serializable object containing weather details or an error.

## Input contract
```json
{
  "city": {
    "type": "string",
    "required": true,
    "description": "City name to retrieve weather for"
  }
}
```

## Output contract
A useful result that fulfills the purpose of City Weather Lookup.

## Executable contract
The Python implementation must expose `run(inputs: dict)` and return the result. Helper functions are allowed, but `run` must call them as needed. Do not use `main()` or printed text as the skill result.
