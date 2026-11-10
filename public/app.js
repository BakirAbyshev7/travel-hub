const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const trip = { from: null, to: null, date: "", dateBack: "", ticket: null, hotel: null, car: null };
const som = n => Math.round(n).toLocaleString("ru-RU") + " сом";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// время рейса показываем местное (как в ответе API), без пересчёта в пояс браузера
const local = d => new Date(String(d).slice(0, 19) + "Z");
const time = d => d ? local(d).toISOString().slice(11, 16) : "";
const day = d => d ? local(d).toLocaleDateString("ru-RU", { day: "numeric", month: "short", weekday: "short", timeZone: "UTC" }) : "";
const dur = m => `${Math.floor(m / 60)} ч ${m % 60 ? (m % 60) + " мин" : ""}`.trim();
const api = async url => { const r = await fetch(url); const j = await r.json(); if (j.error && r.status >= 400) throw new Error(j.error); return j; };
const skeleton = (el, n = 4) => el.innerHTML = '<div class="sk"></div>'.repeat(n);
function toast(text) { const t = $("#toast"); t.textContent = text; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 3500); }

// ---------- навигация ----------
const HERO = {
  tickets: ["Дешёвые авиабилеты", "Сравниваем цены авиакомпаний, а потом подбираем отель и машину на месте"],
  hotels: ["Отели, хостелы и апартаменты", "Реальные места для проживания на карте города"],
  cars: ["Аренда автомобилей", "Без скрытых платежей, оплата при получении"],
  account: ["Личный кабинет", ""],
};
function go(step) {
  if (step === "account" && !me) return openAuth("login", () => go("account"));
  $(".hero").classList.toggle("compact", step === "account");
  $(".layout").classList.toggle("wide", step === "account");
  if (step === "account") loadAccount();
  $$(".panel").forEach(p => p.classList.toggle("active", p.id === step));
  $$("#steps button").forEach(b => b.classList.toggle("active", b.dataset.step === step));
  $$(".step-form").forEach(f => f.classList.toggle("active", f.dataset.for === step));
  $$("#stepper li").forEach(li => li.classList.toggle("active", li.dataset.step === step));
  [$("#heroTitle").textContent, $("#heroSub").textContent] = HERO[step];
  if (step === "cars") loadCars();
  if (step === "hotels" && map) setTimeout(() => map.invalidateSize(), 50);
  scrollTo({ top: 0, behavior: "smooth" });
}
$$("#steps button, #stepper li, [data-go]").forEach(b => b.onclick = () => go(b.dataset.step || b.dataset.go));

function nights() {
  const a = new Date($("#hin").value), b = new Date($("#hout").value);
  return Math.max(1, Math.round((b - a) / 864e5) || 1);
}
function renderTrip() {
  const set = (id, on, text) => { const r = $(id); r.classList.toggle("on", on); r.querySelector("small").innerHTML = text; };
  const t = trip.ticket;
  set("#tripFlight", !!t, t ? `${esc(trip.from.name)} → ${esc(trip.to.name)}<br>${day(t.departure)} · <b>${som(t.price)}</b>` : "не выбран");
  set("#tripHotel", !!trip.hotel, trip.hotel ? `${esc(trip.hotel.name)}<br>${nights()} ноч.` : "не выбран");
  set("#tripCar", !!trip.car, trip.car ? `${esc(trip.car.brand)}<br>${trip.car.days} сут. · <b>${som(trip.car.total)}</b>` : "не выбран");
  $("#tripTotal").textContent = som((t ? t.price : 0) + (trip.car ? trip.car.total : 0));
  const ready = !!(t && trip.hotel && trip.car);
  $("#checkout").disabled = !ready;
  $("#checkoutHint").hidden = ready;
  $("#stepper li[data-step=tickets]").classList.toggle("done", !!t);
  $("#stepper li[data-step=hotels]").classList.toggle("done", !!trip.hotel);
  $("#stepper li[data-step=cars]").classList.toggle("done", !!trip.car);
}

