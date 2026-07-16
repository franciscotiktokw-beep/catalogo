# Punto de entrada WSGI para Hostinger (hosting compartido con "Setup Python App").
# Passenger busca la variable `application`.
from catalogo_panel_server import app as application

if __name__ == "__main__":
    application.run()
