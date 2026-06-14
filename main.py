import os, re, time, logging, requests, json, smtplib, threading
from datetime import datetime
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── CONFIGURATION (variables d'environnement Railway) ─────────
AUTO1_EMAIL      = os.environ.get("AUTO1_EMAIL", "")
AUTO1_PASSWORD   = os.environ.get("AUTO1_PASSWORD", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
EMAIL_EXPEDITEUR   = os.environ.get("EMAIL_EXPEDITEUR", "")
EMAIL_MOT_PASSE    = os.environ.get("EMAIL_MOT_PASSE", "")
EMAIL_DESTINATAIRE = os.environ.get("EMAIL_DESTINATAIRE", "")
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
NTFY_TOPIC       = os.environ.get("NTFY_TOPIC", "auto1-alertes")

SMTP_SERVEUR   = "smtp.office365.com"
SMTP_PORT      = 587
INTERVALLE_SEC = 60

# ── RECHERCHES ────────────────────────────────────────────────
RECHERCHES = [
    {"nom": "🔴 Clio 2 | 500-1000€ | 50-200k km", "make": "Renault", "model": "Clio", "prix_min": 500, "prix_max": 1000, "km_min": 50000, "km_max": 200000, "fuel": ["diesel","petrol"]},
    {"nom": "🟠 Clio 3 | 500-1500€ | 50-220k km", "make": "Renault", "model": "Clio", "prix_min": 500, "prix_max": 1500, "km_min": 50000, "km_max": 220000, "fuel": ["diesel","petrol"]},
    {"nom": "🟡 Clio toutes | 500-3500€ | 50-220k km", "make": "Renault", "model": "Clio", "prix_min": 500, "prix_max": 3500, "km_min": 50000, "km_max": 220000, "fuel": ["diesel","petrol"]},
    {"nom": "🚐 Utilitaire | 500-3000€ | max 230k km", "make": None, "model": None, "prix_min": 500, "prix_max": 3000, "km_min": 0, "km_max": 230000, "fuel": ["diesel"], "type": "van"},
    {"nom": "🔵 3008 2011-2015 | max 2600€", "make": "Peugeot", "model": "3008", "prix_min": 0, "prix_max": 2600, "km_min": 0, "km_max": 220000, "fuel": ["diesel"], "year_min": 2011, "year_max": 2015},
    {"nom": "🟣 3008 2016-2020 | max 7000€", "make": "Peugeot", "model": "3008", "prix_min": 0, "prix_max": 7000, "km_min": 0, "km_max": 220000, "fuel": ["diesel"], "year_min": 2016, "year_max": 2020},
    {"nom": "⚫ 207 | max 1000€", "make": "Peugeot", "model": "207", "prix_min": 0, "prix_max": 1000, "km_min": 0, "km_max": 220000, "fuel": ["diesel"]},
    {"nom": "🟤 C3 Phase 2 | max 1700€", "make": "Citroen", "model": "C3", "prix_min": 0, "prix_max": 1700, "km_min": 0, "km_max": 220000, "fuel": ["diesel"]},
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

# ── GEMINI AI ─────────────────────────────────────────────────
def analyser_avec_gemini(voiture):
    if not GEMINI_API_KEY:
        return None, None
    try:
        prompt = f"""Tu es un expert en achat de voitures d'occasion. Analyse cette annonce Auto1 :

Véhicule : {voiture['titre']} ({voiture['annee']})
Prix : {voiture['prix']} €
Kilométrage : {voiture['km']} km
Carburant : {voiture['carbu']}
Profil recherche : {voiture['profil']}

Réponds en JSON uniquement, sans markdown, sans backticks :
{{
  "score": <note de 1 à 10 (10 = excellente affaire)>,
  "verdict": "<Bonne affaire / Affaire correcte / Prix élevé>",
  "resume": "<1 phrase résumant l'annonce et son intérêt>",
  "points_positifs": "<ce qui est bien>",
  "points_negatifs": "<points de vigilance>"
}}"""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        score = data.get("score", "?")
        resume = (
            f"🤖 Analyse Gemini\n"
            f"⭐ Score : {score}/10 — {data.get('verdict','')}\n"
            f"📝 {data.get('resume','')}\n"
            f"✅ {data.get('points_positifs','')}\n"
            f"⚠️ {data.get('points_negatifs','')}"
        )
        return score, resume
    except Exception as e:
        log.error("Gemini erreur : %s", e)
        return None, None

# ── NTFY.SH ───────────────────────────────────────────────────
def envoyer_ntfy(voiture, score=None, verdict=None):
    try:
        if score and isinstance(score, (int, float)):
            if score >= 8:
                priority = "urgent"
                emoji = "🔥"
            elif score >= 6:
                priority = "high"
                emoji = "✅"
            else:
                priority = "default"
                emoji = "🚗"
        else:
            priority = "default"
            emoji = "🚗"

        titre_notif = f"{emoji} {voiture['titre']} — {voiture['prix']}€"
        corps_notif = (
            f"{voiture['profil']}\n"
            f"📏 {voiture['km']} km | ⛽ {voiture['carbu']} | 📅 {voiture['annee']}\n"
        )
        if verdict:
            corps_notif += f"🤖 {verdict}\n"
        corps_notif += f"👉 {voiture['url']}"

        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=corps_notif.encode("utf-8"),
            headers={
                "Title": titre_notif,
                "Priority": priority,
                "Tags": "car,auto1",
                "Click": voiture["url"],
            },
            timeout=10
        )
        log.info(f"📲 Ntfy envoyé : {voiture['titre']}")
    except Exception as e:
        log.error("Ntfy erreur : %s", e)

# ── TELEGRAM ──────────────────────────────────────────────────
def escape_md(t):
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(t))

def envoyer_telegram_message(chat_id, texte, parse_mode="MarkdownV2"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": texte, "parse_mode": parse_mode}, timeout=10)
    except Exception as e:
        log.error("Telegram erreur : %s", e)

def envoyer_telegram(voiture, analyse_texte=None):
    texte = (
        f"🚗 *{escape_md(voiture['profil'])}*\n\n"
        f"🏷️ *{escape_md(voiture['titre'])}* \\({escape_md(str(voiture['annee']))}\\)\n"
        f"💰 *{escape_md(str(voiture['prix']))} €*\n"
        f"📏 {escape_md(str(voiture['km']))} km  |  ⛽ {escape_md(voiture['carbu'])}\n"
        f"🕒 {escape_md(voiture['date'])}\n"
    )
    if analyse_texte:
        texte += f"\n{escape_md(analyse_texte)}\n"
    texte += f"\n[👉 Voir l'annonce]({voiture['url']})"
    envoyer_telegram_message(TELEGRAM_CHAT_ID, texte)

# ── EMAIL ─────────────────────────────────────────────────────
def envoyer_email(nouveaux):
    if not EMAIL_EXPEDITEUR or not EMAIL_MOT_PASSE:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚗 Auto1 — {len(nouveaux)} nouvelle(s) affaire(s) !"
        msg["From"] = EMAIL_EXPEDITEUR
        msg["To"]   = EMAIL_DESTINATAIRE
        profils = {}
        for v in nouveaux:
            profils.setdefault(v["profil"], []).append(v)
        sections = ""
        for profil, vlist in profils.items():
            lignes = "".join(
                f"<tr><td><a href='{v['url']}'>{v['titre']}</a></td>"
                f"<td style='color:red'><b>{v['prix']}€</b></td>"
                f"<td>{v['km']}km</td><td>{v['carbu']}</td>"
                f"<td>{v.get('gemini_verdict','—')}</td>"
                f"<td>{v.get('gemini_score','—')}/10</td></tr>"
                for v in vlist
            )
            sections += (
                f"<h3>{profil}</h3>"
                f"<table border='1'><tr><th>Véhicule</th><th>Prix</th>"
                f"<th>Km</th><th>Carbu</th><th>Verdict Gemini</th><th>Score</th></tr>"
                f"{lignes}</table>"
            )
        msg.attach(MIMEText(f"<html><body>{sections}</body></html>", "html"))
        with smtplib.SMTP(SMTP_SERVEUR, SMTP_PORT) as srv:
            srv.starttls()
            srv.login(EMAIL_EXPEDITEUR, EMAIL_MOT_PASSE)
            srv.sendmail(EMAIL_EXPEDITEUR, EMAIL_DESTINATAIRE, msg.as_string())
        log.info("📧 Email envoyé")
    except Exception as e:
        log.error("Email : %s", e)

# ── CONNEXION AUTO1 ───────────────────────────────────────────
def connexion():
    log.info("🔑 Connexion à auto1.com...")
    try:
        r = session.get("https://www.auto1.com/fr/home/login", timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        csrf_input = soup.find("input", {"name": re.compile(r"csrf|_token", re.I)})
        csrf = csrf_input["value"] if csrf_input else ""
        r2 = session.post(
            "https://www.auto1.com/fr/home/login",
            data={"email": AUTO1_EMAIL, "password": AUTO1_PASSWORD, "_token": csrf},
            timeout=15, allow_redirects=True
        )
        if "logout" in r2.text.lower():
            log.info("✅ Connecté à Auto1")
            return True
        else:
            log.warning("⚠️ Connexion incertaine à Auto1")
            return False
    except Exception as e:
        log.error("Erreur connexion : %s", e)
        return False

# ── SCRAPING ──────────────────────────────────────────────────
def construire_url(c):
    from urllib.parse import urlencode
    p = [("sort","price_asc"), ("per_page", 48)]
    if c.get("make"):     p.append(("makes[]", c["make"]))
    if c.get("model"):    p.append(("models[]", c["model"]))
    if c.get("prix_min"): p.append(("price[min]", c["prix_min"]))
    if c.get("prix_max"): p.append(("price[max]", c["prix_max"]))
    if c.get("km_min"):   p.append(("mileage[min]", c["km_min"]))
    if c.get("km_max"):   p.append(("mileage[max]", c["km_max"]))
    if c.get("year_min"): p.append(("year[min]", c["year_min"]))
    if c.get("year_max"): p.append(("year[max]", c["year_max"]))
    for f in c.get("fuel", []): p.append(("fuel[]", f))
    if c.get("type"):     p.append(("categories[]", c["type"]))
    return f"https://www.auto1.com/fr/home/buy?{urlencode(p)}"

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

def scraper(url, c):
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        scripts = soup.find_all("script", {"id": "__NEXT_DATA__"})
        if scripts:
            data = json.loads(scripts[0].string)
            cars = chercher_liste(data)
            return [formater_json(v, c) for v in cars]
        return []
    except Exception as e:
        log.error("[%s] Erreur scraping : %s", c["nom"], e)
        return []

# ── BOT TELEGRAM (réception commandes) ───────────────────────
last_update_id = None

def ecouter_telegram():
    global last_update_id
    log.info("👂 Écoute des commandes Telegram...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": last_update_id}
            r = requests.get(url, params=params, timeout=35)
            updates = r.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                texte = msg.get("text", "").strip().lower()
                if texte in ["/start", "start"]:
                    reponse = (
                        "🚀 *Auto1 Monitor est actif\\!*\n\n"
                        f"✅ *{len(RECHERCHES)} profils* surveillés\n"
                        f"🤖 Analyse Gemini AI activée\n"
                        f"📲 Notifications ntfy\\.sh activées\n"
                        f"🔄 Intervalle : *{INTERVALLE_SEC}s*\n\n"
                        "Tu recevras une alerte dès qu'une nouvelle annonce correspond à tes critères\\."
                    )
                    envoyer_telegram_message(chat_id, reponse)
                elif texte == "/status":
                    reponse = (
                        f"📊 *Statut du bot*\n\n"
                        f"🟢 En ligne\n"
                        f"👁️ Annonces vues : *{escape_md(str(len(deja_vus)))}*\n"
                        f"🤖 Gemini : {'✅ actif' if GEMINI_API_KEY else '❌ non configuré'}\n"
                        f"📲 Ntfy : ✅ topic `{escape_md(NTFY_TOPIC)}`\n"
                        f"🕒 {escape_md(datetime.now().strftime('%d/%m/%Y %H:%M'))}"
                    )
                    envoyer_telegram_message(chat_id, reponse)
                elif texte == "/help":
                    reponse = (
                        "ℹ️ *Commandes disponibles*\n\n"
                        "/start \\- Démarrer / infos bot\n"
                        "/status \\- Voir le statut\n"
                        "/help \\- Aide"
                    )
                    envoyer_telegram_message(chat_id, reponse)
        except Exception as e:
            log.error("Erreur écoute Telegram : %s", e)
        time.sleep(1)

# ── BOUCLE PRINCIPALE ─────────────────────────────────────────
def main():
    log.info("🚀 Auto1 Monitor démarré")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("❌ TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquant !")
        return
    if not AUTO1_EMAIL or not AUTO1_PASSWORD:
        log.error("❌ AUTO1_EMAIL ou AUTO1_PASSWORD manquant !")
        return

    t = threading.Thread(target=ecouter_telegram, daemon=True)
    t.start()

    connexion()

    envoyer_telegram_message(
        TELEGRAM_CHAT_ID,
        f"🚀 *Auto1 Monitor actif\\!*\n"
        f"✅ {len(RECHERCHES)} profils surveillés\n"
        f"🤖 Gemini AI : {'activé' if GEMINI_API_KEY else 'non configuré'}\n"
        f"📲 Ntfy\\.sh : topic `{escape_md(NTFY_TOPIC)}`\n"
        f"🔄 Cycle toutes les {INTERVALLE_SEC}s"
    )

    cycle = 0
    while True:
        cycle += 1
        log.info(f"── Cycle #{cycle} ── {datetime.now().strftime('%H:%M:%S')} ──")
        nouveaux = []

        for c in RECHERCHES:
            url = construire_url(c)
            vlist = scraper(url, c)
            for v in vlist:
                cle = f"{c['nom']}|{v['id']}"
                if v["id"] and cle not in deja_vus:
                    deja_vus.add(cle)

                    # Analyse Gemini
                    score, analyse_texte = analyser_avec_gemini(v)
                    v["gemini_score"]   = score
                    v["gemini_verdict"] = analyse_texte.split("—")[1].strip() if analyse_texte and "—" in analyse_texte else "—"

                    nouveaux.append(v)
                    log.info(f"  🆕 {v['titre']} — {v['prix']}€ | Score Gemini: {score}/10")

                    # Envoi immédiat
                    envoyer_telegram(v, analyse_texte)
                    envoyer_ntfy(v, score, v["gemini_verdict"])
                    time.sleep(0.5)

            time.sleep(3)

        if nouveaux:
            log.info(f"🔔 {len(nouveaux)} nouveau(x) véhicule(s) ce cycle")
            envoyer_email(nouveaux)

        log.info(f"😴 Prochain cycle dans {INTERVALLE_SEC}s...")
        time.sleep(INTERVALLE_SEC)

if __name__ == "__main__":
    main()
