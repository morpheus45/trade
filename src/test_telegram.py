import requests
import config

def send_telegram_message(message):
    """Envoie un message test sur Telegram"""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, data=data)
    print(response.json())  # Affiche la réponse de l'API Telegram

# Test d'envoi
send_telegram_message("✅ Test réussi ! Les alertes Telegram fonctionnent 🎉")
