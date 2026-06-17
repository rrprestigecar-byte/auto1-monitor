"""
Auto1 Monitor — version corrigée et instrumentée
═══════════════════════════════════════════════════════════════
⚠️ AVERTISSEMENT IMPORTANT (à lire avant de redéployer) :

Auto1.com est une plateforme B2B (enchères pour professionnels de
l'automobile). Deux risques majeurs qui peuvent expliquer "0 notification"
et que je ne peux PAS diagnostiquer sans accès réseau dans mon environnement :

1. Le site peut nécessiter un compte pro vérifié (KYC) pour voir
   le moindre prix/annonce — même avec un login qui "réussit" techniquement.
2. La page de résultats peut être une SPA qui charge les annonces via un
   appel JS (fetch/XHR) APRÈS le rendu initial. Dans ce cas le bloc
   __NEXT_DATA__ scrappé ici ne contiendra jamais les vraies données.

→ Ce script envoie maintenant un RAPPORT DE DIAGNOSTIC complet sur
  Telegram au démarrage (et via la commande /diagnostic). Lance-le,
  regarde ce rapport, et renvoie-le-moi : on saura exactement quoi
  corriger ensuite au lieu de deviner.

→ Si tu veux la solution la plus fiable à terme : ouvre auto1.com dans
  un navigateur, F12 → onglet Network → filtre XHR/Fetch, lance une
  recherche correspondant à un de tes profils, et regarde quelle requête
  JSON contient réellement les annonces. Partage-moi son URL + ses
  paramètres : j'appellerai directement cette API, ce qui est bien plus
  fiable que parser du HTML.
═══════════════════════════════════════════════════════════════
"""

