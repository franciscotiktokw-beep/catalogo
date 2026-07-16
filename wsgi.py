# Punto de entrada WSGI para gunicorn (VPS Hostinger).
# Ejecutar: gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app
from catalogo_panel_server import app

if __name__ == "__main__":
    app.run()
