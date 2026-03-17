import requests
from bs4 import BeautifulSoup
import re

url = "https://romsfun.com/download/jet-impulse-116561"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

resp = requests.get(url, headers=headers)
soup = BeautifulSoup(resp.content, 'html.parser')

scripts = soup.find_all('script')
for s in scripts:
    if s.string and ('download' in s.string.lower() or 'href' in s.string.lower()):
        print("-" * 20)
        print(s.string[:500])
        # Try to find a URL
        urls = re.findall(r'https?://[^\s"\']+', s.string)
        if urls:
            print("URLs in script:", urls)
            
# Also check hidden inputs in forms
forms = soup.find_all('form')
for f in forms:
    print("Form found:")
    print(f.get('action'))
    inputs = f.find_all('input')
    for i in inputs:
        print(f"  {i.get('name')}: {i.get('value')}")