import os, re, time, logging, requests, json, smtplib, threading, hashlib, sys
from datetime import datetime, date
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── CONFIGURATION ─────────────────────────────────────────────
AUTO1_EMAIL        = os.environ.get("AUTO1_EMAIL", "")
AUTO1_PASSWORD     = os.environ.get("AUTO1_PASSWORD", "")
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
EMAIL_EXPEDITEUR   = os.environ.get("EMAIL_EXPEDITEUR", "")
EMAIL_MOT_PASSE    = os.environ.get("EMAIL_MOT_PASSE", "")
EMAIL_DESTINATAIRE = os.environ.get("EMAIL_DESTINATAIRE", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
NTFY_TOPIC         = os.environ.get("NTFY_TOPIC", "auto1-alertes")
SCORE_MIN          = int(os.environ.get("SCORE_MIN", "6"))
HEURE_RESUME       = int(os.environ.get("HEURE_RESUME", "20"))
INTERVALLE_SEC     = int(os.environ.get("INTERVALLE_SEC", "30"))
# Base d'URL à VÉRIFIER manuellement sur le site (voir avertissement plus haut)
AUTO1_BUY_URL_BASE = os.environ.get("AUTO1_BUY_URL_BASE", "https://www.auto1.com/fr/home/buy")

SMTP_SERVEUR = "smtp.office365.com"
SMTP_PORT    = 587

# Sur les hébergeurs avec disque éphémère (Railway/Render free tier...), /tmp
# peut être effacé à chaque redéploiement. Si tu as un disque persistant,
# pointe SAVE_FILE vers ce disque via une variable d'environnement.
SAVE_FILE = os.environ.get("SAVE_FILE", "/tmp/auto1_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── ÉTAT GLOBAL (protégé par lock car partagé entre threads) ──
etat_lock = threading.Lock()
surveillance_active = True
deja_vus = set()
meilleures_annonces = []
stats_jour = {"total": 0, "alertes": 0, "date": str(date.today())}
cycle_count = 0
derniere_connexion_ok = False
derniere_connexion_ts = None
resume_envoye_aujourdhui = False
dernier_diagnostic = {}

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)


# ── VALIDATION CONFIG ──────────────────────────────────────────
def verifier_config():
    manquants = []
    if not TELEGRAM_TOKEN:   manquants.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID: manquants.append("TELEGRAM_CHAT_ID")
    if not AUTO1_EMAIL:      manquants.append("AUTO1_EMAIL")
    if not AUTO1_PASSWORD:   manquants.append("AUTO1_PASSWORD")
    if manquants:
        log.error(f"❌ Variables d'environnement manquantes : {', '.join(manquants)}")
        log.error("Le bot ne peut pas démarrer sans ces variables. Arrêt.")
        sys.exit(1)
    if not GEMINI_API_KEY:
        log.warning("⚠️ GEMINI_API_KEY absent : les annonces seront envoyées sans score/analyse IA "
                    "(SCORE_MIN ne pourra jamais être évalué → toutes les annonces neuves seront alertées)")


# ── SAUVEGARDE / CHARGEMENT ÉTAT ───────────────────────────────
def charger_etat():
    global deja_vus, stats_jour, meilleures_annonces, resume_envoye_aujourdhui
    try:
        if os.path.exists(SAVE_FILE):
            with open(SAVE_FILE, "r") as f:
                data = json.load(f)
            deja_vus = set(data.get("deja_vus", []))
            if data.get("stats_jour", {}).get("date") == str(date.today()):
                stats_jour = data.get("stats_jour", stats_jour)
                meilleures_annonces = data.get("meilleures_annonces", [])
                resume_envoye_aujourdhui = data.get("resume_envoye", False)
            log.info(f"📂 État chargé : {len(deja_vus)} annonces déjà vues")
    except Exception as e:
        log.error("Erreur chargement état : %s", e)

def sauvegarder_etat():
    try:
        with etat_lock:
            payload = {
                "deja_vus": list(deja_vus),
                "stats_jour": stats_jour,
                "meilleures_annonces": meilleures_annonces[:10],
                "resume_envoye": resume_envoye_aujourdhui,
            }
        with open(SAVE_FILE, "w") as f:
            json.dump(payload, f)
    except Exception as e:
        log.error("Erreur sauvegarde état : %s", e)


# ── CONNEXION AUTO1 ───────────────────────────────────────────
def connexion(tentative=1, max_tentatives=3):
    global derniere_connexion_ok, derniere_connexion_ts
    log.info(f"🔑 Connexion à auto1.com (tentative {tentative}/{max_tentatives})...")
    try:
        r = session.get("https://www.auto1.com/fr/home/login", timeout=15)
        log.info(f"   → page login : statut {r.status_code}, {len(r.text)} caractères reçus")
        soup = BeautifulSoup(r.text, "lxml")
        csrf_input = soup.find("input", {"name": re.compile(r"csrf|_token", re.I)})
        csrf = csrf_input["value"] if csrf_input else ""
        if not csrf_input:
            log.warning("   ⚠️ Aucun token CSRF trouvé — la page de login a peut-être une structure "
                        "différente (rendu JS, captcha, ou formulaire chargé dynamiquement)")

        r2 = session.post(
            "https://www.auto1.com/fr/home/login",
            data={"email": AUTO1_EMAIL, "password": AUTO1_PASSWORD, "_token": csrf},
            timeout=15, allow_redirects=True
        )
        log.info(f"   → réponse login : statut {r2.status_code}, url finale {r2.url}")

        if "logout" in r2.text.lower() or "déconnexion" in r2.text.lower():
            log.info("✅ Connecté à Auto1")
            derniere_connexion_ok = True
            derniere_connexion_ts = datetime.now()
            return True

        log.warning("⚠️ Connexion incertaine : ni 'logout' ni 'déconnexion' trouvés dans la réponse. "
                    "Soit les identifiants sont refusés, soit le site utilise un mécanisme de login "
                    "différent (API JS, 2FA, captcha).")
        derniere_connexion_ok = False
        if tentative < max_tentatives:
            time.sleep(5)
            return connexion(tentative + 1, max_tentatives)
        return False
    except Exception as e:
        log.error("Erreur connexion : %s", e)
        derniere_connexion_ok = False
        return False

def verifier_session():
    if not derniere_connexion_ok or not derniere_connexion_ts or (datetime.now() - derniere_connexion_ts).seconds > 7200:
        connexion()


# ── GEMINI AI ───────────────────────────────────────────────
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
        if r.status_code != 200:
            log.error(f"Gemini HTTP {r.status_code} : {r.text[:300]}")
            return None, None, None
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


# ── TELEGRAM ──────────────────────────────────────────────────
def escape_md(t):
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(t))

