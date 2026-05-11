import joblib
try:
    model = joblib.load("worksight_gps_model.pkl")
except:
    model = None
def predict_location(lat, lon):
    if model:
        result = model.predict([[lat, lon]])[0]
        return True if result == 1 else False
    return True
