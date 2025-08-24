from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    send_file, send_from_directory, jsonify, Response
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from io import BytesIO
from sqlalchemy import text, func, Index
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import time
import hashlib

# ========= TIMEZONE =========
import pytz
UTC = pytz.utc
TZ_SP = pytz.timezone("America/Sao_Paulo")

def format_brasilia(dt: datetime) -> str:
    """Converte UTC → horário de Brasília para exibição"""
    if not dt:
        return ""
    return dt.replace(tzinfo=UTC).astimezone(TZ_SP).strftime("%d/%m/%Y %H:%M")

# ========= APP / CONFIG =========
app = Flask(__name__)
app.secret_key = 'coopex-secreto'

# Corrige scheme/host atrás do proxy para cookies seguros e redirects corretos
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Sessão/Cookies
app.permanent_session_lifetime = timedelta(hours=10)
app.config.update(
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,   # Render usa HTTPS
    TEMPLATES_AUTO_RELOAD=False,  # evita rebuild de template em prod
    JSONIFY_PRETTYPRINT_REGULAR=False,
    JSON_SORT_KEYS=False,
)

# SQLAlchemy (PostgreSQL no Render)
app.config['SQLALCHEMY_DATABASE_URI'] = (
    'postgresql+psycopg://'
    'banco_dados_9ooo_user:4eebYkKJwygTnOzrU1PAMFphnIli4iCH'
    '@dpg-d28sr2juibrs73du5n80-a.oregon-postgres.render.com/banco_dados_9ooo'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_size': 10,       # mais conexões fixas
    'max_overflow': 20,    # maior burst
    'pool_timeout': 30,
    'pool_recycle': 1800,
}

# Estáticos mais rápidos (cache padrão de 1 dia)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400

# Pastas
app.config['UPLOAD_FOLDER_COOPERADOS'] = 'static/uploads'
app.config['UPLOAD_FOLDER_LOGOS'] = 'static/logos'
app.config['STATICS_FOLDER'] = 'statics'
os.makedirs(app.config['UPLOAD_FOLDER_COOPERADOS'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER_LOGOS'], exist_ok=True)
os.makedirs(app.config['STATICS_FOLDER'], exist_ok=True)

# Compressão Gzip (opcional)
try:
    from flask_compress import Compress
    Compress(app)
except Exception:
    pass

db = SQLAlchemy(app)

# ========= MODELS =========
class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    credito = db.Column(db.Float, default=0)
    foto = db.Column(db.String(120), nullable=True)
    foto_data = db.Column(db.LargeBinary, nullable=True)      # BYTEA
    foto_mimetype = db.Column(db.String(50), nullable=True)
    foto_filename = db.Column(db.String(120), nullable=True)

class Estabelecimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    logo = db.Column(db.String(120), nullable=True)
    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)
    def checar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)
    def checar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

class Lancamento(db.Model):
    __tablename__ = 'lancamento'
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    os_numero = db.Column(db.String(50), nullable=False)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=False, index=True)
    estabelecimento_id = db.Column(db.Integer, db.ForeignKey('estabelecimento.id'), nullable=False, index=True)
    valor = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(250))
    cooperado = db.relationship('Cooperado')
    estabelecimento = db.relationship('Estabelecimento')

Index('ix_lancamento_coop_estab_data',
      Lancamento.cooperado_id, Lancamento.estabelecimento_id, Lancamento.data.desc())

# ========= HELPERS =========
def is_admin(): return session.get('user_tipo') == 'admin'
def is_estabelecimento(): return session.get('user_tipo') == 'estabelecimento'

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d')
    except Exception:
        return None

# ========= OTIMIZAÇÃO DE LENTIDÃO =========
# Cache leve do último lançamento (já existia)
_LAST_LANC_CACHE = {"value": 0, "ts": 0.0}
_LAST_LANC_TTL = 2.0  # segundos