def envoyer_telegram_message(chat_id, texte, parse_mode="MarkdownV2"):
    """Envoie un message Telegram et VÉRIFIE la réponse (corrige le bug des échecs silencieux)."""
    if not TELEGRAM_TOKEN or not chat_id:
        log.error("Telegram non configuré (token/chat_id manquant), message non envoyé")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": texte, "parse_mode": parse_mode}, timeout=10)
        data = r.json()
        if data.get("ok"):
            return True

        log.error(f"❌ Telegram a refusé le message : {data.get('description')}")
        # Filet de sécurité : si c'est un problème de parsing Markdown, on renvoie en texte brut
        # plutôt que de perdre la notification.
        if "parse" in str(data.get("description", "")).lower() or "entit" in str(data.get("description", "")).lower():
            brut = re.sub(r'\\(.)', r'\1', texte)  # retire les échappements markdown
            r2 = requests.post(url, json={"chat_id": chat_id, "text": brut}, timeout=10)
            data2 = r2.json()
            if data2.get("ok"):
                log.info("✅ Message renvoyé avec succès en texte brut après échec du Markdown")
                return True
            log.error(f"❌ Échec aussi en texte brut : {data2.get('description')}")
        return False
    except Exception as e:
        log.error("Telegram erreur réseau : %s", e)
        return False

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
        corps_notif = f"{voiture['profil']}\n📏 {voiture['km']} km | ⛽ {voiture['carbu']} | 📅 {voiture['annee']}\n"
        if verdict:
            corps_notif += f"🤖 {verdict}\n"
        corps_notif += f"👉 {voiture['url']}"

        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=corps_notif.encode("utf-8"),
            headers={"Title": titre_notif, "Priority": priority, "Tags": "car,auto1", "Click": voiture["url"]},
            timeout=10
        )
        if r.status_code >= 300:
            log.error(f"❌ Ntfy a répondu {r.status_code} : {r.text[:200]}")
        else:
            log.info(f"📲 Ntfy envoyé : {voiture['titre']}")
    except Exception as e:
        log.error("Ntfy erreur : %s", e)


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
    p = [("sort", "price_asc"), ("per_page", 48)]
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
    return f"{AUTO1_BUY_URL_BASE}?{urlencode(p)}"

def chercher_liste(obj, d=0):
    if d > 6: return []
    if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
        if any(k in obj[0] for k in ["price", "make", "model", "id"]): return obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = chercher_liste(v, d + 1)
            if r: return r
    return []

def formater_json(v, c):
    vid = str(v.get("id") or v.get("uuid") or "")
    titre = f"{v.get('make','') or v.get('brand','')} {v.get('model','')} {v.get('year','') or v.get('firstRegistrationYear','')}".strip()
    prix = v.get("price") or v.get("grossPrice") or "?"
    km = v.get("mileage") or v.get("kilometers") or "?"
    if not vid:
        # Filet de sécurité : si Auto1 ne renvoie pas d'ID exploitable, on en génère un stable
        # à partir du contenu plutôt que de jeter l'annonce silencieusement (bug corrigé).
        vid = hashlib.md5(f"{titre}-{prix}-{km}".encode()).hexdigest()[:12]
    return {
        "id":     vid,
        "profil": c["nom"],
        "titre":  titre,
        "prix":   prix,
        "km":     km,
        "carbu":  v.get("fuelType") or v.get("fuel") or "?",
        "annee":  v.get("year") or v.get("firstRegistrationYear") or "?",
        "url":    v.get("url") or f"https://www.auto1.com/car/{vid}",
        "date":   datetime.now().strftime("%d/%m/%Y %H:%M"),
    }

