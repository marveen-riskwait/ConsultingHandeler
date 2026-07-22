"""
This module takes care of starting the API Server, Loading the DB and Adding the endpoints
"""
import os
from datetime import timedelta
from flask import Flask, request, jsonify, url_for, send_from_directory
from flask_migrate import Migrate
from flask_swagger import swagger
from flask_jwt_extended import JWTManager
from api.utils import APIException, generate_sitemap
from api.models import db
from api.routes import api
from api.portal import portal
from api.admin import setup_admin
from api.commands import setup_commands
from api.sockets import socketio
from api.integrations.media import UPLOADS_DIR

# from models import Person

ENV = "development" if os.getenv("FLASK_DEBUG") == "1" else "production"
static_file_dir = os.path.join(os.path.dirname(
    os.path.realpath(__file__)), '../dist/')
app = Flask(__name__)
app.url_map.strict_slashes = False

# database condiguration
db_url = os.getenv("DATABASE_URL")
if db_url is not None:
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url.replace(
        "postgres://", "postgresql://")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:////tmp/test.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


def sync_permission_catalog():
    """Make every permission defined in code grantable, on every boot.

    Only the Permission catalog is synced — role assignments are deliberately
    left alone, so permissions an administrator granted or revoked by hand in
    the matrix survive a deploy. (`flask sync-rbac` still resets roles to the
    defaults when that is what you want.)

    Without this, a permission added in code shows up in the admin matrix — the
    catalog is served from code — but cannot be granted, because granting looks
    up a Permission row that only existed after someone remembered to run a CLI
    command. That is a deployment trap, not an authorization decision.
    """
    from sqlalchemy import inspect as sa_inspect
    with app.app_context():
        try:
            if not sa_inspect(db.engine).has_table("permission"):
                return  # fresh clone: `flask db upgrade` has not run yet
            from api.rbac import sync_permissions
            sync_permissions()
        except Exception as exc:  # provisioning must never block startup
            db.session.rollback()
            print(f"[rbac] permission catalog sync skipped: {exc}")
# render_as_batch: emit batch (table-rebuild) ALTERs so migrations generated
# here run on SQLite too, not only PostgreSQL.
MIGRATE = Migrate(app, db, compare_type=True, render_as_batch=True)
db.init_app(app)

# JWT authentication
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY") or os.getenv(
    "FLASK_APP_KEY", "change-me-in-production")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)
jwt = JWTManager(app)

# Real-time layer (team chat + call signalling). Threading mode: works under
# the dev server and gunicorn threads; simple-websocket upgrades to real WS.
socketio.init_app(app, cors_allowed_origins="*", async_mode="threading")

# add the admin
setup_admin(app)

# add the admin
setup_commands(app)

# Keep the grantable permission catalog in step with the code.
sync_permission_catalog()

# Add all endpoints form the API with a "api" prefix
app.register_blueprint(api, url_prefix='/api')
# The customer portal is its own surface: see api/portal.py.
app.register_blueprint(portal, url_prefix='/api/portal')

# Handle/serialize errors like a JSON object


@app.errorhandler(APIException)
def handle_invalid_usage(error):
    return jsonify(error.to_dict()), error.status_code

# generate sitemap with all your endpoints


@app.route('/')
def sitemap():
    if ENV == "development":
        return generate_sitemap(app)
    return send_from_directory(static_file_dir, 'index.html')

# Chat media stored on local disk (no Cloudinary configured). Names are
# random UUIDs, which is what makes the unauthenticated GET acceptable.
@app.route('/api/media/<path:name>', methods=['GET'])
def serve_chat_media(name):
    return send_from_directory(UPLOADS_DIR, name)


# any other endpoint will try to serve it like a static file
@app.route('/<path:path>', methods=['GET'])
def serve_any_other_file(path):
    if not os.path.isfile(os.path.join(static_file_dir, path)):
        path = 'index.html'
    response = send_from_directory(static_file_dir, path)
    response.cache_control.max_age = 0  # avoid cache memory
    return response


# this only runs if `$ python src/main.py` is executed
if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 3001))
    # socketio.run wraps app.run and serves WebSocket alongside HTTP.
    socketio.run(app, host='0.0.0.0', port=PORT, debug=True,
                 allow_unsafe_werkzeug=True)