// ---------- автодополнение городов ----------
function suggest(inputId, listId, codeId, onPick) {
  const inp = $(inputId), ul = $(listId); let t;
  inp.addEventListener("input", () => {
    onPick(null); $(codeId).textContent = ""; clearTimeout(t);
    if (inp.value.length < 2) { ul.style.display = "none"; return; }
    t = setTimeout(async () => {
      try {
        const items = await api("/api/places?term=" + encodeURIComponent(inp.value));
        ul.innerHTML = "";
        items.forEach(c => {
          const li = document.createElement("li");
          li.innerHTML = `<div>${esc(c.name)}<br><span>${esc(c.country)}</span></div><span>${esc(c.code)}</span>`;
          li.onclick = () => { inp.value = c.name; $(codeId).textContent = c.code; ul.style.display = "none"; onPick(c); };
          ul.append(li);
        });
        ul.style.display = items.length ? "block" : "none";
      } catch {}
    }, 250);
  });
  document.addEventListener("click", e => { if (e.target !== inp) ul.style.display = "none"; });
}
suggest("#from", "#fromSug", "#fromCode", c => trip.from = c);
// как только известен город прилёта — заранее грузим его отели, чтобы шаг «Жильё» открылся мгновенно
function prefetchHotels(c) {
  if (c) fetch("/api/hotels?" + new URLSearchParams({ city: c.name, lat: c.lat, lon: c.lon })).catch(() => {});
}
suggest("#to", "#toSug", "#toCode", c => { trip.to = c; prefetchHotels(c); });
$("#swap").onclick = () => {
  [trip.from, trip.to] = [trip.to, trip.from];
  [$("#from").value, $("#to").value] = [$("#to").value, $("#from").value];
  [$("#fromCode").textContent, $("#toCode").textContent] = [$("#toCode").textContent, $("#fromCode").textContent];
};
const today = new Date().toISOString().slice(0, 10);
["#date", "#dateBack", "#hin", "#hout", "#cfrom", "#cto"].forEach(s => $(s).min = today);

// ---------- 1. билеты ----------
let tickets = [], sortBy = "price";
$("#ticketForm").onsubmit = async e => {
  e.preventDefault();
  const msg = $("#ticketMsg"), list = $("#ticketList");
  $("#ticketEmpty").hidden = true;
  // город набран вручную, но не выбран из списка — берём первое совпадение
  const resolve = async (inputId, codeId) => {
    const name = $(inputId).value.trim();
    if (name.length < 2) return null;
    const [c] = await api("/api/places?term=" + encodeURIComponent(name)).catch(() => []);
    if (c) $(codeId).textContent = c.code;
    return c || null;
  };
  trip.from = trip.from || await resolve("#from", "#fromCode");
  if (!trip.to) { trip.to = await resolve("#to", "#toCode"); prefetchHotels(trip.to); }
  if (!trip.from || !trip.to) { msg.className = "msg err"; msg.textContent = "Не удалось найти город «" + (trip.from ? $("#to").value : $("#from").value) + "». Проверьте название."; return; }
  trip.date = $("#date").value; trip.dateBack = $("#dateBack").value;
  msg.className = "msg"; msg.textContent = ""; skeleton(list);
  try {
    const q = new URLSearchParams({ origin: trip.from.code, destination: trip.to.code, date: trip.date });
    if (trip.dateBack) q.set("return_date", trip.dateBack);
    const r = await api("/api/flights?" + q);
    list.innerHTML = "";
    if (r.error === "no_token") {
      msg.className = "msg err";
      msg.innerHTML = `Поиск билетов не подключён: нужен бесплатный API-токен Aviasales.<br>
        1) Зарегистрируйтесь на <a href="https://www.travelpayouts.com" target="_blank" rel="noopener">travelpayouts.com</a> → Инструменты → API, скопируйте токен.<br>
        2) Создайте в папке проекта файл <b>.env</b> со строкой <b>TRAVELPAYOUTS_TOKEN=ваш_токен</b> и перезапустите сервер.`;
      return;
    }
    tickets = r.tickets;
    $("#ticketBar").hidden = !tickets.length;
    if (!tickets.length) { msg.textContent = trip.date ? "Билетов на эту дату не найдено. Очистите дату — покажем ближайшие рейсы." : "Рейсов по этому направлению не найдено."; return; }
    // без даты по умолчанию показываем ближайшие рейсы, с датой — самые дешёвые
    sortBy = trip.date ? "price" : "departure";
    $$("#ticketBar .chip").forEach(x => x.classList.toggle("active", x.dataset.sort === sortBy));
    renderTickets();
  } catch (err) { list.innerHTML = ""; msg.className = "msg err"; msg.textContent = "Ошибка: " + err.message; }
};
$$("#ticketBar .chip").forEach(c => c.onclick = () => {
  $$("#ticketBar .chip").forEach(x => x.classList.toggle("active", x === c));
  sortBy = c.dataset.sort; renderTickets();
});
$("#directOnly").onchange = renderTickets;

