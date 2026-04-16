from transformers import pipeline

# Charger explicitement le modèle pour éviter les avertissements
SENTIMENT_MODEL = "distilbert/distilbert-base-uncased-finetuned-sst-2-english"

def analyze_market(pair):
    """
    Analyse le sentiment du marché pour une paire donnée.
    """
    try:
        sentiment_pipeline = pipeline("sentiment-analysis", model=SENTIMENT_MODEL)
        result = sentiment_pipeline(f"Market analysis for {pair}")
        return result[0]['label'], result[0]['score']
    except Exception as e:
        print(f"❌ Erreur lors de l'analyse du sentiment : {e}")
        return None, None