def scraper(url, c):
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            log.error(f"[{c['nom']}] HTTP {r.status_code} sur {url}")
            return []
        soup = BeautifulSoup(r.text, "lxml")
        scripts = soup.find_all("script", {"id": "__NEXT_DATA__"})
        if not scripts:
            log.warning(f"[{c['nom']}] Aucun bloc __NEXT_DATA__ trouvé ({len(r.text)} caractères reçus) — "
                        "structure de page différente ou contenu chargé en JS")
            return []
        data = json.loads(scripts[0].string)
        cars = chercher_liste(data)
        return [formater_json(v, c) for v in cars]
    except Exception as e:
        log.error("[%s] Erreur scraping : %s", c["nom"], e)
        return []


# ── DIAGNOSTIC DE DÉMARRAGE ─────────────────────────────────────
def diagnostic_demarrage():
    """Lance un test complet et envoie un rapport clair sur Telegram.
    C'est LE point d'entrée pour comprendre pourquoi rien n'arrive."""
    global dernier_diagnostic
    rapport = ["🔍 *Diagnostic Auto1 Monitor*\n"]

    login_ok = connexion()
    rapport.append(f"🔑 Connexion Auto1 : {'✅ OK' if login_ok else '❌ ÉCHEC'}")

    if RECHERCHES:
        test = RECHERCHES[0]
        url_test = construire_url(test)
        rapport.append(f"🌐 Test sur : {escape_md(test['nom'])}")
        try:
            r = session.get(url_test, timeout=20)
            rapport.append(f"📡 Statut HTTP : *{r.status_code}* — {len(r.text)} caractères reçus")
            soup = BeautifulSoup(r.text, "lxml")
            scripts = soup.find_all("script", {"id": "__NEXT_DATA__"})
            trouve = bool(scripts)
            rapport.append(f"📦 Bloc de données trouvé : {'✅ oui' if trouve else '❌ NON'}")
            nb_cars = 0
            if trouve:
                data = json.loads(scripts[0].string)
                cars = chercher_liste(data)
                nb_cars = len(cars)
                rapport.append(f"🚗 Annonces détectées : *{nb_cars}*")
            else:
                rapport.append("⚠️ Sans ce bloc, le bot ne peut rien extraire \\(cause la plus probable "
                                "du 'aucune notification'\\)\\. Page peut-être protégée ou rendue en JS\\.")
            dernier_diagnostic = {"login_ok": login_ok, "http_status": r.status_code,
                                   "next_data_trouve": trouve, "nb_cars": nb_cars, "ts": datetime.now().isoformat()}
        except Exception as e:
            rapport.append(f"❌ Erreur pendant le test : {escape_md(str(e))}")
            dernier_diagnostic = {"erreur": str(e), "ts": datetime.now().isoformat()}
    else:
        rapport.append("⚠️ RECHERCHES est vide\\.")

    rapport.append("\n_Renvoie ce rapport si le problème persiste — ça permet de cibler le vrai correctif._")
    texte = "\n".join(rapport)
    log.info("DIAGNOSTIC: " + json.dumps(dernier_diagnostic, ensure_ascii=False))
    envoyer_telegram_message(TELEGRAM_CHAT_ID, texte)


# ── RÉSUMÉ QUOTIDIEN ─────────────────────────────────────────
def envoyer_resume_quotidien():
    log.info("📊 Envoi résumé quotidien...")
    if meilleures_annonces:
        texte = (
            f"🌅 *Résumé du jour — {escape_md(date.today().strftime('%d/%m/%Y'))}*\n\n"
            f"📊 {escape_md(stats_jour['total'])} annonces vues\n"
            f"🔔 {escape_md(stats_jour['alertes'])} alertes envoyées\n\n"
            f"🏆 *Top annonces du jour :*\n\n"
        )
        for i, v in enumerate(meilleures_annonces[:5], 1):
            texte += (
                f"{i}\\. ⭐ *{escape_md(v.get('gemini_score','?'))}*/10 — "
                f"[{escape_md(v['titre'])}]({v['url']})\n"
                f"   💰 {escape_md(v['prix'])}€ | 📏 {escape_md(v['km'])}km | {escape_md(v.get('gemini_verdict',''))}\n\n"
            )
    else:
        texte = (
            f"🌅 *Résumé du jour — {escape_md(date.today().strftime('%d/%m/%Y'))}*\n\n"
            f"📊 {escape_md(stats_jour['total'])} annonces vues\n"
            f"😴 Aucune affaire intéressante aujourd'hui\\."
        )
    envoyer_telegram_message(TELEGRAM_CHAT_ID, texte)

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


