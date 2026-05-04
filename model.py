from sklearn.linear_model import LogisticRegression

def train_model(X, y):
    model = LogisticRegression()
    model.fit(X, y)
    return model

def predict_signal(model, features):
    prediction = model.predict([features])[0]
    probability = model.predict_proba([features])[0]

    confidence = max(probability)

    if prediction == 1:
        return "BUY", round(confidence, 2)
    else:
        return "SELL", round(confidence, 2)
    
def show_feature_importance(model):
    feature_names = [
        "regime",
        "price_above_ema",
        "rsi",
        "momentum",
        "volume",
        "volatility"
    ]

    print("\nFETURE IMPORTANCE")

    for name, coef in zip(feature_names, model.coef_[0]):
        print(f"{name}: {round(coef, 4)}")