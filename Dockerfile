# syntax=docker/dockerfile:1
#
# Image du bot de trading : un seul processus qui fait tourner la boucle de
# trading et sert le dashboard (voir src/main.py).
#
# Python 3.11 : version pour laquelle toutes les dependances (xgboost, pandas,
# scikit-learn, ccxt) publient des wheels precompiles. Sur une version plus
# recente, pip essaierait de compiler depuis les sources.
FROM python:3.11-slim

# Fuseau europeen : les logs, les resets journaliers et les snapshots horaires
# doivent correspondre aux horaires de marche que tu lis.
ENV TZ=Europe/Paris \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Les dependances changent rarement : couche separee pour profiter du cache
# Docker a chaque modification du code.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/    ./src/
COPY models/ ./models/

# Utilisateur non privilegie : une faille dans le dashboard ne donne pas root.
RUN useradd --create-home --uid 10001 bot \
 && mkdir -p /app/data /app/logs \
 && chown -R bot:bot /app
USER bot

# Ces deux dossiers portent l'etat : positions ouvertes, historique, sessions.
# Sans volume monte dessus, tout est perdu a chaque recreation du conteneur.
VOLUME ["/app/data", "/app/logs"]

# Dans un conteneur, l'ecoute se fait sur toutes les interfaces : c'est Docker
# qui decide de ce qui est publie. Le mot de passe reste obligatoire (src/server.py).
ENV DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=5000

EXPOSE 5000

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=5).status==200 else 1)"

CMD ["python", "src/main.py"]
