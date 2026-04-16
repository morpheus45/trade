# scripts/install.bat

@echo off
echo Installation des dépendances...
pip install ccxt pandas numpy xgboost transformers flask requests websocket-client scikit-learn joblib matplotlib torch torchvision torchaudio tensorflow flax
mkdir logs
mkdir models
mkdir data
echo Installation terminée !
pause
