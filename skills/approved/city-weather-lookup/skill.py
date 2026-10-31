import json
import urllib.parse
import urllib.request


def run(inputs):
    city = inputs.get('city')

    if not city or not isinstance(city, str):
        return {
            'success': False,
            'error': 'city must be a non-empty string'
        }

    encoded_city = urllib.parse.quote(city)
    url = f'https://wttr.in/{encoded_city}?format=j1'

    try:
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0'
            }
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        current = data.get('current_condition', [])
        if not current:
            return {
                'success': False,
                'error': 'No weather data found'
            }

        current = current[0]
        weather_desc = ''
        descriptions = current.get('weatherDesc', [])
        if descriptions and isinstance(descriptions, list):
            weather_desc = descriptions[0].get('value', '')

        return {
            'success': True,
            'city': city,
            'temperature_c': current.get('temp_C', ''),
            'feels_like_c': current.get('FeelsLikeC', ''),
            'humidity': current.get('humidity', ''),
            'weather_desc': weather_desc,
            'wind_kph': current.get('windspeedKmph', ''),
            'observation_time': current.get('observation_time', '')
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
