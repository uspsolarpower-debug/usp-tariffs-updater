import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from google import genai
from google.genai import types

GIST_ID = os.environ.get("GIST_ID", "7b239656b37d7bb1e3bf9ffa761633b7").strip()
GIST_TOKEN = os.environ.get("GIST_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def extract_json_object(text: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError(f"لم يتم العثور على JSON صالح في رد Gemini:\n{text}")
    return json.loads(match.group(0))


def is_valid_rate(val, min_v: float, max_v: float) -> bool:
    return isinstance(val, (int, float)) and min_v <= float(val) <= max_v


def fetch_current_gist(gist_id: str, token: str) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    raw_content = data["files"]["tariffs.json"]["content"]
    return json.loads(raw_content)


def update_gist(gist_id: str, token: str, payload: dict) -> None:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
    }
    body = {
        "files": {
            "tariffs.json": {
                "content": json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            }
        }
    }
    resp = requests.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json=body, timeout=15)
    resp.raise_for_status()


def main() -> int:
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY secret is missing.", file=sys.stderr)
        return 1
    if not GIST_TOKEN:
        print("ERROR: GIST_TOKEN secret is missing.", file=sys.stderr)
        return 1

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_data = fetch_current_gist(GIST_ID, GIST_TOKEN)
    print("Current Gist data:", current_data)

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
ابحث عبر Google Search في المصادر المصرية الرسمية والمعتمدة (مثل egyptera.org، sis.gov.eg، petroleum.gov.eg، والمواقع الإخبارية الرسمية المصرية) عن أحدث أسعار رسمية سارية في مصر بتاريخ اليوم ({today_str}) لكل من:
1. سعر لتر السولار (الديزل) الرسمي في مصر بالجنيه المصري.
2. سعر كيلووات ساعة الكهرباء لأغراض الزراعة والري في مصر (تحويل القرش إلى جنيه، مثلاً 265 قرش = 2.65).
3. سعر كيلووات ساعة الكهرباء للقطاع التجاري في مصر (أعلى الشرائح فوق 1000 ك.و.س، مثلاً 279 قرش = 2.79).
4. احسب المتوسط الموحد للكهرباء بين الزراعي والتجاري.

اشترط التحقق من أكثر من مصدر وتاريخ آخر قرار رسمي ساري.
أرجع النتيجة بصيغة JSON فقط بدون أي نص إضافي بهذا الشكل الدقيق:
{{
  "electricityTariffEgp": 2.70,
  "agriElectricityRateEgp": 2.65,
  "commercialElectricityRateEgp": 2.79,
  "dieselLiterPriceEgp": 20.50,
  "updatedAt": "{today_str}",
  "sources": ["source1", "source2"]
}}
""".strip()

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.0,
    )

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
        config=config,
    )

    parsed = extract_json_object(response.text or "")
    print("Gemini search output:", parsed)

    agri = round(float(parsed["agriElectricityRateEgp"]), 2)
    comm = round(float(parsed["commercialElectricityRateEgp"]), 2)
    elec = round(float(parsed.get("electricityTariffEgp") or ((agri + comm) / 2.0)), 2)
    diesel = round(float(parsed["dieselLiterPriceEgp"]), 2)

    if not (is_valid_rate(agri, 1.0, 15.0) and is_valid_rate(comm, 1.0, 15.0) and is_valid_rate(elec, 1.0, 15.0)):
        raise ValueError(f"أسعار كهرباء غير منطقية: agri={agri}, comm={comm}, elec={elec}")
    if not is_valid_rate(diesel, 5.0, 100.0):
        raise ValueError(f"سعر سولار غير منطقي: diesel={diesel}")

    new_payload = {
        "electricityTariffEgp": elec,
        "agriElectricityRateEgp": agri,
        "commercialElectricityRateEgp": comm,
        "dieselLiterPriceEgp": diesel,
        "updatedAt": str(parsed.get("updatedAt") or today_str),
    }

    keys_to_compare = [
        "electricityTariffEgp",
        "agriElectricityRateEgp",
        "commercialElectricityRateEgp",
        "dieselLiterPriceEgp",
    ]
    if all(current_data.get(k) == new_payload[k] for k in keys_to_compare):
        print("No price changes detected. Gist left untouched.")
        return 0

    update_gist(GIST_ID, GIST_TOKEN, new_payload)
    print("Gist updated successfully:", new_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
