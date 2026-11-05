import requests
from constants import HEADERS

url = "https://hs-consumer-api.espncricinfo.com/v1/pages/series/schedule?seriesId=1510719"
resp = requests.get(url, headers=HEADERS)
print("Status Code:", resp.status_code)
if resp.status_code != 200:
    print("Response text:", resp.text[:500])
else:
    print("Success without auth token!")
