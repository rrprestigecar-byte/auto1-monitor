import os, re, time, logging, requests, json, smtplib, threading
from datetime import datetime, date
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── CONFIGURATION ─────────────────────────────────────────────
AUTO1_EMAIL      = os.environ.get("AUTO1_EMAIL", "")
AUTO1_PASSWORD   = os.environ.get("AUTO1_PASSWORD", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
EMAIL_EXPEDITEUR   = os.environ.get("EMAIL_EXPEDITEUR", "")
EMAIL_MOT_PASSE    = os.environ.get("EMAIL_MOT_PASSE", "")
EMAIL_DESTINATAIRE = os.environ.get("EMAIL_DESTINATAIRE", "")
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
NTFY_TOPIC       = os.environ.get("NTFY_TOPIC", "auto1-alertes")
SCORE_MIN        = int(os.environ.get("SCORE_MIN", "6"))
HEURE_RESUME     = 20  # Heure du résumé quotidien

SMTP_SERVEUR   = "smtp.office365.com"
SMTP_PORT      = 587
INTERVALLE_SEC = 30

# ── ÉTAT GLOBAL ───────────────────────────────────────────────
surveillance_active = True
deja_vus = set()
meilleures_annonces = []
stats_jour = {"total": 0, "alertes": 0, "date": str(date.today())}
cycle_count = 0
derniere_connexion = None
resume_envoye_aujourdhui = False

# ── RECHERCHES ────────────────────────────────────────────────
RECHERCHES = [
    # ── Renault ──
    {"nom": "🔴 Clio 2 | 500-1000€ | max 220k km",   "make": "Renault", "model": "Clio",   "prix_min": 500,  "prix_max": 1000, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"], "year_min": 2001, "year_max": 2006},
    {"nom": "🟠 Clio 3 | 500-1500€ | max 220k km",   "make": "Renault", "model": "Clio",   "prix_min": 500,  "prix_max": 1500, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"], "year_min": 2005, "year_max": 2012},
    {"nom": "🟡 Clio 4 | 500-4000€ | max 220k km",   "make": "Renault", "model": "Clio",   "prix_min": 500,  "prix_max": 4000, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"], "year_min": 2012, "year_max": 2019},
    {"nom": "🟢 Kangoo | 500-3000€ | max 230k km",   "make": "Renault", "model": "Kangoo", "prix_min": 500,  "prix_max": 3000, "km_min": 0, "km_max": 230000, "fuel": ["diesel","petrol"]},
    # ── Peugeot ──
    {"nom": "🔵 3008 2011-2015 | max 2600€",          "make": "Peugeot", "model": "3008",   "prix_min": 0,    "prix_max": 2600, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"], "year_min": 2011, "year_max": 2015},
    {"nom": "🟣 3008 2016-2020 | max 7000€",          "make": "Peugeot", "model": "3008",   "prix_min": 0,    "prix_max": 7000, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"], "year_min": 2016, "year_max": 2020},
    {"nom": "⚫ 207 | max 1000€",                     "make": "Peugeot", "model": "207",    "prix_min": 0,    "prix_max": 1000, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"]},
    {"nom": "🩶 308 | 500-4000€ | max 220k km",       "make": "Peugeot", "model": "308",    "prix_min": 500,  "prix_max": 4000, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"]},
    # ── Citroën ──
    {"nom": "🟤 C3 Phase 2 | max 1700€",              "make": "Citroen", "model": "C3",     "prix_min": 0,    "prix_max": 1700, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"]},
    # ── Volkswagen ──
    {"nom": "🔘 Polo | 500-3500€ | max 220k km",      "make": "Volkswagen", "model": "Polo","prix_min": 500,  "prix_max": 3500, "km_min": 0, "km_max": 220000, "fuel": ["diesel","petrol"]},
    # ── Utilitaire ──
    {"nom": "🚐 Utilitaire | 500-2500€ | max 230k km","make": None,      "model": None,     "prix_min": 500,  "prix_max": 2500, "km_min": 0, "km_max": 230000, "fuel": ["diesel","petrol"], "type": "van"},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)

# ── SAUVEGARDE ────────────────────────────────────────────────
SAVE_FILE = "/tmp/deja_vus.json"

def charger_deja_vus():
    global deja_vus
    try:
        if os.path.exists(SAVE_FILE):
            with open(SAVE_FILE, "r") as f:
                deja_vus = set(json.load(f))
            log.info(f"📂 {len(deja_vus)} annonces chargées")
    except Exception as e:
        log.error("Erreur chargement : %s", e)

def sauvegarder_deja_vus():
    try:
        with open(SAVE_FILE, "w") as f:
            json.dump(list(deja_vus), f)
    except Exception as e:
        log.error("Erreur sauvegarde : %s", e)

# ── CONNEXION AUTO1 ───────────────────────────────────────────
def connexion():
    global derniere_connexion
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
            derniere_connexion = datetime.now()
            return True
        else:
            log.warning("⚠️ Connexion incertaine")
            return False
    except Exception as e:
        log.error("Erreur connexion : %s", e)
        return False

def verifier_session():
    global derniere_connexion
    if not derniere_connexion or (datetime.now() - derniere_connexion).seconds > 7200:
        connexion()

# ── GEMINI AI (amélioré) ──────────────────────────────────────
def analyser_avec_gemini(voiture):
    if not GEMINI_API_KEY:
        return None, None, None
    try:
        prompt = f"""Tu es un expert en achat de voitures d'occasion en France. Analyse cette annonce Auto1 :

Véhicule : {voiture['titre']} ({voiture['annee']})
Prix annonce : {voiture['prix']} €
Kilométrage : {voiture['km']} km
Carburant : {voiture['carbu']}
Profil recherche : {voiture['profil']}

Réponds en JSON uniquement, sans markdown, sans backticks :
{{
  "score": <note de 1 à 10 (10 = excellente affaire)>,
  "verdict": "<🔥 Excellente affaire / ✅ Bonne affaire / 👍 Affaire correcte / ⚠️ Prix élevé>",
  "cote_marche": "<estimation du prix marché actuel pour ce véhicule en France, ex: 1200-1500€>",
  "ecart_prix": "<sous-coté / dans la moyenne / sur-coté>",
  "fiabilite": "<note de fiabilité du modèle : Excellente / Bonne / Moyenne / Mauvaise>",
  "points_vigilance_modele": "<problèmes connus sur ce modèle/moteur à surveiller>",
  "resume": "<1 phrase percutante résumant l'intérêt de l'annonce>",
  "points_positifs": "<ce qui est bien>",
  "points_negatifs": "<points de vigilance spécifiques à cette annonce>",
  "conseil": "<conseil d'achat concret en 1 phrase>"
}}"""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        score = data.get("score", 0)
        verdict = data.get("verdict", "")
        resume = (
            f"🤖 Analyse Gemini\n"
            f"⭐ Score : {score}/10 — {verdict}\n"
            f"💶 Cote marché : {data.get('cote_marche','?')} ({data.get('ecart_prix','?')})\n"
            f"🔧 Fiabilité : {data.get('fiabilite','?')} — {data.get('points_vigilance_modele','')}\n"
            f"📝 {data.get('resume','')}\n"
            f"✅ {data.get('points_positifs','')}\n"
            f"⚠️ {data.get('points_negatifs','')}\n"
            f"💡 {data.get('conseil','')}"
        )
        return score, verdict, resume
    except Exception as e:
        log.error("Gemini erreur : %s", e)
        return None, None, None

# ── RÉSUMÉ QUOTIDIEN 20H ─────────────────────────────────────
def envoyer_resume_quotidien():
    log.info("📊 Envoi résumé quotidien...")
    if meilleures_annonces:
        texte = (
            f"🌅 *Résumé du jour — {escape_md(str(date.today().strftime('%d/%m/%Y')))}*\n\n"
            f"📊 {escape_md(str(stats_jour['total']))} annonces vues\n"
            f"🔔 {escape_md(str(stats_jour['alertes']))} alertes envoyées\n\n"
            f"🏆 *Top annonces du jour :*\n\n"
        )
        for i, v in enumerate(meilleures_annonces[:5], 1):
            texte += (
                f"{i}\\. ⭐ *{escape_md(str(v.get('gemini_score','?')))}*/10 — "
                f"[{escape_md(v['titre'])}]({v['url']})\n"
                f"   💰 {escape_md(str(v['prix']))}€ | 📏 {escape_md(str(v['km']))}km | {escape_md(v.get('gemini_verdict',''))}\n\n"
            )
    else:
        texte = (
            f"🌅 *Résumé du jour — {escape_md(str(date.today().strftime('%d/%m/%Y')))}*\n\n"
            f"📊 {escape_md(str(stats_jour['total']))} annonces vues\n"
            f"😴 Aucune affaire intéressante aujourd'hui\\."
        )
    envoyer_telegram_message(TELEGRAM_CHAT_ID, texte)

    # Aussi via ntfy
    try:
        corps = f"📊 {stats_jour['total']} annonces vues, {stats_jour['alertes']} alertes"
        if meilleures_annonces:
            v = meilleures_annonces[0]
            corps += f"\n🏆 Meilleure : {v['titre']} — {v['prix']}€ (⭐{v.get('gemini_score','?')}/10)"
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=corps.encode("utf-8"),
            headers={"Title": f"📅 Résumé Auto1 — {date.today().strftime('%d/%m/%Y')}", "Tags": "calendar,auto1"},
            timeout=10
        )
    except Exception as e:
        log.error("Ntfy résumé erreur : %s", e)

# ── NTFY.SH ───────────────────────────────────────────────────
def envoyer_ntfy(voiture, score=None, verdict=None):
    try:
        if score and isinstance(score, (int, float)):
            if score >= 8:   priority, emoji = "urgent", "🔥"
            elif score >= 6: priority, emoji = "high", "✅"
            else:            priority, emoji = "default", "🚗"
        else:
            priority, emoji = "default", "🚗"

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

def envoyer_telegram_annonce(voiture, analyse_texte=None):
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
                f"<td><b>{v.get('gemini_score','—')}/10</b></td></tr>"
                for v in vlist
            )
            sections += (
                f"<h3>{profil}</h3>"
                f"<table border='1' cellpadding='5'>"
                f"<tr style='background:#333;color:white'><th>Véhicule</th><th>Prix</th>"
                f"<th>Km</th><th>Carbu</th><th>Verdict Gemini</th><th>Score</th></tr>"
                f"{lignes}</table><br>"
            )
        msg.attach(MIMEText(f"<html><body style='font-family:Arial'>{sections}</body></html>", "html"))
        with smtplib.SMTP(SMTP_SERVEUR, SMTP_PORT) as srv:
            srv.starttls()
            srv.login(EMAIL_EXPEDITEUR, EMAIL_MOT_PASSE)
            srv.sendmail(EMAIL_EXPEDITEUR, EMAIL_DESTINATAIRE, msg.as_string())
        log.info("📧 Email envoyé")
    except Exception as e:
        log.error("Email : %s", e)

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

# ── BOT TELEGRAM (commandes) ──────────────────────────────────
last_update_id = None

def ecouter_telegram():
    global last_update_id, surveillance_active
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
                    statut = "🟢 Active" if surveillance_active else "🔴 En pause"
                    reponse = (
                        "🚀 *Auto1 Monitor est actif\\!*\n\n"
                        f"✅ *{len(RECHERCHES)} profils* surveillés\n"
                        f"🤖 Analyse Gemini AI \\(cote marché \\+ fiabilité\\)\n"
                        f"📲 Notifications ntfy\\.sh\n"
                        f"🌅 Résumé automatique à {HEURE_RESUME}h\n"
                        f"🔄 Intervalle : *{INTERVALLE_SEC}s*\n"
                        f"📊 Surveillance : {escape_md(statut)}\n\n"
                        "Commandes : /status /pause /resume /stats /resume\\_jour /help"
                    )
                    envoyer_telegram_message(chat_id, reponse)

                elif texte == "/pause":
                    surveillance_active = False
                    envoyer_telegram_message(chat_id,
                        "⏸️ *Surveillance mise en pause*\n\nEnvoie /resume pour reprendre\\.")

                elif texte == "/resume":
                    surveillance_active = True
                    envoyer_telegram_message(chat_id,
                        "▶️ *Surveillance reprise\\!*\n\nLe bot surveille à nouveau Auto1\\.")

                elif texte == "/status":
                    statut = "🟢 Active" if surveillance_active else "🔴 En pause"
                    reponse = (
                        f"📊 *Statut du bot*\n\n"
                        f"Surveillance : {escape_md(statut)}\n"
                        f"👁️ Annonces vues : *{escape_md(str(len(deja_vus)))}*\n"
                        f"🔔 Alertes aujourd'hui : *{escape_md(str(stats_jour['alertes']))}*\n"
                        f"🔄 Cycles effectués : *{escape_md(str(cycle_count))}*\n"
                        f"🤖 Gemini : {'✅ actif' if GEMINI_API_KEY else '❌ non configuré'}\n"
                        f"📲 Ntfy : ✅ `{escape_md(NTFY_TOPIC)}`\n"
                        f"⭐ Score min alerte : *{SCORE_MIN}/10*\n"
                        f"🌅 Résumé à : *{HEURE_RESUME}h00*\n"
                        f"🕒 {escape_md(datetime.now().strftime('%d/%m/%Y %H:%M'))}"
                    )
                    envoyer_telegram_message(chat_id, reponse)

                elif texte == "/stats":
                    if meilleures_annonces:
                        reponse = f"🏆 *Meilleures annonces du jour*\n\n"
                        for i, v in enumerate(meilleures_annonces[:5], 1):
                            reponse += (
                                f"{i}\\. ⭐ *{escape_md(str(v.get('gemini_score','?')))}*/10 — "
                                f"[{escape_md(v['titre'])}]({v['url']})\n"
                                f"   💰 {escape_md(str(v['prix']))}€ | 📏 {escape_md(str(v['km']))}km\n\n"
                            )
                    else:
                        reponse = "📊 *Stats du jour*\n\nAucune annonce intéressante encore trouvée\\."
                    envoyer_telegram_message(chat_id, reponse)

                elif texte == "/resume_jour":
                    envoyer_resume_quotidien()

                elif texte == "/help":
                    reponse = (
                        "ℹ️ *Commandes disponibles*\n\n"
                        "/start \\- Infos du bot\n"
                        "/status \\- Statut détaillé\n"
                        "/pause \\- Mettre en pause\n"
                        "/resume \\- Reprendre la surveillance\n"
                        "/stats \\- Top annonces du jour\n"
                        "/resume\\_jour \\- Résumé immédiat\n"
                        "/help \\- Aide"
                    )
                    envoyer_telegram_message(chat_id, reponse)

        except Exception as e:
            log.error("Erreur écoute Telegram : %s", e)
        time.sleep(1)

# ── BOUCLE PRINCIPALE ─────────────────────────────────────────
def main():
    global cycle_count, stats_jour, meilleures_annonces, resume_envoye_aujourdhui

    log.info("🚀 Auto1 Monitor démarré")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("❌ TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquant !")
        return
    if not AUTO1_EMAIL or not AUTO1_PASSWORD:
        log.error("❌ AUTO1_EMAIL ou AUTO1_PASSWORD manquant !")
        return

    charger_deja_vus()

    t = threading.Thread(target=ecouter_telegram, daemon=True)
    t.start()

    connexion()

    envoyer_telegram_message(
        TELEGRAM_CHAT_ID,
        f"🚀 *Auto1 Monitor actif\\!*\n"
        f"✅ {len(RECHERCHES)} profils surveillés\n"
        f"🤖 Gemini AI : cote marché \\+ fiabilité activées\n"
        f"📲 Ntfy\\.sh : `{escape_md(NTFY_TOPIC)}`\n"
        f"⭐ Score min alerte : {SCORE_MIN}/10\n"
        f"🌅 Résumé quotidien à {HEURE_RESUME}h00\n"
        f"🔄 Cycle toutes les {INTERVALLE_SEC}s\n\n"
        f"Commandes : /status /pause /resume /stats /help"
    )

    while True:
        now = datetime.now()

        # Reset stats quotidiennes
        today_str = str(date.today())
        if today_str != stats_jour["date"]:
            stats_jour = {"total": 0, "alertes": 0, "date": today_str}
            meilleures_annonces = []
            resume_envoye_aujourdhui = False
            log.info("📅 Nouveau jour — stats réinitialisées")

        # Résumé quotidien automatique à 20h
        if now.hour == HEURE_RESUME and not resume_envoye_aujourdhui:
            envoyer_resume_quotidien()
            resume_envoye_aujourdhui = True

        if not surveillance_active:
            log.info("⏸️ Surveillance en pause...")
            time.sleep(10)
            continue

        cycle_count += 1
        log.info(f"── Cycle #{cycle_count} ── {now.strftime('%H:%M:%S')} ──")

        verifier_session()

        nouveaux_a_alerter = []

        for c in RECHERCHES:
            url = construire_url(c)
            vlist = scraper(url, c)
            for v in vlist:
                cle = f"{c['nom']}|{v['id']}"
                if v["id"] and cle not in deja_vus:
                    deja_vus.add(cle)
                    stats_jour["total"] += 1

                    score, verdict, analyse_texte = analyser_avec_gemini(v)
                    v["gemini_score"]   = score
                    v["gemini_verdict"] = verdict or "—"

                    log.info(f"  🆕 {v['titre']} — {v['prix']}€ | Score: {score}/10")

                    if score and isinstance(score, (int, float)) and score >= 7:
                        meilleures_annonces.append(v)
                        meilleures_annonces.sort(key=lambda x: x.get("gemini_score", 0), reverse=True)

                    if score is None or (isinstance(score, (int, float)) and score >= SCORE_MIN):
                        nouveaux_a_alerter.append((v, analyse_texte, score, verdict))
                        stats_jour["alertes"] += 1

            time.sleep(3)

        for v, analyse_texte, score, verdict in nouveaux_a_alerter:
            envoyer_telegram_annonce(v, analyse_texte)
            envoyer_ntfy(v, score, verdict)
            time.sleep(0.5)

        if nouveaux_a_alerter:
            envoyer_email([item[0] for item in nouveaux_a_alerter])

        sauvegarder_deja_vus()

        log.info(f"😴 Prochain cycle dans {INTERVALLE_SEC}s... ({stats_jour['alertes']} alertes aujourd'hui)")
        time.sleep(INTERVALLE_SEC)

if __name__ == "__main__":
    main()
