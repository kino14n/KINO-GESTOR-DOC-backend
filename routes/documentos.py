import os
import json
from flask import Blueprint, request, jsonify, g
from werkzeug.utils import secure_filename
import pymysql
import boto3


# Blueprint para agrupar las rutas de documentos
documentos_bp = Blueprint("documentos", __name__)

# Cargar la configuración de inquilinos desde un JSON en disco
with open('tenants.json', 'r') as f:
    TENANTS_CONFIG = json.load(f)


@documentos_bp.before_request
def identify_tenant():
    """
    Middleware que identifica el inquilino en función del encabezado X‑Tenant‑ID.
    Si no se proporciona un inquilino válido, la solicitud es rechazada.
    El identificador y la configuración del inquilino se almacenan en el
    contexto global (g) para que los demás handlers puedan usarlos.
    """
    tenant_id = request.headers.get('X-Tenant-ID')
    if not tenant_id or tenant_id not in TENANTS_CONFIG:
        return jsonify({"error": "Inquilino no válido o no especificado"}), 403
    g.tenant_id = tenant_id
    g.tenant_config = TENANTS_CONFIG[tenant_id]


def get_db_connection():
    """
    Devuelve una nueva conexión a la base de datos del inquilino actual.

    Utiliza los datos de conexión almacenados en la configuración del inquilino.
    """
    config = g.tenant_config
    return pymysql.connect(
        host=config["db_host"],
        user=config["db_user"],
        password=config["db_pass"],
        database=config["db_name"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def get_s3_client():
    """
    Crea y devuelve un cliente boto3 configurado para Cloudflare R2.

    La configuración se obtiene de variables de entorno:
    R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY.
    """
    return boto3.client(
        's3',
        endpoint_url=os.getenv("R2_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name='auto',
    )


def _codes_list(raw: str):
    """
    Convierte una cadena de códigos separados por comas o saltos de línea en
    una lista de códigos normalizada (en mayúsculas y sin espacios vacíos).
    """
    if not raw:
        return []
    return [c.strip().upper() for c in raw.replace("\n", ",").split(",") if c.strip()]


@documentos_bp.route("/upload", methods=["POST"])
def upload_document():
    """
    Maneja la subida de un nuevo documento para el inquilino actual.

    Se acepta un archivo mediante multipart/form-data y se guarda tanto en la
    base de datos como en el bucket de R2. Los códigos asociados, si los hay,
    se insertan en la tabla codes.
    """
    if "file" not in request.files:
        return jsonify({"error": "No se envió el archivo"}), 400
    f = request.files["file"]
    tenant_id = g.tenant_id
    # Asegurar un nombre de archivo seguro
    filename = secure_filename(f.filename)
    object_key = f"{tenant_id}/{filename}"
    bucket_name = os.getenv("R2_BUCKET_NAME")
    s3 = get_s3_client()
    try:
        s3.upload_fileobj(
            f,
            bucket_name,
            object_key,
            ExtraArgs={'ContentType': f.content_type},
        )
    except Exception as e:
        return jsonify({"error": f"Error al subir a R2: {str(e)}"}), 500

    # Recoger otros campos del formulario
    name = request.form.get("name") or filename
    date = request.form.get("date")
    codigos = request.form.get("codigos")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (name, date, path) VALUES (%s, %s, %s)",
                (name, date, object_key),
            )
            document_id = cur.lastrowid
            if codigos:
                for code in _codes_list(codigos):
                    cur.execute(
                        "INSERT INTO codes (document_id, code) VALUES (%s, %s)",
                        (document_id, code),
                    )
        return jsonify({"ok": True, "id": document_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@documentos_bp.route("/", methods=["GET"])
def listar_documentos():
    """
    Devuelve todos los documentos del inquilino actual junto con sus códigos.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.name, d.date, d.path,
                       GROUP_CONCAT(c.code ORDER BY c.code) AS codigos_extraidos
                FROM documents d
                LEFT JOIN codes c ON c.document_id = d.id
                GROUP BY d.id
                ORDER BY d.id DESC
                """
            )
            return jsonify(cur.fetchall())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@documentos_bp.route("/<int:doc_id>", methods=["DELETE"])
def eliminar_documento(doc_id: int):
    """
    Elimina un documento y sus códigos asociados para el inquilino actual.

    Además intenta eliminar el archivo asociado en R2. Si ocurre un error al
    eliminar de R2, se registra pero no impide la eliminación en la base de
    datos.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Obtener la ruta del archivo para eliminarlo del almacenamiento
            cur.execute("SELECT path FROM documents WHERE id=%s", (doc_id,))
            row = cur.fetchone()
            if row and row.get("path"):
                try:
                    s3 = get_s3_client()
                    s3.delete_object(
                        Bucket=os.getenv("R2_BUCKET_NAME"),
                        Key=row["path"],
                    )
                except Exception as e:
                    # Registrar el error pero continuar
                    print(f"Advertencia: No se pudo eliminar de R2: {e}")
            # Eliminar códigos y documento de la base de datos
            cur.execute("DELETE FROM codes WHERE document_id=%s", (doc_id,))
            cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()