def _get_cached_last_lanc_id():
    now = time.time()
    if now - _LAST_LANC_CACHE["ts"] <= _LAST_LANC_TTL and _LAST_LANC_CACHE["ts"] > 0:
        return _LAST_LANC_CACHE["value"], True
    last_id = db.session.query(func.max(Lancamento.id)).scalar() or 0
    _LAST_LANC_CACHE["value"] = int(last_id)
    _LAST_LANC_CACHE["ts"] = now
    return _LAST_LANC_CACHE["value"], False

def _invalidate_last_lanc_cache(): _LAST_LANC_CACHE["ts"] = 0.0
def _update_last_lanc_cache_with_value(v: int):
    _LAST_LANC_CACHE["value"] = max(_LAST_LANC_CACHE["value"], int(v))
    _LAST_LANC_CACHE["ts"] = time.time()

# ========= PAINEL ESTABELECIMENTO (com fuso) =========
@app.route('/painel_estabelecimento', methods=['GET', 'POST'])
def painel_estabelecimento():
    if not is_estabelecimento():
        return redirect(url_for('login'))
    est = Estabelecimento.query.get(session['user_id'])
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()

    if request.method == 'POST':
        cooperado_id = request.form.get('cooperado_id')
        valor = request.form.get('valor')
        os_numero = request.form.get('os_numero')
        descricao = request.form.get('descricao')

        if cooperado_id and valor and os_numero:
            c = Cooperado.query.get(int(cooperado_id))
            if c:
                valor_f = float(valor)
                if c.credito >= valor_f:
                    l = Lancamento(
                        data=datetime.utcnow(),
                        os_numero=os_numero,
                        cooperado_id=c.id,
                        estabelecimento_id=est.id,
                        valor=valor_f,
                        descricao=descricao
                    )
                    db.session.add(l)
                    c.credito -= valor_f
                    db.session.commit()
                    _update_last_lanc_cache_with_value(l.id)
                    flash('Lançamento realizado com sucesso!', 'success')
                else:
                    flash('Crédito insuficiente para este lançamento.', 'danger')
            else:
                flash('Cooperado não encontrado!', 'danger')
        else:
            flash('Preencha todos os campos obrigatórios!', 'danger')

    lancamentos = Lancamento.query.filter_by(estabelecimento_id=est.id)\
                                  .order_by(Lancamento.data.desc()).all()
    for l in lancamentos:
        l.data_brasilia = format_brasilia(l.data)

    return render_template('painel_estabelecimento.html',
                           est=est, cooperados=cooperados, lancamentos=lancamentos)

# ========= EXPORTAÇÃO LANÇAMENTOS =========
@app.route('/lancamentos/exportar')
def exportar_lancamentos():
    if not is_admin():
        return redirect(url_for('login'))
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter

    rows = Lancamento.query.order_by(Lancamento.data.desc()).all()
    wb = Workbook(); ws = wb.active; ws.title = "Lançamentos"
    header = ["Data", "Nº OS", "Cooperado", "Estabelecimento", "Valor (R$)", "Descrição"]
    ws.append(header)
    for c in range(1, len(header)+1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for l in rows:
        ws.append([
            format_brasilia(l.data),
            l.os_numero,
            l.cooperado.nome if l.cooperado else "",
            l.estabelecimento.nome if l.estabelecimento else "",
            float(l.valor),
            l.descricao or ""
        ])

    for i,w in enumerate([20,16,32,32,16,60], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    bio = BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name="lancamentos.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ========= CRIA BANCO + ADMIN MASTER =========
def criar_banco_e_admin():
    with app.app_context():
        db.create_all()
        if not Admin.query.filter_by(username='coopex').first():
            admin = Admin(nome='Administrador Master', username='coopex')
            admin.set_senha('coopex05289')
            db.session.add(admin); db.session.commit()
            print('Admin criado: coopex / coopex05289')

# ========= MAIN =========
if __name__ == '__main__':
    criar_banco_e_admin()
    app.run(debug=False, host="0.0.0.0")
