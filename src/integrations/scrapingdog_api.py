import json
import os
from typing import Any, Dict, List

import dotenv
import requests

dotenv.load_dotenv()

# type this function to list of the following type
# {
#     "title": "Meta CTO explains why the smart glasses demos ... - TechCrunch",
#     "displayed_link": "https://techcrunch.com › 2025/09/19 › meta-cto-explains-why-the-smart-glasses-d...",
#     "snippet": "Meta CTO Andrew Bosworth offered a postmortem on Meta's demo fails this week at its developer conference, where it showed off new smart ...",
#     "date": "2 days ago",
#     "missing": [],
#     "link": "https://techcrunch.com/2025/09/19/meta-cto-explains-why-the-smart-glasses-demos-failed-at-meta-connect-and-it-wasnt-the-wi-fi/",
#     "favicon": "https://www.google.com/s2/favicons?domain=techcrunch.com",
#     "rank": 1
# }
# return list of the following type


def searchGoogle(query: str, maxResults: int = 10) -> List[Dict[str, Any]]:
    """
    Search Google using the ScrapingDog API
    return list of the following type

        "title": "Meta CTO explains why the smart glasses demos ... - TechCrunch",
        "displayed_link": "https://techcrunch.com › 2025/09/19 › meta-cto-explains-why-the-smart-glasses-d...",
        "snippet": "Meta CTO Andrew Bosworth offered a postmortem on Meta's demo fails this week at its developer conference, where it showed off new smart ...",
        "date": "2 days ago",
        "missing": [],
        "link": "https://techcrunch.com/2025/09/19/meta-cto-explains-why-the-smart-glasses-demos-failed-at-meta-connect-and-it-wasnt-the-wi-fi/",
        "favicon": "https://www.google.com/s2/favicons?domain=techcrunch.com",
        "rank": 1
    }
    """
    api_key = os.getenv("SCRAPPING_DOG_API_KEY")
    url = "https://api.scrapingdog.com/google"

    params = {
        "api_key": api_key,
        "query": query,
        "results": maxResults,
        "country": "us",
        "domain": "google.com"
    }

    response = requests.get(url, params=params)

    if response.status_code == 200:
        data = response.json()
        print(data["organic_results"])
        with open("last_response_from_sdg.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data["organic_results"]
    else:
        print(f"Request failed with status code: {response.status_code}")
        return []


if __name__ == "__main__":
    searchGoogle(
        "Meta Ray‑Ban Display AR glasses gesture recognition failure at Meta Connect 2025")
