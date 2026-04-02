import os

# Other content of app.py

app.secret_key = os.environ.get("SECRET_KEY", "dev-key")

# Other content of app.py

port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)