function renderTickets() {
  const list = $("#ticketList"), msg = $("#ticketMsg");
  let arr = tickets.filter(t => !$("#directOnly").checked || !t.transfers);
  const key = { price: t => t.price, duration: t => t.duration, departure: t => local(t.departure).getTime() }[sortBy];
  arr = [...arr].sort((a, b) => key(a) - key(b));
  const cheapest = Math.min(...tickets.map(t => t.price)), fastest = Math.min(...tickets.map(t => t.duration));
  msg.className = "msg"; msg.textContent = `${trip.date ? "Найдено" : "Ближайшие рейсы:"} ${arr.length} вариантов · цены в сомах по текущему курсу, точную стоимость покажет страница покупки`;
  list.innerHTML = "";
  arr.forEach(t => {
    const arrive = t.arrival || new Date(local(t.departure).getTime() + t.duration * 6e4).toISOString();
    const c = document.createElement("div"); c.className = "tk";
    c.innerHTML = `
      <div class="tk-buy">
        <div class="tk-price">${som(t.price)}</div>
        <button class="go pick">Выбрать</button>
        <a target="_blank" rel="noopener" href="${esc(t.link)}">Купить на Aviasales ↗</a>
      </div>
      <div class="tk-main">
        <div class="tk-head">
          <img class="tk-al" src="https://pics.avs.io/220/60/${esc(t.airline)}.png" alt="${esc(t.airline)}" onerror="this.outerHTML='<span class=tk-al-txt>${esc(t.airline)}</span>'">
          <div>${t.price === cheapest ? '<span class="badge">Самый дешёвый</span> ' : ""}${t.duration === fastest ? '<span class="badge b2">Самый быстрый</span>' : ""}</div>
        </div>
        <div class="leg">
          <div class="leg-t">${time(t.departure)}<small>${esc(t.from_airport)} · ${day(t.departure)}</small></div>
          <div class="leg-mid">в пути ${dur(t.duration)}<div class="leg-line">${t.transfers ? "<i></i>" : ""}</div>${t.transfers ? t.transfers + " пересадка" : "прямой"}</div>
          <div class="leg-t leg-r">${time(arrive)}<small>${day(arrive)} · ${esc(t.to_airport)}</small></div>
        </div>
        ${t.return ? `<small class="note">Обратно: ${day(t.return)}, ${time(t.return)}</small>` : ""}
      </div>`;
    c.querySelector(".pick").onclick = () => {
      trip.ticket = t;
      $("#hcity").value = trip.to.name; $("#ccity").value = trip.to.name;
      $("#hin").value = $("#cfrom").value = (t.departure || "").slice(0, 10);
      $("#hout").value = $("#cto").value = trip.dateBack || (t.return || "").slice(0, 10);
      renderTrip(); toast("✈️ Перелёт добавлен в поездку"); go("hotels"); searchHotels();
    };
    list.append(c);
  });
}

