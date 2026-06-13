# Cellule 1 — Installation
# !pip install requests beautifulsoup4 python-dotenv twilio lxml

# ─── Cellule 2 — Script complet ───────────────────────────────
import os, re, time, logging, smtplib, requests
from datetime import datetime
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── VOS IDENTIFIANTS ─────────────────────────────────────────
AUTO1_EMAIL    = "d.m30@hotmail.fr"
AUTO1_PASSWORD = "Khamma30@"
INTERVALLE_SEC = 300  # 5 minutes

EMAIL_EXPEDITEUR   = "d.m30@hotmail.fr"
EMAIL_MOT_PASSE    = "Khamma30@"
EMAIL_DESTINATAIRE = "d.m30@hotmail.fr"
SMTP_SERVEUR       = "smtp.office365.com"
SMTP_PORT          = 587

TELEGRAM_TOKEN   = "8627193282:AAGZjgZsksfnH5vWeuJyjOb3Rn3f5miqFSY"
TELEGRAM_CHAT_ID = "838815078"

# ── RECHERCHES ────────────────────────────────────────────────
RECHERCHES = [
    {"nom": "🔴 Clio 2 | 500-1000€ | 50-200k km", "make": "Renault", "model": "Clio", "prix_min": 500, "prix_max": 1000, "km_min": 50000, "km_max": 200000, "fuel": ["diesel","petrol"], "year_min": None, "year_max": None},
    {"nom": "🟠 Clio 3 | 500-1500€ | 50-220k km", "make": "Renault", "model": "Clio", "prix_min": 500, "prix_max": 1500, "km_min": 50000, "km_max": 220000, "fuel": ["diesel","petrol"], "year_min": None, "year_max": None},
    {"nom": "🟡 Clio toutes | 500-3500€ | 50-220k km", "make": "Renault", "model": "Clio", "prix_min": 500, "prix_max": 3500, "km_min": 50000, "km_max": 220000, "fuel": ["diesel","petrol"], "year_min": None, "year_max": None},
    {"nom": "🚐 Utilitaire | 500-3000€ | max 230k km", "make": None, "model": None, "prix_min": 500, "prix_max": 3000, "km_min": 0, "km_max": 230000, "fuel": ["diesel"], "year_min": None, "year_max": None, "type": "van"},
    {"nom": "🔵 3008 2011-2015 | max 2600€", "make": "Peugeot", "model": "3008", "prix_min": 0, "prix_max": 2600, "km_min": 0, "km_max": 220000, "fuel": ["diesel"], "year_min": 2011, "year_max": 2015},
    {"nom": "🟣 3008 2016-2020 | max 7000€", "make": "Peugeot", "model": "3008", "prix_min": 0, "prix_max": 7000, "km_min": 0, "km_max": 220000, "fuel": ["diesel"], "year_min": 2016, "year_max": 2020},
    {"nom": "⚫ 207 | max 1000€", "make": "Peugeot", "model": "207", "prix_min": 0, "prix_max": 1000, "km_min": 0, "km_max": 220000, "fuel": ["diesel"], "year_min": None, "year_max": None},
    {"nom": "🟤 C3 Phase 2 | max 1700€", "make": "Citroen", "model": "C3", "prix_min": 0, "prix_max": 1700, "km_min": 0, "km_max": 220000, "fuel": ["diesel"], "year_min": None, "year_max": None},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

session  = requests.Session()
session.headers.update(HEADERS)
deja_vus = set()

def connexion():
    log.info("🔑 Connexion à auto1.com...")
    try:
        r = session.get("https://www.auto1.com/fr/home/login", timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        csrf_input = soup.find("input", {"name": re.compile(r"csrf|_token", re.I)})
        csrf = csrf_input["value"] if csrf_input else ""
        r2 = session.post("https://www.auto1.com/fr/home/login",
                          data={"email": AUTO1_EMAIL, "password": AUTO1_PASSWORD, "_token": csrf},
                          timeout=15, allow_redirects=True)
        log.info("✅ Connecté" if "logout" in r2.text.lower() else "⚠️ Connexion incertaine")
    except Exception as e:
        log.error("Erreur connexion : %s", e)

def construire_url(c):
    from urllib.parse import urlencode
    p = [("sort","price_asc"), ("per_page", 48)]
    if c.get("make"):   p.append(("makes[]", c["make"]))
    if c.get("model"):  p.append(("models[]", c["model"]))
    if c.get("prix_min"): p.append(("price[min]", c["prix_min"]))
    if c.get("prix_max"): p.append(("price[max]", c["prix_max"]))
    if c.get("km_min"):   p.append(("mileage[min]", c["km_min"]))
    if c.get("km_max"):   p.append(("mileage[max]", c["km_max"]))
    if c.get("year_min"): p.append(("year[min]", c["year_min"]))
    if c.get("year_max"): p.append(("year[max]", c["year_max"]))
    for f in c.get("fuel", []): p.append(("fuel[]", f))
    if c.get("type"):     p.append(("categories[]", c["type"]))
    return f"https://www.auto1.com/fr/home/buy?{urlencode(p)}"

def scraper(url, c):
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        import json
        scripts = soup.find_all("script", {"id": "__NEXT_DATA__"})
        if scripts:
            data = json.loads(scripts[0].string)
            cars = chercher_liste(data)
            return [formater_json(v, c) for v in cars]
        return []
    except Exception as e:
        log.error("[%s] Erreur : %s", c["nom"], e)
        return []

def chercher_liste(obj, d=0):
    if d > 6: return []
    if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
        if any(k in obj[0] for k in ["price","make","model","id"]): return obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = chercher_liste(v, d+1)
            if r: return r
    return []

def formater_json(v, c):
    vid = str(v.get("id") or v.get("uuid") or "")
    return {
        "id":     vid,
        "profil": c["nom"],
        "titre":  f"{v.get('make','') or v.get('brand','')} {v.get('model','')} {v.get('year','') or v.get('firstRegistrationYear','')}".strip(),
        "prix":   v.get("price") or v.get("grossPrice") or "?",
        "km":     v.get("mileage") or v.get("kilometers") or "?",
        "carbu":  v.get("fuelType") or v.get("fuel") or "?",
        "annee":  v.get("year") or v.get("firstRegistrationYear") or "?",
        "url":    v.get("url") or f"https://www.auto1.com/car/{vid}",
        "date":   datetime.now().strftime("%d/%m/%Y %H:%M"),
    }

def envoyer_telegram(nouveaux):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for v in nouveaux:
        texte = (f"🚗 *{v['profil']}*\n\n"
                 f"🏷️ *{v['titre']}* \\({v['annee']}\\)\n"
                 f"💰 *{v['prix']} €*\n"
                 f"📏 {v['km']} km  |  ⛽ {v['carbu']}\n"
                 f"🕒 {v['date']}\n\n"
                 f"[👉 Voir l'annonce]({v['url']})")
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": texte, "parse_mode": "MarkdownV2"}, timeout=10)
            time.sleep(0.4)
        except Exception as e:
            log.error("Telegram : %s", e)

def envoyer_email(nouveaux):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚗 Auto1 — {len(nouveaux)} nouvelle(s) affaire(s) !"
        msg["From"] = EMAIL_EXPEDITEUR
        msg["To"]   = EMAIL_DESTINATAIRE
        profils = {}
        for v in nouveaux: profils.setdefault(v["profil"], []).append(v)
        sections = ""
        for profil, vlist in profils.items():
            lignes = "".join(f"<tr><td><a href='{v['url']}'>{v['titre']}</a></td><td style='color:red'><b>{v['prix']}€</b></td><td>{v['km']}km</td><td>{v['carbu']}</td></tr>" for v in vlist)
            sections += f"<h3>{profil}</h3><table border='1'><tr><th>Véhicule</th><th>Prix</th><th>Km</th><th>Carbu</th></tr>{lignes}</table>"
        msg.attach(MIMEText(f"<html><body>{sections}</body></html>", "html"))
        with smtplib.SMTP(SMTP_SERVEUR, SMTP_PORT) as srv:
            srv.starttls()
            srv.login(EMAIL_EXPEDITEUR, EMAIL_MOT_PASSE)
            srv.sendmail(EMAIL_EXPEDITEUR, EMAIL_DESTINATAIRE, msg.as_string())
        log.info("📧 Email envoyé")
    except Exception as e:
        log.error("Email : %s", e)

# ── BOUCLE PRINCIPALE ─────────────────────────────────────────
connexion()
cycle = 0
print("🚀 Surveillance Auto1.com démarrée — 8 profils actifs")
print("   Telegram configuré ✅")
print("   Email Hotmail configuré ✅")
print(f"   Intervalle : {INTERVALLE_SEC}s\n")

while True:
    cycle += 1
    print(f"\n── Cycle #{cycle} ── {datetime.now().strftime('%H:%M:%S')} ──")
    nouveaux = []
    for c in RECHERCHES:
        url = construire_url(c)
        vlist = scraper(url, c)
        for v in vlist:
            cle = f"{c['nom']}|{v['id']}"
            if v["id"] and cle not in deja_vus:
                deja_vus.add(cle)
                nouveaux.append(v)
                print(f"  🆕 {v['titre']} — {v['prix']}€ ({v['km']}km)")
        if not [v for v in vlist if f"{c['nom']}|{v['id']}" not in deja_vus]:
            print(f"  ✔️  {c['nom']} — rien de nouveau")
        time.sleep(3)

    if nouveaux:
        print(f"🔔 {len(nouveaux)} nouveau(x) → alertes envoyées !")
        envoyer_telegram(nouveaux)
        envoyer_email(nouveaux)

    print(f"😴 Prochain cycle dans {INTERVALLE_SEC}s...")
    time.sleep(INTERVALLE_SEC)
