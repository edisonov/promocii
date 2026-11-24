#!/usr/bin/env python3
"""
Обновява data.json с актуалните промоции в Lidl, Kaufland и BILLA (София).
Използва Google Gemini API (безплатен ключ) + Google Search grounding.
При грешка или невалиден резултат НЕ презаписва data.json (пази последните добри данни).
"""
import os, sys, json, re, datetime, pathlib

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("ERROR: липсва пакетът 'google-genai' (pip install google-genai)", file=sys.stderr)
    sys.exit(1)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
if not API_KEY:
    print("ERROR: липсва GEMINI_API_KEY", file=sys.stderr)
    sys.exit(1)

today = datetime.date.today()
BG_DAYS = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък", "Събота", "Неделя"]
BG_MONTHS = ["", "януари", "февруари", "март", "април", "май", "юни", "юли",
             "август", "септември", "октомври", "ноември", "декември"]
date_label = f"{BG_DAYS[today.weekday()]}, {today.day} {BG_MONTHS[today.month]} {today.year}"

SCHEMA = r"""
{
  "date": "ГГГГ-ММ-ДД",
  "date_label": "напр. Вторник, 24 ноември 2026",
  "city": "София",
  "currency": "евро (€)",
  "generated_at": "ISO време",
  "recommendation": "кратък текст: кои 1–2 магазина си струват днес",
  "stores_status": [
    {"store": "Kaufland", "ok": true, "note": "период/статус на проверката"},
    {"store": "BILLA", "ok": true, "note": "..."},
    {"store": "Lidl", "ok": "partial", "note": "..."}
  ],
  "items": [
    {
      "id": "кратък стабилен идентификатор, напр. k-mandarini (един и същ за същия продукт през дните)",
      "store": "Lidl | Kaufland | BILLA",
      "product": "име на продукта",
      "size": "грамаж/обем или кг/бр.",
      "price": 2.55,
      "old_price": 6.75,
      "price_label": "за показване, напр. 2,55 €/кг или 1,59 €",
      "unit_label": "цена за единица, напр. 6,36 €/кг, или празно",
      "discount": 62,
      "category": "една от: Месо и риба | Млечни | Плодове и зеленчуци | Основни продукти | Напитки | Домакинство и хигиена | Друго",
      "valid_to": "ДД.ММ",
      "condition": "без карта или изискването, напр. BILLA Card",
      "top": true
    }
  ]
}
"""

PROMPT = f"""Днес е {date_label} ({today.isoformat()}). Ти си агент за изгодно пазаруване в София, България.

Събери АКТУАЛНИТЕ промоции във веригите Lidl, Kaufland и BILLA в България, като търсиш в Google. Фокус върху ежедневни стоки: месо и риба, млечни, плодове и зеленчуци, основни продукти (кафе, олио, брашно, захар, ориз, хляб), напитки, домакинство и хигиена. Включи и 1–3 нехранителни оферти само ако отстъпката е много голяма (категория „Друго“).

Използвай източници като официалните сайтове (kaufland.bg, lidl.bg, billa.bg) и агрегатори на брошури (proomo.info, geobuyer.bg, broshura.bg, katalozi.bg). Цените са в евро (€). НЕ измисляй цени, отстъпки или дати — ако нещо не е проверено, не го включвай. Изчисли цена за единица (€/кг, €/л) където е възможно. Отбележи кои оферти изискват карта/приложение.

Върни РЕЗУЛТАТА САМО като валиден JSON по следната схема (без markdown ограждане, без обяснения, само JSON обектът):
{SCHEMA}

Изисквания:
- "date" = {today.isoformat()}, "date_label" = "{date_label}".
- 30–60 продукта в "items"; маркирай ~15-те най-изгодни с "top": true, останалите "top": false.
- Стабилни "id" (напр. "k-mandarini" за мандарини в Kaufland), за да се пазят списъците за пазаруване на потребителя между дните.
- "stores_status" за трите вериги с кратка бележка за периода на брошурата; ok = true/false/"partial".
- Ако някоя верига не може да се провери, отбележи го в stores_status и просто не добавяй нейни продукти.
Върни само JSON обекта, нищо друго."""


def extract_json(text):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return None
    return text[i:j + 1]


def main():
    client = genai.Client(api_key=API_KEY)
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=PROMPT,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
                max_output_tokens=16000,
            ),
        )
    except Exception as e:
        print(f"ERROR: неуспешно извикване на Gemini ({MODEL}): {e}", file=sys.stderr)
        sys.exit(1)

    text = getattr(resp, "text", None)
    raw = extract_json(text)
    if not raw:
        print("ERROR: не намерих JSON в отговора", file=sys.stderr)
        print((text or "")[:2000], file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: невалиден JSON: {e}", file=sys.stderr)
        print(raw[:2000], file=sys.stderr)
        sys.exit(1)

    items = data.get("items")
    if not isinstance(items, list) or len(items) < 10:
        n = len(items) if isinstance(items, list) else "няма"
        print(f"ERROR: твърде малко продукти ({n}) — пазя старите данни", file=sys.stderr)
        sys.exit(1)

    data.setdefault("date", today.isoformat())
    data.setdefault("date_label", date_label)
    data.setdefault("city", "София")
    data.setdefault("currency", "евро (€)")
    data["generated_at"] = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: записани {len(items)} продукта за {data['date']}")


if __name__ == "__main__":
    main()
