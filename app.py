import os
from flask import Flask, jsonify
from flask_cors import CORS
from routes.documentos import documentos_bp


def create_app() -> Flask:
    """
    Crea una instancia de la aplicación Flask configurada para un entorno multi‑inquilino.

    La app registra el blueprint de documentos y configura CORS para permitir
    solicitudes desde cualquier origen. También expone una ruta simple para
    verificar que la API está en funcionamiento.
    """
    app = Flask(__name__)
    # Configurar CORS para todas las rutas bajo /api
    CORS(
        app,
        resources={r"/api/*": {"origins": "*"}},
        expose_headers=["Content-Disposition"],
        allow_headers=["Content-Type", "X-Tenant-ID"],
    )

    # Registrar blueprint para manejar documentos
    app.register_blueprint(documentos_bp, url_prefix="/api/documentos")

    @app.route("/api")
    def index() -> jsonify:
        """Ruta de diagnóstico para confirmar que la API se ejecuta."""
        return jsonify({
            "message": "API del Gestor de Documentos Multi-Inquilino funcionando.",
            "status": "ok",
        })

    return app


if __name__ == "__main__":
    # Permitir especificar el puerto vía variable de entorno, default 5001
    app = create_app()
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)