// ---------- 2. жильё ----------
let map, layer, hotels = [], hotelKind = "", markers = {};
$("#hotelForm").onsubmit = e => { e.preventDefault(); searchHotels(); };
$$("#hotelBar .chip").forEach(c => c.onclick = () => {
  $$("#hotelBar .chip").forEach(x => x.classList.toggle("active", x === c));
  hotelKind = c.dataset.kind; renderHotels();
});
const KIND = { hotel: "Отель", hostel: "Хостел", guest_house: "Гостевой дом", apartment: "Апартаменты" };
const PALETTE = ["#0c73fe", "#ff6d00", "#1aa356", "#8e44ef", "#e0393e", "#00a3b4", "#d48c00"];
const color = s => PALETTE[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % PALETTE.length];

async function searchHotels() {
  const city = $("#hcity").value.trim(), msg = $("#hotelMsg"), list = $("#hotelList");
  if (!city) return;
  $("#hotelEmpty").hidden = true; msg.className = "msg"; msg.textContent = "Ищем реальные отели…";
  skeleton(list, 5);
  try {
    const q = new URLSearchParams({ city });
    if (trip.to && trip.to.name === city && trip.to.lat) { q.set("lat", trip.to.lat); q.set("lon", trip.to.lon); }
    const r = await api("/api/hotels?" + q);
    hotels = r.hotels;
    if (!map) {
      map = L.map("map", { zoomControl: true });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap" }).addTo(map);
    }
    if (r.center) map.setView([r.center.lat, r.center.lon], 13);
    setTimeout(() => map.invalidateSize(), 50);
    $("#hotelBar").hidden = !hotels.length;
    renderHotels();
  } catch (err) { list.innerHTML = ""; msg.className = "msg err"; msg.textContent = "Сервер карт занят, повторите поиск через минуту. (" + err.message + ")"; }
}

