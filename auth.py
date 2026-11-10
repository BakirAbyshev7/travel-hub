"""Личный кабинет: пользователи, сессии, сохранённые поездки. Хранение — JSON-файлы в data/."""
import hashlib, hmac, json, os, re, secrets, threading, time, uuid

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SESSION_TTL = 30 * 24 * 3600
COOKIE = "sid"
_lock = threading.RLock()


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _load(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(name, obj):
    # пишем во временный файл и подменяем — файл не побьётся при сбое посреди записи
    path = os.path.join(DATA, name)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)


# ---------- пароли ----------
def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${digest}"


def check_password(password, stored):
    salt = stored.split("$", 1)[0]
    return hmac.compare_digest(hash_password(password, salt), stored)


def _check_new_password(password):
    if len(password) < 6:
        raise AuthError("Пароль должен быть не короче 6 символов")


def public(user):
    return {k: user.get(k) for k in ("id", "email", "name", "phone", "created")}


# ---------- регистрация и вход ----------
def register(email, password, name):
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AuthError("Введите корректный email")
    if not name:
        raise AuthError("Введите имя")
    _check_new_password(password or "")
    with _lock:
        users = _load("users.json", [])
        if any(u["email"] == email for u in users):
            raise AuthError("Пользователь с таким email уже зарегистрирован", 409)
        user = {"id": uuid.uuid4().hex[:12], "email": email, "name": name, "phone": "",
                "password": hash_password(password), "created": time.strftime("%Y-%m-%d %H:%M")}
        users.append(user)
        _save("users.json", users)
    return user


def login(email, password):
    email = (email or "").strip().lower()
    user = next((u for u in _load("users.json", []) if u["email"] == email), None)
    if not user or not check_password(password or "", user["password"]):
        raise AuthError("Неверный email или пароль", 401)
    return user


# ---------- сессии (в файле храним только хэш токена) ----------
def _token_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    with _lock:
        now = time.time()
        sessions = {k: s for k, s in _load("sessions.json", {}).items() if s["expires"] > now}
        sessions[_token_key(token)] = {"user_id": user_id, "expires": now + SESSION_TTL}
        _save("sessions.json", sessions)
    return token


def user_by_token(token):
    if not token:
        return None
    s = _load("sessions.json", {}).get(_token_key(token))
    if not s or s["expires"] < time.time():
        return None
    return next((u for u in _load("users.json", []) if u["id"] == s["user_id"]), None)


def destroy_session(token):
    with _lock:
        sessions = _load("sessions.json", {})
        if sessions.pop(_token_key(token or ""), None):
            _save("sessions.json", sessions)


# ---------- профиль ----------
def update_profile(user_id, name, phone):
    name = (name or "").strip()
    if not name:
        raise AuthError("Имя не может быть пустым")
    with _lock:
        users = _load("users.json", [])
        user = next(u for u in users if u["id"] == user_id)
        user["name"], user["phone"] = name, (phone or "").strip()
        _save("users.json", users)
    return user


def change_password(user_id, old, new):
    _check_new_password(new or "")
    with _lock:
        users = _load("users.json", [])
        user = next(u for u in users if u["id"] == user_id)
        if not check_password(old or "", user["password"]):
            raise AuthError("Текущий пароль указан неверно", 403)
        user["password"] = hash_password(new)
        _save("users.json", users)


# ---------- поездки и брони ----------
def trips_of(user_id):
    return [t for t in _load("trips.json", []) if t["user_id"] == user_id][::-1]


def add_trip(user_id, trip):
    if not any(trip.get(k) for k in ("ticket", "hotel", "car")):
        raise AuthError("Поездка пустая: выберите билет, отель или автомобиль")
    rec = {"id": uuid.uuid4().hex[:8], "user_id": user_id, "created": time.strftime("%Y-%m-%d %H:%M"),
           **{k: trip.get(k) for k in ("from", "to", "ticket", "hotel", "car", "total")}}
    with _lock:
        trips = _load("trips.json", [])
        trips.append(rec)
        _save("trips.json", trips)
    return rec


def delete_trip(user_id, trip_id):
    with _lock:
        trips = _load("trips.json", [])
        left = [t for t in trips if not (t["id"] == trip_id and t["user_id"] == user_id)]
        if len(left) == len(trips):
            raise AuthError("Поездка не найдена", 404)
        _save("trips.json", left)


def create_order(user, data, cars):
    """Заказ = билет + отель + авто. Сумму пересчитываем на сервере, клиенту не доверяем."""
    tk, h, c = data.get("ticket"), data.get("hotel"), data.get("car")
    if not (tk and h and c):
        raise AuthError("Для заказа выберите билет, отель и автомобиль")
    car = next((x for x in cars if x["id"] == c.get("car_id")), None)
    if not car:
        raise AuthError("Автомобиль не найден")
    days = max(1, int(c.get("days") or 1))
    c = {**c, "brand": car["brand"], "class": car["class"], "price_day": car["price_day"],
         "days": days, "total": car["price_day"] * days}
    cu = data.get("customer") or {}
    customer = {"name": (user or {}).get("name") or cu.get("name"), "email": (user or {}).get("email") or cu.get("email"),
                "phone": cu.get("phone") or (user or {}).get("phone")}
    if not customer["name"]:
        raise AuthError("Укажите имя покупателя")
    now = time.localtime()
    order = {
        "id": secrets.token_hex(8),  # секретная часть ссылки на PDF
        "number": time.strftime("PT-%y%m%d-", now) + f"{secrets.randbelow(10000):04d}",
        "user_id": user["id"] if user else None, "created": time.strftime("%Y-%m-%dT%H:%M:%S", now),
        "from": data.get("from"), "to": data.get("to"), "customer": customer,
        "ticket": tk, "hotel": h, "car": c, "total": round(tk.get("price") or 0) + c["total"],
    }
    with _lock:
        orders = _load("orders.json", [])
        orders.append(order)
        _save("orders.json", orders)
    return order


def get_order(order_id, user):
    """Свой заказ видит только владелец; заказ гостя — любой, у кого есть секретная ссылка."""
    order = next((o for o in _load("orders.json", []) if o["id"] == order_id), None)
    if not order or (order["user_id"] and (not user or user["id"] != order["user_id"])):
        raise AuthError("Заказ не найден", 404)
    return order


def orders_of(user_id):
    return [o for o in _load("orders.json", []) if o["user_id"] == user_id][::-1]


def bookings_of(user_id):
    return [b for b in _load("bookings.json", []) if b.get("user_id") == user_id][::-1]


def cancel_booking(user_id, booking_id):
    with _lock:
        bookings = _load("bookings.json", [])
        b = next((b for b in bookings if b["id"] == booking_id and b.get("user_id") == user_id), None)
        if not b:
            raise AuthError("Бронь не найдена", 404)
        b["status"] = "cancelled"
        _save("bookings.json", bookings)
