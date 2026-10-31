import requests

response = requests.post(
  'https://free.mockerapi.com/mock/a5eb8b2c-dc63-4b24-9e36-f9f214f9cf23',
  auth=('admin', 'admin')
)
print(response.json())