function renderHotels() {
  const list = $("#hotelList"), msg = $("#hotelMsg"), city = $("#hcity").value.trim();
  const arr = hotels.filter(h => !hotelKind || h.kind === hotelKind);
  msg.className = "msg";
  msg.textContent = arr.length ? `${city}: ${arr.length} вариантов проживания · цены уточняйте на сайте отеля` : "В этой категории ничего не найдено.";
  list.innerHTML = ""; if (layer) layer.remove(); layer = L.layerGroup().addTo(map); markers = {};
  arr.forEach(h => {
    const n = parseInt(h.stars) || 0;
    const gm = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(h.name + " " + city)}`;
    const site = h.website ? (h.website.startsWith("http") ? h.website : "https://" + h.website) : "";
    const img = h.image && h.image.startsWith("http") ? `background-image:url('${esc(h.image)}')` : `background:linear-gradient(135deg,${color(h.name)},${color(h.name + "x")})`;
    const c = document.createElement("div"); c.className = "ht" + (trip.hotel && trip.hotel.id === h.id ? " sel" : "");
    c.innerHTML = `
      <div class="ht-img" style="${img}">${h.image ? "" : esc(h.name[0].toUpperCase())}</div>
      <div class="ht-body">
        <div>${n ? `<span class="stars">${"★".repeat(n)}</span>` : ""}<h3>${esc(h.name)}</h3></div>
        <div class="ht-meta">${KIND[h.kind] || "Жильё"}${h.address ? " · " + esc(h.address) : ""}${h.phone ? "<br>☎ " + esc(h.phone) : ""}</div>
        <div class="ht-foot">
          <button class="btn pick">Выбрать</button>
          <button class="btn ghost show">На карте</button>
          <a target="_blank" rel="noopener" href="${gm}">Отзывы ↗</a>
          ${site ? `<a target="_blank" rel="noopener" href="${esc(site)}">Сайт отеля ↗</a>` : ""}
        </div>
      </div>`;
    c.querySelector(".pick").onclick = () => { trip.hotel = h; renderTrip(); toast("🏨 Отель добавлен в поездку"); go("cars"); };
    c.querySelector(".show").onclick = () => { map.setView([h.lat, h.lon], 16); markers[h.id].openPopup(); };
    list.append(c);
    markers[h.id] = L.marker([h.lat, h.lon]).addTo(layer).bindPopup(`<b>${esc(h.name)}</b><br>${KIND[h.kind] || ""}`);
  });
}

// ---------- 3. авто ----------
const carPhoto = c => `<img src="${esc(c.photo)}" alt="${esc(c.brand)}" loading="lazy">`;
let cars = [], carClass = "", curCar;

$("#carSearch").onsubmit = e => { e.preventDefault(); loadCars(); };
async function loadCars() {
  const city = $("#ccity").value.trim() || (trip.to && trip.to.name) || $("#hcity").value.trim();
  if (city) $("#ccity").value = city;
  cars = await api("/api/cars?city=" + encodeURIComponent(city));
  const classes = [...new Set(cars.map(c => c.class))];
  $("#carClasses").innerHTML = ["", ...classes].map(c => `<button class="chip${c === carClass ? " active" : ""}" data-c="${esc(c)}">${c || "Все классы"}</button>`).join("");
  $$("#carClasses .chip").forEach(b => b.onclick = () => { carClass = b.dataset.c; loadCars(); });
  $("#carMsg").className = "msg";
  $("#carMsg").textContent = city ? `Доступно в городе ${city} · получение в аэропорту или у отеля` : "";
  $("#carList").innerHTML = "";
  cars.filter(c => !carClass || c.class === carClass).forEach(c => {
    const d = document.createElement("div"); d.className = "car";
    d.innerHTML = `<div class="car-img">${carPhoto(c)}<span class="credit">Фото: ${esc(c.photo_credit)}</span></div>
      <div class="car-b">
        <span class="car-cls">${esc(c.class)}</span><h3>${esc(c.brand)}</h3>
        <div class="specs"><span>👤 ${c.seats}</span><span>⚙️ ${esc(c.gearbox)}</span><span>⛽ ${esc(c.fuel)}</span></div>
        <div class="car-foot"><b>${som(c.price_day)}<small> / сутки</small></b><button class="btn">Выбрать</button></div>
      </div>`;
    d.querySelector("button").onclick = () => openCar(c);
    $("#carList").append(d);
  });
}
function openCar(c) {
  curCar = c;
  $("#carHead").innerHTML = `${carPhoto(c)}<div><span class="car-cls">${esc(c.class)}</span><h3>${esc(c.brand)}</h3><small class="note">${som(c.price_day)} / сутки</small></div>`;
  const f = $("#carForm");
  f.date_from.value = $("#cfrom").value || trip.date || "";
  f.date_to.value = $("#cto").value || trip.dateBack || "";
  if (me) { f.name.value ||= me.name; f.phone.value ||= me.phone || ""; }
  updTotal(); $("#carDlg").showModal();
}
function carDays() {
  const f = $("#carForm"), a = new Date(f.date_from.value), b = new Date(f.date_to.value);
  return Math.max(1, Math.round((b - a) / 864e5) || 1);
}
function updTotal() {
  const f = $("#carForm"), ok = f.date_from.value && f.date_to.value;
  $("#carDays").textContent = ok ? `${carDays()} сут. × ${som(curCar.price_day)}` : "Выберите даты";
  $("#carTotal").textContent = ok ? som(carDays() * curCar.price_day) : "";
}
$("#carForm").addEventListener("input", updTotal);
$("#carCancel").onclick = () => $("#carDlg").close();
$("#carForm").onsubmit = async e => {
  e.preventDefault();
  const f = $("#carForm");
  const body = { car_id: curCar.id, car: curCar.brand, hotel: trip.hotel && trip.hotel.name, city: $("#ccity").value,
                 total: carDays() * curCar.price_day };
  ["name", "phone", "date_from", "date_to"].forEach(k => body[k] = f[k].value);
  const r = await fetch("/api/cars/book", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) { toast("⚠️ " + j.error); return; }
  trip.car = { car_id: curCar.id, brand: curCar.brand, days: carDays(), total: carDays() * curCar.price_day,
               booking_id: j.id, date_from: body.date_from, date_to: body.date_to, name: body.name, phone: body.phone };
  $("#carDlg").close(); renderTrip();
  toast(`✅ Заявка №${j.id} принята — менеджер свяжется с вами`);
};

$("#hin").onchange = $("#hout").onchange = renderTrip;
renderTrip();

// ---------- авторизация и личный кабинет ----------
let me = null, afterAuth = null, authMode = "login";
const post = async (url, body) => {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "Ошибка");
  return j;
};
const initial = s => esc((s || "?").trim()[0].toUpperCase());

function renderAuthBtn() {
  $("#authBtn").innerHTML = me ? `<span class="av">${initial(me.name)}</span>${esc(me.name.split(" ")[0])}` : "Войти";
}
$("#authBtn").onclick = () => me ? go("account") : openAuth("login");

function setAuthMode(mode) {
  authMode = mode;
  $$(".auth-tabs button").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
  $("#authForm").classList.toggle("register", mode === "register");
  $("#authForm").name.required = mode === "register";
  $("#authForm").password.autocomplete = mode === "register" ? "new-password" : "current-password";
  $("#authSubmit").textContent = mode === "register" ? "Зарегистрироваться" : "Войти";
  $("#authErr").textContent = "";
}
function openAuth(mode, then) {
  afterAuth = then || null; setAuthMode(mode); $("#authDlg").showModal();
}
$$(".auth-tabs button").forEach(b => b.onclick = () => setAuthMode(b.dataset.mode));
$("#authClose").onclick = () => $("#authDlg").close();
$("#authForm").onsubmit = async e => {
  e.preventDefault();
  const f = $("#authForm");
  try {
    const r = await post("/api/auth/" + authMode, { name: f.name.value, email: f.email.value, password: f.password.value });
    me = r.user; renderAuthBtn(); $("#authDlg").close(); f.reset();
    toast(authMode === "register" ? `🎉 Добро пожаловать, ${me.name}!` : `👋 С возвращением, ${me.name}!`);
    if (afterAuth) afterAuth();
  } catch (err) { $("#authErr").textContent = err.message; }
};

$("#logoutBtn").onclick = async () => {
  await post("/api/auth/logout"); me = null; renderAuthBtn(); go("tickets"); toast("Вы вышли из аккаунта");
};

// сохранение текущей поездки в кабинет
async function saveTrip() {
  if (!trip.ticket && !trip.hotel && !trip.car) return toast("Сначала выберите билет, отель или автомобиль");
  if (!me) return openAuth("login", saveTrip);
  try {
    await post("/api/trips", tripPayload());
    toast("💾 Поездка сохранена в личном кабинете");
  } catch (err) { toast("⚠️ " + err.message); }
}
$("#saveTrip").onclick = saveTrip;

const addDays = (d, n) => d ? new Date(new Date(d).getTime() + n * 864e5).toISOString().slice(0, 10) : "";

// данные поездки для сохранения и для заказа
function tripPayload() {
  const t = trip.ticket, h = trip.hotel;
  return {
    from: trip.from && trip.from.name, to: (trip.to && trip.to.name) || $("#hcity").value,
    ticket: t && { from: t.from, to: t.to, from_airport: t.from_airport, to_airport: t.to_airport, airline: t.airline,
                   flight: t.flight, departure: t.departure, arrival: t.arrival, price: t.price, link: t.link, transfers: t.transfers },
    hotel: h && { name: h.name, kind: h.kind, address: h.address, city: $("#hcity").value, guests: $("#guests").value,
                  checkin: $("#hin").value, checkout: $("#hout").value || addDays($("#hin").value, nights()), nights: nights() },
    car: trip.car,
    total: (t ? t.price : 0) + (trip.car ? trip.car.total : 0),
  };
}

// оформление заказа → номер заказа и PDF-отчёт
$("#checkout").onclick = async () => {
  const btn = $("#checkout"); btn.disabled = true; btn.textContent = "Оформляем…";
  try {
    const o = await post("/api/orders", { ...tripPayload(), customer: { name: trip.car.name, phone: trip.car.phone } });
    $("#orderNum").textContent = o.number;
    $("#orderTotal").textContent = som(o.total);
    $("#orderPdf").href = `/api/orders/${o.id}/pdf`;
    $("#orderAccount").hidden = !me;
    $("#orderDlg").showModal();
  } catch (err) { toast("⚠️ " + err.message); }
  btn.textContent = "Оформить заказ"; renderTrip();
};
$("#orderClose").onclick = () => $("#orderDlg").close();
$("#orderAccount").onclick = () => { $("#orderDlg").close(); go("account"); showAccTab("orders"); };

$$("#accNav [data-tab]").forEach(b => b.onclick = () => showAccTab(b.dataset.tab));
function showAccTab(tab) {
  $$("#accNav [data-tab]").forEach(x => x.classList.toggle("active", x.dataset.tab === tab));
  $$(".acc-tab").forEach(x => x.classList.toggle("active", x.dataset.tab === tab));
}

async function loadAccount() {
  try { renderAccount(await api("/api/account")); }
  catch { me = null; renderAuthBtn(); openAuth("login", () => go("account")); }
}
function renderAccount(d) {
  me = d.user; renderAuthBtn();
  $("#accAvatar").textContent = (me.name || "?")[0].toUpperCase();
  $("#accName").textContent = me.name; $("#accEmail").textContent = me.email;
  $("#accSince").textContent = "С нами с " + new Date(me.created.replace(" ", "T")).toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  $("#cntTrips").textContent = d.trips.length || "";
  $("#cntBookings").textContent = d.bookings.filter(b => b.status !== "cancelled").length || "";
  $("#cntOrders").textContent = d.orders.length || "";
  $("#ordersList").innerHTML = d.orders.length ? "" : emptyBox("🧾", "Заказов пока нет", "Выберите билет, отель и автомобиль, затем нажмите «Оформить заказ».");
  d.orders.forEach(o => {
    const el = document.createElement("div"); el.className = "trip-card";
    el.innerHTML = `
      <header><h3>Заказ ${esc(o.number)}</h3><small class="note">${esc(o.created.replace("T", " ").slice(0, 16))}</small></header>
      <div class="trip-parts">
        <div class="part"><b>✈️ ${esc(o.from)} → ${esc(o.to)}</b>${esc(o.ticket.airline)} ${esc(o.ticket.flight || "")} · ${day(o.ticket.departure)}<br>${som(o.ticket.price)}</div>
        <div class="part"><b>🏨 ${esc(o.hotel.name)}</b>${o.hotel.nights} ноч. · оплата в отеле</div>
        <div class="part"><b>🚗 ${esc(o.car.brand)}</b>${o.car.days} сут. · ${som(o.car.total)}</div>
      </div>
      <footer><b>${som(o.total)}</b><a class="btn" href="/api/orders/${esc(o.id)}/pdf" target="_blank" rel="noopener">📄 Отчёт PDF</a></footer>`;
    $("#ordersList").append(el);
  });
  const pf = $("#profileForm"); pf.name.value = me.name; pf.phone.value = me.phone || ""; pf.email.value = me.email;

  $("#tripsList").innerHTML = d.trips.length ? "" : emptyBox("🧳", "Поездок пока нет", "Соберите поездку и нажмите «Сохранить поездку» в правой панели.");
  d.trips.forEach(t => {
    const tk = t.ticket, h = t.hotel, c = t.car;
    const el = document.createElement("div"); el.className = "trip-card";
    el.innerHTML = `
      <header><h3>${esc(t.from || "—")} → ${esc(t.to || "—")}</h3><small class="note">сохранено ${esc(t.created)}</small></header>
      <div class="trip-parts">
        <div class="part${tk ? "" : " off"}"><b>✈️ Перелёт</b>${tk ? `${esc(tk.airline)} ${esc(tk.flight || "")} · ${day(tk.departure)}, ${time(tk.departure)}<br>${esc(tk.from_airport)} → ${esc(tk.to_airport)} · ${som(tk.price)}<br><a href="${esc(tk.link)}" target="_blank" rel="noopener">Купить ↗</a>` : "не выбран"}</div>
        <div class="part${h ? "" : " off"}"><b>🏨 Отель</b>${h ? `${esc(h.name)}<br>${h.checkin ? esc(h.checkin) + (h.checkout ? " — " + esc(h.checkout) : "") + "<br>" : ""}${h.nights} ноч.` : "не выбран"}</div>
        <div class="part${c ? "" : " off"}"><b>🚗 Автомобиль</b>${c ? `${esc(c.brand)}<br>${c.days} сут. · ${som(c.total)}` : "не выбран"}</div>
      </div>
      <footer><b>${som(t.total || 0)}</b><button class="link-danger">Удалить</button></footer>`;
    el.querySelector(".link-danger").onclick = async () => {
      if (!confirm("Удалить эту поездку?")) return;
      renderAccount(await post("/api/trips/delete", { id: t.id })); toast("Поездка удалена");
    };
    $("#tripsList").append(el);
  });

  $("#bookingsList").innerHTML = d.bookings.length ? "" : emptyBox("🚗", "Броней пока нет", "Забронируйте автомобиль на шаге «Аренда авто», будучи в аккаунте.");
  d.bookings.forEach(b => {
    const off = b.status === "cancelled";
    const el = document.createElement("div"); el.className = "trip-card";
    el.innerHTML = `
      <header><h3>${esc(b.car)}</h3><span class="status${off ? " cancelled" : ""}">${off ? "Отменена" : "Активна"}</span></header>
      <div class="trip-parts">
        <div class="part"><b>Даты</b>${esc(b.date_from)} — ${esc(b.date_to)}</div>
        <div class="part"><b>Город</b>${esc(b.city || "—")}${b.hotel ? "<br>у отеля " + esc(b.hotel) : ""}</div>
        <div class="part"><b>Заявка №${esc(b.id)}</b>${esc(b.name)}<br>${esc(b.phone)}</div>
      </div>
      <footer><b>${b.total ? som(b.total) : ""}</b>${off ? "" : '<button class="link-danger">Отменить бронь</button>'}</footer>`;
    if (!off) el.querySelector(".link-danger").onclick = async () => {
      if (!confirm("Отменить бронь автомобиля?")) return;
      renderAccount(await post("/api/bookings/cancel", { id: b.id })); toast("Бронь отменена");
    };
    $("#bookingsList").append(el);
  });
}
const emptyBox = (ico, title, text) => `<div class="empty"><div class="empty-ico">${ico}</div><h3>${title}</h3><p>${text}</p></div>`;

$("#profileForm").onsubmit = async e => {
  e.preventDefault();
  const f = e.target;
  try { renderAccount(await post("/api/account/profile", { name: f.name.value, phone: f.phone.value })); toast("✅ Профиль сохранён"); }
  catch (err) { toast("⚠️ " + err.message); }
};
$("#passwordForm").onsubmit = async e => {
  e.preventDefault();
  const f = e.target;
  try { await post("/api/account/password", { old: f.old.value, new: f.new.value }); f.reset(); toast("🔒 Пароль изменён"); }
  catch (err) { toast("⚠️ " + err.message); }
};

api("/api/me").then(r => { me = r.user; renderAuthBtn(); }).catch(() => {});
