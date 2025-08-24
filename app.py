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

# ========= FUSO HORÁRIO =========
from pytz import timezone, utc
TZ_SP = timezone("America/Sao_Paulo")

# ========= APP / CONFIG =========
app = Flask(__name__)
app.secret_key = 'coopex-secreto'

# Corrige scheme/host atrás do proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Sessão/Cookies
app.permanent_session_lifetime = timedelta(hours=10)
app.config.update(
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    TEMPLATES_AUTO_RELOAD=False,
    JSONIFY_PRETTYPRINT_REGULAR=False,
    JSON_SORT_KEYS=False,
)

# Banco
app.config['SQLALCHEMY_DATABASE_URI'] = (
    'postgresql+psycopg://'
    'banco_dados_9ooo_user:4eebYkKJwygTnOzrU1PAMFphnIli4iCH'
    '@dpg-d28sr2juibrs73du5n80-a.oregon-postgres.render.com/banco_dados_9ooo'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_size': 5,
    'max_overflow': 10,
    'pool_timeout': 30,
    'pool_recycle': 1800,
}

# Estáticos
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400
app.config['UPLOAD_FOLDER_COOPERADOS'] = 'static/uploads'
app.config['UPLOAD_FOLDER_LOGOS'] = 'static/logos'
app.config['STATICS_FOLDER'] = 'statics'
os.makedirs(app.config['UPLOAD_FOLDER_COOPERADOS'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER_LOGOS'], exist_ok=True)
os.makedirs(app.config['STATICS_FOLDER'], exist_ok=True)

# Compressão
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
    foto_data = db.Column(db.LargeBinary, nullable=True)
    foto_mimetype = db.Column(db.String(50), nullable=True)
    foto_filename = db.Column(db.String(120), nullable=True)

class Estabelecimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    logo = db.Column(db.String(120), nullable=True)
    def set_senha(self, senha): self.senha_hash = generate_password_hash(senha)
    def checar_senha(self, senha): return check_password_hash(self.senha_hash, senha)

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    def set_senha(self, senha): self.senha_hash = generate_password_hash(senha)
    def checar_senha(self, senha): return check_password_hash(self.senha_hash, senha)

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

# ========= FUNÇÕES DE DATA =========
def format_brasilia(dt: datetime) -> str:
    """Converte UTC → America/Sao_Paulo e retorna string formatada"""
    if not dt: return ""
    dt_brasilia = dt.replace(tzinfo=utc).astimezone(TZ_SP)
    return dt_brasilia.strftime("%d/%m/%Y %H:%M")

# ========= LOGIN/LOGOUT =========
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        tipo = request.form.get('tipo')
        username = request.form.get('username')
        senha = request.form.get('senha')
        session.permanent = True

        if tipo == 'admin':
            user = Admin.query.filter_by(username=username).first()
            if user and user.checar_senha(senha):
                session['user_id'] = user.id
                session['user_tipo'] = 'admin'
                return redirect(url_for('dashboard'))
        elif tipo == 'estabelecimento':
            est = Estabelecimento.query.filter_by(username=username).first()
            if est and est.checar_senha(senha):
                session['user_id'] = est.id
                session['user_tipo'] = 'estabelecimento'
                return redirect(url_for('painel_estabelecimento'))
        flash('Usuário ou senha inválidos', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ========= DASHBOARD =========
@app.route('/')
@app.route('/dashboard')
def dashboard():
    if session.get("user_tipo") != "admin":
        return redirect(url_for('login'))
    admin = Admin.query.get(session['user_id'])
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    estabelecimentos = Estabelecimento.query.order_by(Estabelecimento.nome).all()
    lancamentos = Lancamento.query.order_by(Lancamento.data.desc()).all()

    for l in lancamentos:
        l.data_fmt = format_brasilia(l.data)

    return render_template('dashboard.html',
        admin=admin, cooperados=cooperados,
        estabelecimentos=estabelecimentos, lancamentos=lancamentos)

# ========= PAINEL ESTABELECIMENTO =========
@app.route('/painel_estabelecimento', methods=['GET','POST'])
def painel_estabelecimento():
    if session.get("user_tipo") != "estabelecimento":
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
                v = float(valor)
                if c.credito >= v:
                    l = Lancamento(data=datetime.utcnow(), os_numero=os_numero,
                                   cooperado_id=c.id, estabelecimento_id=est.id,
                                   valor=v, descricao=descricao)
                    db.session.add(l)
                    c.credito -= v
                    db.session.commit()
                    flash('Lançamento realizado!', 'success')
                else:
                    flash('Crédito insuficiente.', 'danger')

    lancamentos = Lancamento.query.filter_by(estabelecimento_id=est.id).order_by(Lancamento.data.desc()).all()
    for l in lancamentos:
        l.data_fmt = format_brasilia(l.data)

    return render_template('painel_estabelecimento.html',
                           est=est, cooperados=cooperados, lancamentos=lancamentos)

# ========= EXPORTAÇÃO =========
@app.route('/lancamentos/exportar')
def exportar_lancamentos():
    if session.get("user_tipo") != "admin":
        return redirect(url_for('login'))
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter

    rows = Lancamento.query.order_by(Lancamento.data.desc()).all()
    wb = Workbook()
    ws = wb.active
    ws.append(["Data", "Nº OS", "Cooperado", "Estabelecimento", "Valor", "Descrição"])
    for l in rows:
        ws.append([format_brasilia(l.data), l.os_numero,
                   l.cooperado.nome if l.cooperado else "",
                   l.estabelecimento.nome if l.estabelecimento else "",
                   float(l.valor), l.descricao or ""])
    for i,w in enumerate([20,16,32,32,16,60], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    bio = BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name="lancamentos.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ========= CRIA BANCO + ADMIN =========
def criar_banco_e_admin():
    with app.app_context():
        db.create_all()
        if not Admin.query.filter_by(username='coopex').first():
            admin = Admin(nome='Administrador Master', username='coopex')
            admin.set_senha('coopex05289')
            db.session.add(admin); db.session.commit()
            print("Admin criado: coopex / coopex05289")

# ========= MAIN =========
if __name__ == "__main__":
    criar_banco_e_admin()
    app.run(debug=False, host="0.0.0.0")