# ── BOT TELEGRAM (commandes) ──────────────────────────────────
last_update_id = None

def ecouter_telegram():
    global last_update_id, surveillance_active
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=10)
    except Exception as e:
        log.warning("Impossible de supprimer le webhook existant : %s", e)

    log.info("👂 Écoute des commandes Telegram...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": last_update_id}
            r = requests.get(url, params=params, timeout=35)
            payload = r.json()
            if not payload.get("ok"):
                log.error("Erreur getUpdates Telegram : %s", payload.get("description"))
                time.sleep(5)
                continue

            for update in payload.get("result", []):
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
                        "Commandes : /status /pause /resume /stats /resume\\_jour /diagnostic /help"
                    )
                    envoyer_telegram_message(chat_id, reponse)

                elif texte == "/pause":
                    with etat_lock:
                        surveillance_active = False
                    envoyer_telegram_message(chat_id, "⏸️ *Surveillance mise en pause*\n\nEnvoie /resume pour reprendre\\.")

                elif texte == "/resume":
                    with etat_lock:
                        surveillance_active = True
                    envoyer_telegram_message(chat_id, "▶️ *Surveillance reprise\\!*\n\nLe bot surveille à nouveau Auto1\\.")

                elif texte == "/status":
                    statut = "🟢 Active" if surveillance_active else "🔴 En pause"
                    co = "✅ OK" if derniere_connexion_ok else "❌ ÉCHEC/inconnue"
                    reponse = (
                        f"📊 *Statut du bot*\n\n"
                        f"Surveillance : {escape_md(statut)}\n"
                        f"🔑 Connexion Auto1 : {escape_md(co)}\n"
                        f"👁️ Annonces vues \\(total\\) : *{escape_md(len(deja_vus))}*\n"
                        f"🔔 Alertes aujourd'hui : *{escape_md(stats_jour['alertes'])}*\n"
                        f"🔄 Cycles effectués : *{escape_md(cycle_count)}*\n"
                        f"🤖 Gemini : {'✅ actif' if GEMINI_API_KEY else '❌ non configuré'}\n"
                        f"⭐ Score min alerte : *{SCORE_MIN}/10*\n"
                        f"🌅 Résumé à : *{HEURE_RESUME}h00*\n"
                        f"🕒 {escape_md(datetime.now().strftime('%d/%m/%Y %H:%M'))}\n\n"
                        f"Tape /diagnostic pour un test complet de scraping\\."
                    )
                    envoyer_telegram_message(chat_id, reponse)

                elif texte == "/stats":
                    if meilleures_annonces:
                        reponse = "🏆 *Meilleures annonces du jour*\n\n"
                        for i, v in enumerate(meilleures_annonces[:5], 1):
                            reponse += (
                                f"{i}\\. ⭐ *{escape_md(v.get('gemini_score','?'))}*/10 — "
                                f"[{escape_md(v['titre'])}]({v['url']})\n"
                                f"   💰 {escape_md(v['prix'])}€ | 📏 {escape_md(v['km'])}km\n\n"
                            )
                    else:
                        reponse = "📊 *Stats du jour*\n\nAucune annonce intéressante encore trouvée\\."
                    envoyer_telegram_message(chat_id, reponse)

                elif texte == "/resume_jour":
                    envoyer_resume_quotidien()

                elif texte == "/diagnostic":
                    envoyer_telegram_message(chat_id, "🔍 Lancement du diagnostic, un instant\\.\\.\\.")
                    diagnostic_demarrage()

                elif texte == "/help":
                    reponse = (
                        "ℹ️ *Commandes disponibles*\n\n"
                        "/start \\- Infos du bot\n"
                        "/status \\- Statut détaillé\n"
                        "/pause \\- Mettre en pause\n"
                        "/resume \\- Reprendre la surveillance\n"
                        "/stats \\- Top annonces du jour\n"
                        "/resume\\_jour \\- Résumé immédiat\n"
                        "/diagnostic \\- Test complet de connexion et scraping\n"
                        "/help \\- Aide"
                    )
                    envoyer_telegram_message(chat_id, reponse)

        except Exception as e:
            log.error("Erreur écoute Telegram : %s", e)
            time.sleep(3)
        time.sleep(1)


