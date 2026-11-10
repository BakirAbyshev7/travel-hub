"""Travel Hub: билеты -> жильё -> аренда авто. Только стандартная библиотека Python."""
import json, os, re, threading, time, uuid, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import auth
import report

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
DATA = os.path.join(ROOT, "data")
UA = "travel-hub-lab/1.0 (student project)"


def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
PORT = int(os.environ.get("PORT", "8000"))

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
_cache = {}


def http(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ---------- билеты (Aviasales / Travelpayouts) ----------
def places(term):
    q = urllib.parse.urlencode({"term": term, "locale": "ru"}) + "&types[]=city"
    items = http("https://autocomplete.travelpayouts.com/places2?" + q, timeout=10)
    return [
        {"code": i["code"], "name": i["name"], "country": i.get("country_name", ""),
         "lat": (i.get("coordinates") or {}).get("lat"), "lon": (i.get("coordinates") or {}).get("lon")}
        for i in items if i.get("type") == "city"
    ][:8]


def rub_to_kgs():
    """Курс RUB -> KGS (сом), обновляется раз в 6 часов."""
    def fetch():
        try:
            return http("https://open.er-api.com/v6/latest/RUB", timeout=10)["rates"]["KGS"]
        except Exception:
            return 1.07  # запасной курс, если сервис курсов недоступен
    return cached("kgs", 6 * 3600, fetch)


def airport_tz():
    """Часовые пояса аэропортов (IATA -> 'Europe/Moscow'), справочник обновляется раз в сутки."""
    def fetch():
        try:
            return {a["code"]: a["time_zone"] for a in http("https://api.travelpayouts.com/data/ru/airports.json")
                    if a.get("time_zone")}
        except Exception:
            return {}
    return cached("tz", 24 * 3600, fetch)


def arrival(departure, minutes, airport):
    """Время прилёта по местному времени аэропорта прибытия."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    try:
        arr = datetime.fromisoformat(departure) + timedelta(minutes=minutes)
        tz = airport_tz().get(airport)
        return (arr.astimezone(ZoneInfo(tz)) if tz else arr).isoformat()
    except Exception:
        return None


def flights(p):
    if not TOKEN:
        return {"error": "no_token"}
    # без даты API отдаёт рейсы на ближайшие дни/месяцы
    q = {"origin": p["origin"], "destination": p["destination"],
         "currency": "rub", "sorting": "price", "limit": 100, "unique": "false",
         "one_way": "false" if p.get("return_date") else "true", "token": TOKEN}
    if p.get("date"):
        q["departure_at"] = p["date"]
    if p.get("return_date"):
        q["return_at"] = p["return_date"]
    res = http("https://api.travelpayouts.com/aviasales/v3/prices_for_dates?" + urllib.parse.urlencode(q))
    rate = rub_to_kgs()
    out = []
    for t in res.get("data", []):
        out.append({
            "from": t.get("origin"), "to": t.get("destination"),
            "from_airport": t.get("origin_airport"), "to_airport": t.get("destination_airport"),
            "airline": t.get("airline"), "flight": t.get("flight_number"),
            "departure": t.get("departure_at"), "return": t.get("return_at"),
            "transfers": t.get("transfers"), "duration": t.get("duration_to") or t.get("duration"),
            "arrival": arrival(t.get("departure_at"), t.get("duration_to") or t.get("duration") or 0,
                               t.get("destination_airport")),
            "price": round((t.get("price") or 0) * rate), "link": "https://www.aviasales.ru" + (t.get("link") or ""),
        })
    if not p.get("date"):
        out.sort(key=lambda t: t["departure"] or "")
    return {"tickets": out, "nearest": not p.get("date")}


# ---------- жильё (OpenStreetMap: реальные отели города) ----------
def geocode(city):
    q = urllib.parse.urlencode({"q": city, "format": "json", "limit": 1})
    r = http("https://nominatim.openstreetmap.org/search?" + q, timeout=15)
    if not r:
        return None
    return float(r[0]["lat"]), float(r[0]["lon"])


def overpass(query):
    """Опрашиваем все зеркала параллельно, берём первый удачный ответ."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    body = urllib.parse.urlencode({"data": query}).encode()
    ex = ThreadPoolExecutor(len(OVERPASS))
    futs = [ex.submit(http, u, body, 25) for u in OVERPASS]
    try:
        for f in as_completed(futs):
            try:
                return f.result()
            except Exception:
                continue
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("серверы OpenStreetMap сейчас перегружены")


def overpass_hotels(lat, lon):
    query = (f'[out:json][timeout:25];nwr["tourism"~"^(hotel|hostel|guest_house|apartment)$"]["name"]'
             f'(around:7000,{lat},{lon});out center 80;')
    out = []
    for e in overpass(query).get("elements", []):
        t = e.get("tags", {})
        c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
        if c.get("lat") is None:
            continue
        addr = ", ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x)
        out.append({
            "id": f'{e["type"]}{e["id"]}', "name": t.get("name:ru") or t["name"],
            "kind": t.get("tourism"), "stars": t.get("stars"), "address": addr,
            "phone": t.get("phone") or t.get("contact:phone"),
            "website": t.get("website") or t.get("contact:website"),
            "image": t.get("image"), "lat": c["lat"], "lon": c["lon"],
        })
    out.sort(key=lambda h: (h["stars"] is None, -(float(h["stars"][0]) if h["stars"] and h["stars"][0].isdigit() else 0)))
    return out


def nominatim_hotels(lat, lon):
    """Быстрый источник: поиск отелей через Nominatim в окне вокруг центра."""
    d = 0.08
    q = urllib.parse.urlencode({"q": "hotel", "format": "json", "limit": 40, "bounded": 1, "extratags": 1,
                                "viewbox": f"{lon-d},{lat+d},{lon+d},{lat-d}", "accept-language": "ru"})
    out = []
    for r in http("https://nominatim.openstreetmap.org/search?" + q, timeout=20):
        x = r.get("extratags") or {}
        out.append({"id": f'{r["osm_type"]}{r["osm_id"]}', "name": r.get("name") or r["display_name"].split(",")[0],
                    "kind": "hotel", "stars": x.get("stars"), "address": "", "phone": x.get("phone"),
                    "website": x.get("website"), "lat": float(r["lat"]), "lon": float(r["lon"])})
    return out


# Кэш отелей на диске: повторный поиск города — мгновенно, даже после перезапуска сервера.
HOTEL_CACHE = os.path.join(DATA, "hotels_cache.json")
HOTEL_TTL = 7 * 24 * 3600
_hotel_lock = threading.Lock()
_inflight = {}
POOL = ThreadPoolExecutor(8)          # запросы к источникам
SEARCH_POOL = ThreadPoolExecutor(4)   # сами поиски (отдельно, чтобы не было взаимной блокировки)


def hotel_cache_get(key):
    with _hotel_lock:
        hit = read_json("hotels_cache.json", {}).get(key)
    return hit if hit and time.time() - hit["ts"] < HOTEL_TTL else None


def hotel_cache_put(key, value, full):
    with _hotel_lock:
        cache = read_json("hotels_cache.json", {})
        old = cache.get(key)
        if old and old.get("full") and not full:
            return  # не затираем полный результат Overpass быстрым из Nominatim
        cache[key] = {"ts": time.time(), "full": full, **value}
        write_json("hotels_cache.json", cache)


def hotels(city, lat=None, lon=None):
    key = city.strip().lower()
    hit = hotel_cache_get(key)
    if hit:
        return {"hotels": hit["hotels"], "center": hit["center"], "cached": True}
    with _hotel_lock:  # одинаковые одновременные запросы (например, предзагрузка) делят одну работу
        fut = _inflight.get(key)
        if not fut:
            fut = _inflight[key] = SEARCH_POOL.submit(_search_hotels, key, city, lat, lon)
            fut.add_done_callback(lambda _: _inflight.pop(key, None))
    return fut.result()


def _search_hotels(key, city, lat, lon):
    if lat is None or lon is None:
        pos = geocode(city)
        if not pos:
            return {"hotels": [], "center": None}
        lat, lon = pos
    center = {"lat": lat, "lon": lon}

    def save_full(f):
        if not f.exception() and f.result():
            hotel_cache_put(key, {"hotels": f.result(), "center": center}, True)

    # Оба источника стартуют одновременно. Overpass даёт больше данных, но часто перегружен:
    # ждём его 2,5 секунды, иначе отдаём быстрый Nominatim, а Overpass дописывает кэш в фоне.
    full = POOL.submit(overpass_hotels, lat, lon)
    full.add_done_callback(save_full)
    quick = POOL.submit(nominatim_hotels, lat, lon)
    try:
        res = full.result(timeout=2.5)
        if res:
            return {"hotels": res, "center": center}
    except Exception:
        pass
    try:
        res = quick.result(timeout=20)
    except Exception:
        res = full.result(timeout=30)  # Nominatim упал — дожидаемся Overpass
    hotel_cache_put(key, {"hotels": res, "center": center}, False)
    return {"hotels": res, "center": center, "fallback": True}


# ---------- авто (вручную: data/cars.json) ----------
def read_json(name, default):
    path = os.path.join(DATA, name)
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def write_json(name, obj):
    json.dump(obj, open(os.path.join(DATA, name), "w", encoding="utf-8"), ensure_ascii=False, indent=2)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=PUBLIC, **kw)

    def send_json(self, obj, status=200, headers=()):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def token(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[auth.COOKIE].value if auth.COOKIE in c else None

    def user(self):
        return auth.user_by_token(self.token())

    def session_cookie(self, token, max_age=auth.SESSION_TTL):
        return ("Set-Cookie", f"{auth.COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}")

    def account(self, user):
        return {"user": auth.public(user), "trips": auth.trips_of(user["id"]),
                "bookings": auth.bookings_of(user["id"]), "orders": auth.orders_of(user["id"])}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/api/places":
                return self.send_json(cached(("pl", p.get("term")), 600, lambda: places(p.get("term", ""))))
            if u.path == "/api/flights":
                return self.send_json(flights(p))
            if u.path == "/api/hotels":
                lat = float(p["lat"]) if p.get("lat") else None
                lon = float(p["lon"]) if p.get("lon") else None
                return self.send_json(hotels(p.get("city", ""), lat, lon))
            if u.path == "/api/cars":
                cars = read_json("cars.json", [])
                city = p.get("city", "").strip().lower()
                if city:
                    cars = [c for c in cars if city in c["city"].lower() or c["city"] == "*"]
                return self.send_json(cars)
            if u.path == "/api/me":
                user = self.user()
                return self.send_json({"user": auth.public(user) if user else None})
            if u.path == "/api/account":
                user = self.user()
                if not user:
                    return self.send_json({"error": "Войдите в аккаунт"}, 401)
                return self.send_json(self.account(user))
            m = re.fullmatch(r"/api/orders/([0-9a-f]{16})/pdf", u.path)
            if m:
                return self.send_pdf(auth.get_order(m.group(1), self.user()))
            if u.path == "/api/config":
                return self.send_json({"flights_enabled": bool(TOKEN)})
        except auth.AuthError as e:
            return self.send_json({"error": str(e)}, e.status)
        except Exception as e:
            return self.send_json({"error": str(e)}, 502)
        super().do_GET()

    def send_pdf(self, order):
        pdf = report.build_pdf(order)
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(pdf)))
        # inline — открывается во вкладке браузера, откуда его можно скачать или распечатать
        self.send_header("Content-Disposition", f'inline; filename="order-{order['number']}.pdf"')
        self.end_headers()
        self.wfile.write(pdf)

    def do_POST(self):
        # JSON-only: обычная форма с чужого сайта не сможет отправить такой запрос (защита от CSRF)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send_json({"error": "Ожидается JSON"}, 415)
        n = int(self.headers.get("Content-Length", 0))
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send_json({"error": "Некорректный JSON"}, 400)
        path, user = self.path, self.user()
        try:
            if path in ("/api/auth/register", "/api/auth/login"):
                if path.endswith("register"):
                    u = auth.register(b.get("email"), b.get("password"), b.get("name"))
                else:
                    u = auth.login(b.get("email"), b.get("password"))
                token = auth.create_session(u["id"])
                return self.send_json({"user": auth.public(u)}, headers=[self.session_cookie(token)])
            if path == "/api/auth/logout":
                auth.destroy_session(self.token())
                return self.send_json({"ok": True}, headers=[self.session_cookie("", 0)])
            if path == "/api/cars/book":
                return self.book_car(b, user)
            if path == "/api/orders":
                order = auth.create_order(user, b, read_json("cars.json", []))
                return self.send_json({"id": order["id"], "number": order["number"], "total": order["total"]})
            if not user:
                return self.send_json({"error": "Войдите в аккаунт"}, 401)
            if path == "/api/account/profile":
                auth.update_profile(user["id"], b.get("name"), b.get("phone"))
            elif path == "/api/account/password":
                auth.change_password(user["id"], b.get("old"), b.get("new"))
            elif path == "/api/trips":
                auth.add_trip(user["id"], b)
            elif path == "/api/trips/delete":
                auth.delete_trip(user["id"], b.get("id"))
            elif path == "/api/bookings/cancel":
                auth.cancel_booking(user["id"], b.get("id"))
            else:
                return self.send_json({"error": "not found"}, 404)
            return self.send_json(self.account(self.user()))
        except auth.AuthError as e:
            return self.send_json({"error": str(e)}, e.status)

    def book_car(self, b, user):
        if not all(b.get(k) for k in ("car_id", "name", "phone", "date_from", "date_to")):
            return self.send_json({"error": "Заполните все поля"}, 400)
        rec = {"id": uuid.uuid4().hex[:8], "created": time.strftime("%Y-%m-%d %H:%M:%S"),
               "status": "active", "user_id": user["id"] if user else None,
               **{k: b.get(k) for k in ("car_id", "car", "city", "hotel", "name", "phone", "date_from", "date_to", "total")}}
        bookings = read_json("bookings.json", [])
        bookings.append(rec)
        write_json("bookings.json", bookings)
        self.send_json({"ok": True, "id": rec["id"]})


if __name__ == "__main__":
    print(f"Travel Hub: http://localhost:{PORT}  (билеты: {'ON' if TOKEN else 'нужен TRAVELPAYOUTS_TOKEN в .env'})")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
