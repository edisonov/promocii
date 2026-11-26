#!/usr/bin/env python3
"""
Обновява data.json с актуалните промоции в Lidl, Kaufland и BILLA (София).
Безплатен стек: Tavily (търсене в уеб) + Groq (AI за структуриране).
При грешка или невалиден резултат НЕ презаписва data.json (пази последните добри данни).
"""
import os, sys, json, re, datetime, pathlib, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"

TAVILY_KEY = os.environ.get("TAVILY_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
if not TAVILY_KEY:
    print("ERROR: липсва TAVILY_API_KEY", file=sys.stderr); sys.exit(1)
if not GROQ_KEY:
    print("ERROR: липсва GROQ_API_KEY", file=sys.stderr); sys.exit(1)

today = datetime.date.today()
BG_DAYS = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък", "Събота", "Неделя"]
BG_MONTHS = ["", "януари", "февруари", "март", "април", "май", "юни", "юли",
             "август", "септември", "октомври", "ноември", "декември"]
date_label = f"{BG_DAYS[today.weekday()]}, {today.day} {BG_MONTHS[today.month]} {today.year}"


UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def http_post(url, payload, headers, timeout=90):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tavily_search(query, max_results=4):
    try:
        res = http_post(
            "https://api.tavily.com/search",
            {"api_key": TAVILY_KEY, "query": query, "search_depth": "advanced",
             "max_results": max_results, "include_raw_content": True},
            {"Content-Type": "application/json"},
        )
    except Exception as e:
        print(f"WARN: Tavily грешка за '{query}': {e}", file=sys.stderr)
        return []
    out = []
    for r in res.get("results", []):
        txt = (r.get("raw_content") or r.get("content") or "")[:6000]
        if txt.strip():
            out.append(f"ИЗТОЧНИК: {r.get('url','')}\n{txt}")
    return out


def gather_context():
    queries = [
        "Kaufland България актуална брошура промоции цени тази седмица",
        "BILLA България седмична брошура промоции цени",
        "Lidl България брошура промоции цени тази седмица",
        "proomo.info Kaufland BILLA Lidl промоции тази седмица",
    ]
    chunks = []
    for q in queries:
        chunks.extend(tavily_search(q))
    blob = "\n\n---\n\n".join(chunks)
    return blob[:45000]  # пази контекста разумен


SCHEMA = r"""{
  "date": "ГГГГ-ММ-ДД", "date_label": "...", "city": "София", "currency": "евро (€)",
  "generated_at": "ISO", "recommendation": "кои 1-2 магазина си струват днес",
  "stores_status": [{"store":"Kaufland","ok":true,"note":"период"},{"store":"BILLA","ok":true,"note":"..."},{"store":"Lidl","ok":"partial","note":"..."}],
  "items": [{"id":"k-mandarini","store":"Kaufland","product":"Мандарини","size":"кг","price":0.95,"old_price":null,"price_label":"0,95 €/кг","unit_label":"0,95 €/кг","discount":56,"category":"Плодове и зеленчуци","valid_to":"29.11","condition":"без карта","top":true}]
}"""

CATS = "Месо и риба | Млечни | Плодове и зеленчуци | Основни продукти | Напитки | Домакинство и хигиена | Друго"


def groq_models():
    # Подреден списък с кандидат-модели; първият работещ се ползва.
    cands = []
    if GROQ_MODEL:
        cands.append(GROQ_MODEL)
    cands += ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b",
              "llama-3.1-8b-instant", "gemma2-9b-it"]
    seen, out = set(), []
    for m in cands:
        if m not in seen:
            seen.add(m); out.append(m)
    return out


def groq_extract(context):
    sys_msg = ("Ти си помощник, който връща САМО валиден JSON обект по подадена схема. "
               "Не добавяй обяснения или markdown.")
    user_msg = f"""Днес е {date_label} ({today.isoformat()}). По-долу са извадки от уеб източници за промоциите в Lidl, Kaufland и BILLA в България (София).

Състави JSON със следната схема (ТОЧНО тези полета):
{SCHEMA}

Правила:
- "date"="{today.isoformat()}", "date_label"="{date_label}", "city"="София", "currency"="евро (€)".
- Извлечи 25–55 реални продукта от текста. НЕ измисляй цени, отстъпки или дати — ако ги няма в текста, не добавяй продукта.
- "category" е точно една от: {CATS}.
- "id": кратък стабилен идентификатор (напр. "k-mandarini"), еднакъв за същия продукт.
- "price" е число; "price_label" и "unit_label" за показване (напр. "2,55 €/кг", "6,36 €/кг").
- Маркирай ~15-те най-изгодни с "top": true, останалите false.
- "stores_status": за трите вериги, с кратка бележка; ok=true/false/"partial". Ако верига липсва в текста — ok:false и без нейни продукти.
- "valid_to" във формат "ДД.ММ".

ТЕКСТ ОТ ИЗТОЧНИЦИТЕ:
{context}

Върни само JSON обекта."""
    last_err = ""
    for model in groq_models():
        try:
            res = http_post(
                "https://api.groq.com/openai/v1/chat/completions",
                {"model": model, "temperature": 0.2, "max_tokens": 8000,
                 "response_format": {"type": "json_object"},
                 "messages": [{"role": "system", "content": sys_msg},
                              {"role": "user", "content": user_msg}]},
                {"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_KEY}"},
            )
            print(f"INFO: използван модел {model}", file=sys.stderr)
            return res["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:400]
            last_err = f"HTTP {e.code} ({model}): {body}"
            # 404/400 → моделът липсва или не е достъпен; пробвай следващия
            if e.code in (400, 404):
                print(f"WARN: {last_err} — пробвам следващ модел", file=sys.stderr)
                continue
            # друга грешка (401 ключ, 429 лимит) → няма смисъл да сменяме модел
            print(f"ERROR: Groq {last_err}", file=sys.stderr); sys.exit(1)
        except Exception as e:
            last_err = f"{model}: {e}"
            print(f"WARN: Groq грешка {last_err} — пробвам следващ модел", file=sys.stderr)
            continue
    print(f"ERROR: нито един Groq модел не сработи. Последна грешка: {last_err}", file=sys.stderr)
    sys.exit(1)


def extract_json(text):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if (i != -1 and j > i) else None


def main():
    context = gather_context()
    if len(context) < 500:
        print("ERROR: търсенето върна твърде малко съдържание — пазя старите данни", file=sys.stderr); sys.exit(1)

    raw = extract_json(groq_extract(context))
    if not raw:
        print("ERROR: не намерих JSON в отговора", file=sys.stderr); sys.exit(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: невалиден JSON: {e}", file=sys.stderr); sys.exit(1)

    items = data.get("items")
    if not isinstance(items, list) or len(items) < 10:
        n = len(items) if isinstance(items, list) else "няма"
        print(f"ERROR: твърде малко продукти ({n}) — пазя старите данни", file=sys.stderr); sys.exit(1)

    data["date"] = today.isoformat()
    data["date_label"] = date_label
    data.setdefault("city", "София")
    data.setdefault("currency", "евро (€)")
    data["generated_at"] = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: записани {len(items)} продукта за {data['date']}")


if __name__ == "__main__":
    main()