# ── BOUCLE PRINCIPALE ─────────────────────────────────────────
def main():
    global cycle_count, stats_jour, meilleures_annonces, resume_envoye_aujourdhui

    log.info("🚀 Auto1 Monitor démarré")
    verifier_config()
    charger_etat()

    t = threading.Thread(target=ecouter_telegram, daemon=True)
    t.start()

    # Rapport de diagnostic immédiat — c'est la pièce maîtresse pour comprendre
    # pourquoi tu ne recevais rien jusqu'ici.
    diagnostic_demarrage()

    envoyer_telegram_message(
        TELEGRAM_CHAT_ID,
        f"🚀 *Auto1 Monitor actif\\!*\n"
        f"✅ {len(RECHERCHES)} profils surveillés\n"
        f"🤖 Gemini AI : {'✅ activé' if GEMINI_API_KEY else '❌ non configuré'}\n"
        f"⭐ Score min alerte : {SCORE_MIN}/10\n"
        f"🌅 Résumé quotidien à {HEURE_RESUME}h00\n"
        f"🔄 Cycle toutes les {INTERVALLE_SEC}s\n\n"
        f"Commandes : /status /pause /resume /stats /diagnostic /help"
    )

    while True:
        now = datetime.now()

        today_str = str(date.today())
        if today_str != stats_jour["date"]:
            with etat_lock:
                stats_jour = {"total": 0, "alertes": 0, "date": today_str}
                meilleures_annonces = []
                resume_envoye_aujourdhui = False
            log.info("📅 Nouveau jour — stats réinitialisées")

        if now.hour == HEURE_RESUME and not resume_envoye_aujourdhui:
            envoyer_resume_quotidien()
            with etat_lock:
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
            log.info(f"  [{c['nom']}] {len(vlist)} annonces récupérées sur la page")

            nouveaux_ce_profil = 0
            for v in vlist:
                cle = f"{c['nom']}|{v['id']}"
                if cle in deja_vus:
                    continue
                with etat_lock:
                    deja_vus.add(cle)
                    stats_jour["total"] += 1
                nouveaux_ce_profil += 1

                score, verdict, analyse_texte = analyser_avec_gemini(v)
                v["gemini_score"]   = score
                v["gemini_verdict"] = verdict or "—"

                log.info(f"  🆕 {v['titre']} — {v['prix']}€ | Score: {score}/10")

                if score and isinstance(score, (int, float)) and score >= 7:
                    with etat_lock:
                        meilleures_annonces.append(v)
                        meilleures_annonces.sort(key=lambda x: x.get("gemini_score", 0) or 0, reverse=True)

                if score is None or (isinstance(score, (int, float)) and score >= SCORE_MIN):
                    nouveaux_a_alerter.append((v, analyse_texte, score, verdict))
                    with etat_lock:
                        stats_jour["alertes"] += 1

            if nouveaux_ce_profil == 0 and not vlist:
                log.info(f"  [{c['nom']}] ⚠️ 0 annonce récupérée — voir /diagnostic si ça persiste")

            time.sleep(3)

        for v, analyse_texte, score, verdict in nouveaux_a_alerter:
            envoyer_telegram_annonce(v, analyse_texte)
            envoyer_ntfy(v, score, verdict)
            time.sleep(0.5)

        if nouveaux_a_alerter:
            envoyer_email([item[0] for item in nouveaux_a_alerter])

        sauvegarder_etat()

        log.info(f"😴 Prochain cycle dans {INTERVALLE_SEC}s... ({stats_jour['alertes']} alertes aujourd'hui)")
        time.sleep(INTERVALLE_SEC)


if __name__ == "__main__":
    main()
