import requests
import json


urls = [
    "https://raw.githubusercontent.com/Yswag/uzVideo/main/js/spider_sources.json",
    "https://raw.githubusercontent.com/YYDS678/uzVideo/main/video_sources_default.json",
    "https://raw.githubusercontent.com/YYDS678/uzVideo/main/video_sources_sese.json"
]

def fetch_data():
    combined_data = []

    for url in urls:
        try:
            print(f"Fetching data from {url}...")
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            combined_data.extend(data)
            print(f"Successfully fetched data from {url}.")
        except Exception as e:
            print(f"Error fetching {url}: {e}")

    with open('UZ.json', 'w', encoding='utf-8') as f:
        json.dump(combined_data, f, ensure_ascii=False, indent=4)

    print("Data fetched and saved to UZ.json")

fetch_data()
