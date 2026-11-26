#!/usr/bin/env python3
"""
Обновява data.json с актуалните промоции в Lidl, Kaufland и BILLA (София).
Безплатен стек: Tavily (търсене) + Groq (AI). Вади всяка верига поотделно,
с пауза между заявките, за да се спази безплатният лимит на Groq (~8000 токена/мин).
При грешка/малко данни НЕ презаписва data.json (пази последните добри данни).
"""
import os, sys, json, re, time, datetime, pathlib, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"

TAVILY_KEY = os.environ.get("TAVILY_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL")
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
CATS = "Месо и риба | Млечни | Плодове и зеленчуци | Основни продукти | Напитки | Домакинство и хигиена | Друго"
AGG = ["proomo.info", "geobuyer.bg", "broshura.bg", "katalozi.bg", "kaufland.bg", "lidl.bg", "billa.bg"]
MODELS_FALLBACK = ["openai/gpt-oss-120b", "openai/gpt-oss-20b",
                   "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]


def http_post(url, payload, headers, timeout=90):
    hdrs = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tavily_context(chain):
    q = f"{chain} България брошура промоции цени тази седмица"
    try:
        res = http_post("https://api.tavily.com/search",
                        {"api_key": TAVILY_KEY, "query": q, "search_depth": "advanced",
                         "max_results": 5, "include_raw_content": True, "include_domains": AGG}, {})
    except Exception as e:
        print(f"WARN: Tavily грешка за {chain}: {e}", file=sys.stderr)
        return ""
    parts = []
    for r in res.get("results", []):
        txt = (r.get("raw_content") or r.get("content") or "")[:3500]
        if txt.strip():
            parts.append(f"ИЗТОЧНИК {r.get('url','')}\n{txt}")
    return ("\n\n".join(parts))[:8000]


def model_list():
    out, seen = [], set()
    for m in ([GROQ_MODEL] if GROQ_MODEL else []) + MODELS_FALLBACK:
        if m and m not in seen:
            seen.add(m); out.append(m)
    return out


def groq_json(system, user):
    last = ""
    for model in model_list():
        try:
            res = http_post("https://api.groq.com/openai/v1/chat/completions",
                            {"model": model, "temperature": 0.1, "max_tokens": 2800,
                             "response_format": {"type": "json_object"},
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}]},
                            {"Authorization": f"Bearer {GROQ_KEY}"})
            return res["choices"][0]["message"]["content"], model
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} ({model}): {e.read().decode('utf-8','ignore')[:200]}"
            if e.code in (400, 404, 413, 429):
                print(f"WARN: {last} — следващ модел", file=sys.stderr)
                time.sleep(2); continue
            print(f"ERROR: Groq {last}", file=sys.stderr); return None, None
        except Exception as e:
            last = f"{model}: {e}"; print(f"WARN: {last}", file=sys.stderr); continue
    print(f"ERROR: нито един Groq модел не сработи. {last}", file=sys.stderr)
    return None, None


def parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None


def extract_chain(chain):
    ctx = tavily_context(chain)
    if len(ctx) < 300:
        print(f"WARN: малко съдържание за {chain}", file=sys.stderr)
        return {"ok": False, "note": "не е проверено днес"}, []
    system = "Връщаш САМО валиден JSON обект. Без markdown, без обяснения."
    user = f"""Днес е {date_label} ({today.isoformat()}). От текста по-долу извлечи промоциите само на верига {chain} в България.

Върни JSON: {{"store_status": {{"ok": true, "note": "период на брошурата накратко"}}, "items": [ {{"id":"...","product":"...","size":"...","price":0.0,"old_price":null,"price_label":"...","unit_label":"...","discount":0,"category":"...","valid_to":"ДД.ММ","condition":"...","top":false}} ] }}

Правила:
- Извлечи колкото реални продукта намериш (цел 12–25). НЕ измисляй цени/отстъпки/дати — ако ги няма в текста, пропусни продукта.
- "category" е точно една от: {CATS}.
- "id": кратък идентификатор от първата буква на веригата + продукта, напр. за {chain}: "{chain[0].lower()}-ime".
- "price" е число (напр. 2.55). "price_label" и "unit_label" за показване (напр. "2,55 €/кг", "6,36 €/кг"). Цените са в евро.
- Маркирай 4–6-те най-изгодни с "top": true.
- Ако в текста няма реални оферти за {chain}, върни "ok": false и празен "items".

ТЕКСТ:
{ctx}

Само JSON обекта."""
    raw, model = groq_json(system, user)
    data = parse_json(raw)
    if not data:
        print(f"WARN: невалиден JSON за {chain}", file=sys.stderr)
        return {"ok": False, "note": "грешка при обработка"}, []
    status = data.get("store_status") or {"ok": True, "note": ""}
    items = data.get("items") or []
    for it in items:
        it["store"] = chain
    print(f"INFO: {chain}: {len(items)} продукта (модел {model})", file=sys.stderr)
    return status, items


def main():
    all_items, statuses = [], []
    chains = ["Kaufland", "BILLA", "Lidl"]
    for idx, chain in enumerate(chains):
        st, items = extract_chain(chain)
        statuses.append({"store": chain, "ok": st.get("ok", True), "note": st.get("note", "")})
        all_items.extend(items)
        if idx < len(chains) - 1:
            time.sleep(65)  # изчакване заради лимита токени/минута

    # премахни дубликати по id
    seen, uniq = set(), []
    for it in all_items:
        iid = it.get("id") or (it.get("store", "") + it.get("product", ""))
        if iid in seen:
            continue
        seen.add(iid); uniq.append(it)

    if len(uniq) < 10:
        print(f"ERROR: твърде малко продукти общо ({len(uniq)}) — пазя старите данни", file=sys.stderr)
        sys.exit(1)

    ok_stores = [s["store"] for s in statuses if s["ok"]]
    rec = ("Виж топ офертите по-долу. " +
           (f"Проверени магазини днес: {', '.join(ok_stores)}. " if ok_stores else "") +
           "Обърни внимание на офертите, които изтичат скоро.")

    data = {
        "date": today.isoformat(),
        "date_label": date_label,
        "city": "София",
        "currency": "евро (€)",
        "generated_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "recommendation": rec,
        "stores_status": statuses,
        "items": uniq,
    }
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: записани {len(uniq)} продукта за {today.isoformat()}")


if __name__ == "__main__":
    main()
