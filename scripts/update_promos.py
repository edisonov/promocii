#!/usr/bin/env python3
"""
Обновява data.json с актуалните промоции в Lidl, Kaufland и BILLA (София).
Източник: текстовите седмични публикации на proomo.info (Blogger feed) — реални
таблици с цени. Структуриране с Groq (безплатно). Tavily е резервен източник.
При грешка/малко данни НЕ презаписва data.json (пази последните добри данни).
"""
import os, sys, json, re, time, html, datetime, pathlib, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"

GROQ_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY")  # по желание, резервен източник
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
FEED = "https://www.proomo.info/feeds/posts/default?alt=json&max-results=50"
MODELS_FALLBACK = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
CHAINS = [("Kaufland", "kaufland"), ("BILLA", "billa"), ("Lidl", "lidl")]


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def http_post(url, payload, headers, timeout=90):
    hdrs = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def strip_html(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = html.unescape(h)
    h = re.sub(r"[ \t ]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return h.strip()


def proomo_sources():
    """Връща {верига: текст} от най-новите публикации на proomo.info."""
    try:
        feed = json.loads(http_get(FEED))
        entries = feed.get("feed", {}).get("entry", [])
    except Exception as e:
        print(f"WARN: proomo feed грешка: {e}", file=sys.stderr)
        return {}
    out = {}
    for chain, kw in CHAINS:
        best = None
        for e in entries:
            title = e.get("title", {}).get("$t", "")
            if kw in title.lower():
                pub = e.get("published", {}).get("$t", "")
                if best is None or pub > best[0]:
                    text = strip_html(e.get("content", {}).get("$t", ""))[:7000]
                    best = (pub, text, title)
        if best:
            out[chain] = best[1]
            print(f"DEBUG {chain}: proomo '{best[2]}' len={len(best[1])}", file=sys.stderr)
    return out


def tavily_source(chain):
    if not TAVILY_KEY:
        return ""
    try:
        res = http_post("https://api.tavily.com/search",
                        {"api_key": TAVILY_KEY, "query": f"{chain} България промоции цени брошура",
                         "search_depth": "advanced", "max_results": 4, "include_raw_content": True,
                         "include_domains": ["proomo.info", "geobuyer.bg"]}, {})
    except Exception as e:
        print(f"WARN: Tavily грешка {chain}: {e}", file=sys.stderr)
        return ""
    parts = [(r.get("raw_content") or r.get("content") or "")[:3500] for r in res.get("results", [])]
    return ("\n\n".join(p for p in parts if p.strip()))[:7000]


def model_list():
    out, seen = [], set()
    for m in ([GROQ_MODEL] if GROQ_MODEL else []) + MODELS_FALLBACK:
        if m and m not in seen:
            seen.add(m); out.append(m)
    return out


def groq_once(model, system, user):
    # Без "response_format" (строгият JSON режим чупи gpt-oss) — сами разчитаме JSON от текста.
    res = http_post("https://api.groq.com/openai/v1/chat/completions",
                    {"model": model, "temperature": 0.1, "max_tokens": 4000,
                     "messages": [{"role": "system", "content": system},
                                  {"role": "user", "content": user}]},
                    {"Authorization": f"Bearer {GROQ_KEY}"})
    return res["choices"][0]["message"]["content"]


def groq_json(system, user):
    """Един модел на опит; при лимит (413/429) изчаква и пробва пак; при 400/404 сменя модела."""
    last = ""
    for model in model_list():
        for attempt in range(2):
            try:
                return groq_once(model, system, user), model
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} ({model}): {e.read().decode('utf-8','ignore')[:180]}"
                if e.code in (413, 429):
                    print(f"WARN: {last} — изчаквам 65с и пробвам пак", file=sys.stderr)
                    time.sleep(65); continue           # повтори същия модел след изчакване
                if e.code in (400, 404):
                    print(f"WARN: {last} — следващ модел", file=sys.stderr); break
                print(f"ERROR: Groq {last}", file=sys.stderr); return None, None
            except Exception as e:
                last = f"{model}: {e}"; print(f"WARN: {last}", file=sys.stderr); break
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


def extract_chain(chain, ctx):
    if len(ctx) < 300:
        print(f"WARN: малко съдържание за {chain}", file=sys.stderr)
        return {"ok": False, "note": "не е проверено днес"}, []
    system = "Връщаш САМО валиден JSON обект. Без markdown, без обяснения."
    user = f"""Днес е {date_label} ({today.isoformat()}). От текста по-долу (седмична брошура) извлечи промоциите на верига {chain} в България.

Върни JSON: {{"store_status": {{"ok": true, "note": "период на брошурата накратко"}}, "items": [ {{"id":"...","product":"...","size":"...","price":0.0,"old_price":null,"price_label":"...","unit_label":"...","discount":0,"category":"...","valid_to":"ДД.ММ","condition":"...","top":false}} ] }}

Правила:
- Извлечи колкото реални продукта с цени намериш (цел 12–25). НЕ измисляй цени/отстъпки/дати.
- "category" е точно една от: {CATS}.
- "id": кратка латиница от първа буква на веригата + продукта, напр. "{chain[0].lower()}-maslo".
- "price" е число (напр. 2.55). "price_label" за показване (напр. "2,55 €/кг" или "1,59 €"); "unit_label" цена за единица (напр. "6,36 €/кг") или "". Цените са в евро.
- "discount" цяло число проценти или null. "old_price" число или null.
- Маркирай 4–6-те най-изгодни с "top": true.

ТЕКСТ:
{ctx}

Само JSON обекта."""
    raw, model = groq_json(system, user)
    data = parse_json(raw)
    if not data:
        print(f"WARN: невалиден JSON за {chain}", file=sys.stderr)
        return {"ok": False, "note": "грешка при обработка"}, []
    status = data.get("store_status") or {"ok": True, "note": ""}
    items = [it for it in (data.get("items") or []) if isinstance(it, dict) and it.get("product") and it.get("price") is not None]
    for it in items:
        it["store"] = chain
    print(f"INFO: {chain}: {len(items)} продукта (модел {model})", file=sys.stderr)
    return status, items


def main():
    sources = proomo_sources()
    all_items, statuses = [], []
    for idx, (chain, _) in enumerate(CHAINS):
        ctx = sources.get(chain) or tavily_source(chain)
        st, items = extract_chain(chain, ctx)
        statuses.append({"store": chain, "ok": st.get("ok", True), "note": st.get("note", "")})
        all_items.extend(items)
        if idx < len(CHAINS) - 1:
            time.sleep(65)  # лимит токени/минута на безплатния Groq

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
           (f"Проверени днес: {', '.join(ok_stores)}. " if ok_stores else "") +
           "Обърни внимание на офертите, които изтичат скоро.")

    data = {
        "date": today.isoformat(), "date_label": date_label, "city": "София",
        "currency": "евро (€)",
        "generated_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "recommendation": rec, "stores_status": statuses, "items": uniq,
    }
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: записани {len(uniq)} продукта за {today.isoformat()}")


if __name__ == "__main__":
    main()
