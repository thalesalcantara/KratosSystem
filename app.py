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
    'pool_size': 5,
    'max_overflow': 10,
    'pool_timeout': 30,
    'pool_recycle': 1800,  # 30 min
    # REMOVIDO: connect_args com "statement_cache_size" (incompatível com psycopg3)
}

# Estáticos mais rápidos (cache padrão de 1 dia)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 24 * 60 * 60

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
    # legado (arquivo no disco)
    foto = db.Column(db.String(120), nullable=True)
    # novos campos (foto no banco)
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

# Índices adicionais úteis em filtros/orden.
Index('ix_lancamento_coop_estab_data', Lancamento.cooperado_id, Lancamento.estabelecimento_id, Lancamento.data.desc())

# ========= SCHEMA (adaptação leve) =========
def ensure_schema():
    """Cria colunas de foto no banco se ainda não existirem (sem Alembic)."""
    with app.app_context():
        cols = {r[0] for r in db.session.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'cooperado'"
        )).fetchall()}
        alter_stmts = []
        if 'foto_data' not in cols:
            alter_stmts.append("ADD COLUMN foto_data BYTEA")
        if 'foto_mimetype' not in cols:
            alter_stmts.append("ADD COLUMN foto_mimetype VARCHAR(50)")
        if 'foto_filename' not in cols:
            alter_stmts.append("ADD COLUMN foto_filename VARCHAR(120)")
        if alter_stmts:
            db.session.execute(text("ALTER TABLE cooperado " + ", ".join(alter_stmts)))
            db.session.commit()

_SCHEMA_BOOTED = False
@app.before_request
def _run_schema_once():
    # Uma só vez por worker, rápido.
    global _SCHEMA_BOOTED
    if not _SCHEMA_BOOTED:
        try:
            ensure_schema()
        except Exception:
            pass
        _SCHEMA_BOOTED = True

# ========= HELPERS =========
def is_admin():
    return session.get('user_tipo') == 'admin'

def is_estabelecimento():
    return session.get('user_tipo') == 'estabelecimento'

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d')
    except Exception:
        return None

def _cache_headers(seconds=None, etag_base: str | None = None):
    """Gera cabeçalhos Cache-Control e ETag simples."""
    if seconds is None:
        seconds = int(app.config.get('SEND_FILE_MAX_AGE_DEFAULT', 3600))
    headers = {
        "Cache-Control": f"public, max-age={seconds}, immutable" if seconds >= 86400 else f"public, max-age={seconds}"
    }
    if etag_base:
        etag = hashlib.sha256(etag_base.encode('utf-8')).hexdigest()[:16]
        headers["ETag"] = etag
    return headers

def _response_with_cache(resp: Response, seconds=None, etag_base=None):
    headers = _cache_headers(seconds, etag_base)
    for k, v in headers.items():
        resp.headers[k] = v
    return resp

# ========= CACHE LEVE para /api/ultimo_lancamento =========
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

def _invalidate_last_lanc_cache():
    _LAST_LANC_CACHE["ts"] = 0.0

def _update_last_lanc_cache_with_value(v: int):
    _LAST_LANC_CACHE["value"] = max(_LAST_LANC_CACHE["value"], int(v))
    _LAST_LANC_CACHE["ts"] = time.time()

# ========= ESTÁTICOS =========
@app.route('/statics/<path:filename>')
def statics_files(filename):
    # Em Flask 3/Werkzeug 3, send_from_directory usa get_send_file_max_age por padrão.
    resp = send_from_directory(app.config['STATICS_FOLDER'], filename)
    # Reforça cache explícito (e corrige erro de cache_timeout removido)
    return _response_with_cache(resp, etag_base=f"statics/{filename}")

# ========= HEADERS GERAIS DE PERFORMANCE =========
@app.after_request
def add_perf_headers(resp: Response):
    # Mantém conexões TCP ativas e dá dica de timing
    resp.headers.setdefault("Connection", "keep-alive")
    # Pequena dica de timing (não mede tudo, mas ajuda a depurar)
    resp.headers.setdefault("Server-Timing", "app;desc=\"Coopex-API\"")
    return resp

# ========= LOGIN/LOGOUT =========
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        tipo = request.form.get('tipo', '').strip()
        username = request.form.get('username', '').strip()
        senha = request.form.get('senha', '')
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
        else:
            flash('Selecione o tipo de usuário.', 'danger')
            return render_template('login.html'), 400

        flash('Usuário ou senha inválidos', 'danger')
        return render_template('login.html'), 401

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ========= DASHBOARD (ADMIN) =========
@app.route('/')
@app.route('/dashboard')
def dashboard():
    if not is_admin():
        return redirect(url_for('login'))
    admin = Admin.query.get(session['user_id'])
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    estabelecimentos = Estabelecimento.query.order_by(Estabelecimento.nome).all()

    filtros = {
        'cooperado_id': request.args.get('cooperado_id'),
        'estabelecimento_id': request.args.get('estabelecimento_id'),
        'data_inicio': request.args.get('data_inicio'),
        'data_fim': request.args.get('data_fim')
    }
    coop_id_i = int(filtros['cooperado_id']) if filtros['cooperado_id'] else None
    est_id_i  = int(filtros['estabelecimento_id']) if filtros['estabelecimento_id'] else None
    di = parse_date(filtros['data_inicio'])
    df = parse_date(filtros['data_fim'])

    # Base query com filtros
    base_q = db.session.query(Lancamento)
    if coop_id_i is not None:
        base_q = base_q.filter(Lancamento.cooperado_id == coop_id_i)
    if est_id_i is not None:
        base_q = base_q.filter(Lancamento.estabelecimento_id == est_id_i)
    if di:
        base_q = base_q.filter(Lancamento.data >= di)
    if df:
        base_q = base_q.filter(Lancamento.data <= df)

    # Totais
    total_pedidos = base_q.count()
    sum_q = db.session.query(func.coalesce(func.sum(Lancamento.valor), 0.0))
    if coop_id_i is not None:
        sum_q = sum_q.filter(Lancamento.cooperado_id == coop_id_i)
    if est_id_i is not None:
        sum_q = sum_q.filter(Lancamento.estabelecimento_id == est_id_i)
    if di:
        sum_q = sum_q.filter(Lancamento.data >= di)
    if df:
        sum_q = sum_q.filter(Lancamento.data <= df)
    total_valor = (sum_q.scalar() or 0.0)

    # Gráfico por cooperado com OUTER JOIN + GROUP BY (mantido)
    sum_per_coop = db.session.query(
        Cooperado.id,
        Cooperado.nome,
        func.coalesce(func.sum(Lancamento.valor), 0.0).label('total')
    ).outerjoin(
        Lancamento, Cooperado.id == Lancamento.cooperado_id
    )

    if coop_id_i is not None:
        sum_per_coop = sum_per_coop.filter(Cooperado.id == coop_id_i)
    if est_id_i is not None:
        sum_per_coop = sum_per_coop.filter((Lancamento.estabelecimento_id == est_id_i) | (Lancamento.id.is_(None)))
    if di:
        sum_per_coop = sum_per_coop.filter((Lancamento.data >= di) | (Lancamento.id.is_(None)))
    if df:
        sum_per_coop = sum_per_coop.filter((Lancamento.data <= df) | (Lancamento.id.is_(None)))

    sum_per_coop = sum_per_coop.group_by(Cooperado.id, Cooperado.nome).order_by(Cooperado.nome).all()

    cooperado_nomes = [row.nome for row in sum_per_coop] or ["Nenhum cooperado"]
    cooperado_valores = [float(row.total) for row in sum_per_coop] or [0.0]

    # ID global do último lançamento
    try:
        ultimo_lancamento_id, _ = _get_cached_last_lanc_id()
    except Exception:
        ultimo_lancamento_id = 0

    return render_template('dashboard.html',
        admin=admin,
        cooperados=cooperados,
        estabelecimentos=estabelecimentos,
        total_pedidos=total_pedidos,
        total_valor=total_valor,
        total_cooperados=len(cooperados),
        total_estabelecimentos=len(estabelecimentos),
        cooperado_nomes=cooperado_nomes,
        cooperado_valores=cooperado_valores,
        lancamentos_contagem=cooperado_valores,
        filtros=filtros,
        ultimo_lancamento_id=ultimo_lancamento_id
    )

@app.route('/painel_admin')
def painel_admin():
    return redirect(url_for('dashboard'))

# ========= APIs para o Dashboard detectar novos lançamentos =========
@app.get('/api/ultimo_lancamento')
def api_ultimo_lancamento():
    """Retorna o maior ID de lançamento na base (com cache leve para reduzir carga)."""
    last_id, cached = _get_cached_last_lanc_id()
    resp = jsonify({"last_id": int(last_id)})
    # Ajuda navegador/proxy a reusar por 2s
    resp.headers['Cache-Control'] = 'public, max-age=2'
    if cached:
        resp.headers['X-Cache-Hit'] = '1'
    return resp

@app.get('/api/lancamento_info')
def api_lancamento_info():
    """Retorna dados básicos do lançamento para balão/tooltip no dashboard."""
    lanc_id = request.args.get('id', type=int)
    if not lanc_id:
        return jsonify({"error": "id requerido"}), 400
    l = Lancamento.query.get_or_404(lanc_id)
    nome = l.cooperado.nome if l.cooperado else ""
    return jsonify({
        "id": l.id,
        "cooperado": nome,
        "valor": float(l.valor),
        "os_numero": l.os_numero
    })

# ========= COOPERADOS CRUD =========
@app.route('/listar_cooperados')
def listar_cooperados():
    if not is_admin():
        return redirect(url_for('login'))
    admin = Admin.query.get(session['user_id'])
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    return render_template('cooperados.html', admin=admin, cooperados=cooperados)

@app.route('/cooperados/novo', methods=['GET', 'POST'])
def novo_cooperado():
    if not is_admin():
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        username = request.form['username']
        credito = float(request.form.get('credito', 0) or 0)
        foto_file = request.files.get('foto')

        foto_filename = None
        foto_data = None
        foto_mimetype = None
        if foto_file and foto_file.filename:
            foto_filename = secure_filename(f"foto_{username}_{foto_file.filename}")
            foto_file.stream.seek(0)
            raw = foto_file.read()
            foto_data = raw
            foto_mimetype = foto_file.mimetype
            # opcional: salva também no disco (fallback)
            try:
                with open(os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], foto_filename), 'wb') as f:
                    f.write(raw)
            except Exception:
                pass

        if Cooperado.query.filter_by(username=username).first():
            flash('Usuário já existe!', 'danger')
            return redirect(url_for('novo_cooperado'))

        cooperado = Cooperado(
            nome=nome, username=username, credito=credito,
            foto=foto_filename, foto_data=foto_data,
            foto_mimetype=foto_mimetype, foto_filename=foto_filename
        )
        db.session.add(cooperado)
        db.session.commit()
        flash('Cooperado cadastrado!', 'success')
        return redirect(url_for('listar_cooperados'))
    return render_template('cooperado_form.html', editar=False, cooperado=None)

@app.route('/cooperados/editar/<int:id>', methods=['GET', 'POST'])
def editar_cooperado(id):
    if not is_admin():
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(id)
    if request.method == 'POST':
        cooperado.nome = request.form['nome']
        cooperado.credito = float(request.form.get('credito', cooperado.credito) or cooperado.credito)

        foto_file = request.files.get('foto')
        if foto_file and foto_file.filename:
            foto_filename = secure_filename(f"foto_{cooperado.username}_{foto_file.filename}")
            foto_file.stream.seek(0)
            raw = foto_file.read()
            cooperado.foto = foto_filename
            cooperado.foto_filename = foto_filename
            cooperado.foto_data = raw
            cooperado.foto_mimetype = foto_file.mimetype
            try:
                with open(os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], foto_filename), 'wb') as f:
                    f.write(raw)
            except Exception:
                pass

        db.session.commit()
        flash('Cooperado alterado!', 'success')
        return redirect(url_for('listar_cooperados'))
    return render_template('cooperado_form.html', editar=True, cooperado=cooperado)

@app.route('/cooperados/excluir/<int:id>')
def excluir_cooperado(id):
    if not is_admin():
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(id)
    db.session.delete(cooperado)
    db.session.commit()
    flash('Cooperado excluído!', 'success')
    return redirect(url_for('listar_cooperados'))

# Serve foto do cooperado (prioriza banco; fallback no disco) com cache correto
@app.route('/cooperados/foto/<int:id>')
def foto_cooperado(id):
    c = Cooperado.query.get_or_404(id)
    cache_sec = int(app.config.get('SEND_FILE_MAX_AGE_DEFAULT', 86400))

    if c.foto_data:
        bio = BytesIO(c.foto_data)
        resp = send_file(
            bio,
            mimetype=c.foto_mimetype or 'image/jpeg',
            download_name=c.foto_filename or f'cooperado_{id}.jpg'
        )
        return _response_with_cache(resp, cache_sec, etag_base=f"cooperado_db_{id}_{len(c.foto_data)}")

    if c.foto:
        path = os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], c.foto)
        if os.path.exists(path):
            resp = send_file(path, mimetype='image/jpeg')
            try:
                size = os.path.getsize(path)
            except Exception:
                size = 0
            return _response_with_cache(resp, cache_sec, etag_base=f"cooperado_fs_{id}_{size}")

    # vazio
    resp = send_file(BytesIO(b''), mimetype='image/jpeg')
    return _response_with_cache(resp, cache_sec, etag_base=f"cooperado_empty_{id}")

# ========= AJUSTAR CRÉDITO =========
@app.route('/ajustar_credito', methods=['GET', 'POST'])
def ajustar_credito():
    if not is_admin():
        return redirect(url_for('login'))
    cooperado_id = request.args.get('id')
    if cooperado_id:
        return ajustar_credito_individual(int(cooperado_id))

    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    if request.method == 'POST':
        cooperado_id = request.form.get('cooperado_id')
        novo_credito = request.form.get('credito')
        if cooperado_id and novo_credito is not None:
            c = Cooperado.query.get(int(cooperado_id))
            if c:
                c.credito = float(novo_credito)
                db.session.commit()
                flash('Crédito ajustado!', 'success')
                return redirect(url_for('ajustar_credito'))
            else:
                flash('Cooperado não encontrado!', 'danger')
        else:
            flash('Selecione um cooperado e valor.', 'danger')
    return render_template('ajustar_credito.html', cooperados=cooperados)

@app.route('/ajustar_credito/<int:id>', methods=['GET', 'POST'])
def ajustar_credito_individual(id):
    if not is_admin():
        return redirect(url_for('login'))
    cooperado = Cooperado.query.get_or_404(id)
    if request.method == 'POST':
        novo_credito = request.form.get('credito')
        if novo_credito is not None:
            cooperado.credito = float(novo_credito)
            db.session.commit()
            flash('Crédito ajustado!', 'success')
            return redirect(url_for('listar_cooperados'))
    return render_template('ajustar_credito.html', cooperado=cooperado)

# ========= ESTABELECIMENTOS CRUD =========
@app.route('/listar_estabelecimentos')
def listar_estabelecimentos():
    if not is_admin():
        return redirect(url_for('login'))
    admin = Admin.query.get(session['user_id'])
    estabelecimentos = Estabelecimento.query.order_by(Estabelecimento.nome).all()
    return render_template('estabelecimentos.html', admin=admin, estabelecimentos=estabelecimentos)

@app.route('/novo_estabelecimento', methods=['GET', 'POST'])
def novo_estabelecimento():
    if not is_admin():
        return redirect(url_for('login'))
    if request.method == 'POST':
        nome = request.form['nome']
        username = request.form['username']
        senha = request.form['senha']
        logo_file = request.files.get('logo')
        filename = None
        if logo_file and logo_file.filename:
            filename = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(app.config['UPLOAD_FOLDER_LOGOS'], filename))
        if Estabelecimento.query.filter_by(username=username).first():
            flash('Usuário já existe!', 'danger')
            return redirect(url_for('novo_estabelecimento'))
        est = Estabelecimento(nome=nome, username=username, logo=filename)
        est.set_senha(senha)
        db.session.add(est)
        db.session.commit()
        flash('Estabelecimento cadastrado!', 'success')
        return redirect(url_for('listar_estabelecimentos'))
    return render_template('estabelecimento_form.html', editar=False, estabelecimento=None)

@app.route('/editar_estabelecimento/<int:id>', methods=['GET', 'POST'])
def editar_estabelecimento(id):
    if not is_admin():
        return redirect(url_for('login'))
    est = Estabelecimento.query.get_or_404(id)
    if request.method == 'POST':
        est.nome = request.form['nome']
        if request.form['senha']:
            est.set_senha(request.form['senha'])
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename:
            filename = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(app.config['UPLOAD_FOLDER_LOGOS'], filename))
            est.logo = filename
        db.session.commit()
        flash('Estabelecimento alterado!', 'success')
        return redirect(url_for('listar_estabelecimentos'))
    return render_template('estabelecimento_form.html', editar=True, estabelecimento=est)

@app.route('/excluir_estabelecimento/<int:id>')
def excluir_estabelecimento(id):
    if not is_admin():
        return redirect(url_for('login'))
    est = Estabelecimento.query.get_or_404(id)
    db.session.delete(est)
    db.session.commit()
    flash('Estabelecimento excluído!', 'success')
    return redirect(url_for('listar_estabelecimentos'))

# ========= LANÇAMENTOS (ADMIN) =========
@app.route('/lancamentos')
def listar_lancamentos():
    if not is_admin():
        return redirect(url_for('login'))
    admin = Admin.query.get(session['user_id'])
    cooperados = Cooperado.query.order_by(Cooperado.nome).all()
    estabelecimentos = Estabelecimento.query.order_by(Estabelecimento.nome).all()
    filtros = {
        'cooperado_id': request.args.get('cooperado_id'),
        'estabelecimento_id': request.args.get('estabelecimento_id'),
        'data_inicio': request.args.get('data_inicio'),
        'data_fim': request.args.get('data_fim')
    }
    coop_id_i = int(filtros['cooperado_id']) if filtros['cooperado_id'] else None
    est_id_i  = int(filtros['estabelecimento_id']) if filtros['estabelecimento_id'] else None
    di = parse_date(filtros['data_inicio'])
    df = parse_date(filtros['data_fim'])

    query = Lancamento.query
    if coop_id_i is not None:
        query = query.filter(Lancamento.cooperado_id == coop_id_i)
    if est_id_i is not None:
        query = query.filter(Lancamento.estabelecimento_id == est_id_i)
    if di:
        query = query.filter(Lancamento.data >= di)
    if df:
        query = query.filter(Lancamento.data <= df)

    lancamentos = query.order_by(Lancamento.data.desc()).all()
    return render_template('lancamentos.html',
                           admin=admin,
                           cooperados=cooperados,
                           estabelecimentos=estabelecimentos,
                           lancamentos=lancamentos,
                           filtros=filtros)

@app.route('/lancamentos/exportar')
def exportar_lancamentos():
    if not is_admin():
        return redirect(url_for('login'))

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        return ("Para exportar, inclua 'openpyxl>=3.1.2' no requirements.txt e redeploy."), 500

    coop_id = request.args.get('cooperado_id')
    est_id = request.args.get('estabelecimento_id')
    di_s = request.args.get('data_inicio')
    df_s = request.args.get('data_fim')

    coop_id_i = int(coop_id) if coop_id else None
    est_id_i  = int(est_id) if est_id else None
    di = parse_date(di_s)
    df = parse_date(df_s)

    q = Lancamento.query
    if coop_id_i is not None:
        q = q.filter(Lancamento.cooperado_id == coop_id_i)
    if est_id_i is not None:
        q = q.filter(Lancamento.estabelecimento_id == est_id_i)
    if di:
        q = q.filter(Lancamento.data >= di)
    if df:
        q = q.filter(Lancamento.data <= df)
    q = q.order_by(Lancamento.data.desc())

    rows = q.all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Lançamentos"

    header = ["Data", "Nº OS", "Cooperado", "Estabelecimento", "Valor (R$)", "Descrição"]
    ws.append(header)
    for col_idx, h in enumerate(header, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for l in rows:
        coop_nome = l.cooperado.nome if l.cooperado else ""
        est_nome = l.estabelecimento.nome if l.estabelecimento else ""
        data_fmt = l.data.strftime('%d/%m/%Y %H:%M')
        ws.append([data_fmt, l.os_numero, coop_nome, est_nome, float(l.valor), l.descricao or ""])

    widths = [20, 16, 32, 32, 16, 60]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=5).number_format = u'"R$" #,##0.00'

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    resp = send_file(
        bio,
        as_attachment=True,
        download_name="lancamentos.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return _response_with_cache(resp, 0, etag_base=f"xlsx_{len(rows)}")

# ========= PAINEL ESTABELECIMENTO =========
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

                # DEBITO: lançamento diminui o crédito do cooperado
                novo_credito = c.credito - valor_f
                if novo_credito < 0:
                    flash('Crédito insuficiente para este lançamento.', 'danger')
                else:
                    l = Lancamento(
                        data=datetime.utcnow(),
                        os_numero=os_numero,
                        cooperado_id=c.id,
                        estabelecimento_id=est.id,
                        valor=valor_f,
                        descricao=descricao
                    )
                    db.session.add(l)
                    c.credito = novo_credito
                    db.session.commit()
                    _update_last_lanc_cache_with_value(l.id)
                    flash('Lançamento realizado com sucesso!', 'success')
            else:
                flash('Cooperado não encontrado!', 'danger')
        else:
            flash('Preencha todos os campos obrigatórios!', 'danger')

    lancamentos = Lancamento.query.filter_by(estabelecimento_id=est.id).order_by(Lancamento.data.desc()).all()

    # Timezone conversion (import uma vez só aqui)
    try:
        from pytz import timezone, utc
        tz_sp = timezone('America/Sao_Paulo')
        for l in lancamentos:
            hora_brasilia = l.data.replace(tzinfo=utc).astimezone(tz_sp)
            l.data_brasilia = hora_brasilia.strftime('%d/%m/%Y %H:%M')
    except Exception:
        for l in lancamentos:
            l.data_brasilia = l.data.strftime('%d/%m/%Y %H:%M')

    return render_template('painel_estabelecimento.html', est=est, cooperados=cooperados, lancamentos=lancamentos)

# ========= ESTAB: EDITAR / EXCLUIR LANÇAMENTO (10h de janela) =========
@app.route('/estab/lancamento/editar/<int:id>', methods=['POST'])
def estab_editar_lancamento(id):
    if not is_estabelecimento():
        return redirect(url_for('login'))

    l = Lancamento.query.get_or_404(id)

    # segurança: só o estabelecimento que criou pode editar
    if l.estabelecimento_id != session.get('user_id'):
        flash('Você não tem permissão para editar este lançamento.', 'danger')
        return redirect(url_for('painel_estabelecimento'))

    if datetime.utcnow() - l.data > timedelta(hours=10):
        flash('Edição permitida somente até 10 horas após a criação.', 'warning')
        return redirect(url_for('painel_estabelecimento'))

    os_numero = request.form.get('os_numero', '').strip()
    valor_str = request.form.get('valor', '').strip().replace(',', '.')
    descricao = request.form.get('descricao', '').strip()

    try:
        novo_valor = float(valor_str)
        if novo_valor <= 0:
            raise ValueError()
    except Exception:
        flash('Valor inválido.', 'danger')
        return redirect(url_for('painel_estabelecimento'))

    if not os_numero:
        flash('O número da OS é obrigatório.', 'danger')
        return redirect(url_for('painel_estabelecimento'))

    cooperado = Cooperado.query.get(l.cooperado_id)
    if not cooperado:
        flash('Cooperado não encontrado.', 'danger')
        return redirect(url_for('painel_estabelecimento'))

    valor_antigo = l.valor
    delta = novo_valor - valor_antigo

    if delta > 0 and cooperado.credito < delta:
        flash('Crédito insuficiente para aumentar o valor deste lançamento.', 'danger')
        return redirect(url_for('painel_estabelecimento'))

    l.os_numero = os_numero
    l.valor = novo_valor
    l.descricao = descricao if descricao else None

    cooperado.credito -= delta
    db.session.commit()
    _invalidate_last_lanc_cache()
    flash('Lançamento editado com sucesso!', 'success')
    return redirect(url_for('painel_estabelecimento'))

@app.route('/estab/lancamento/excluir/<int:id>', methods=['POST'])
def estab_excluir_lancamento(id):
    if not is_estabelecimento():
        return redirect(url_for('login'))

    l = Lancamento.query.get_or_404(id)

    if l.estabelecimento_id != session.get('user_id'):
        flash('Você não tem permissão para excluir este lançamento.', 'danger')
        return redirect(url_for('painel_estabelecimento'))

    if datetime.utcnow() - l.data > timedelta(hours=10):
        flash('Exclusão permitida somente até 10 hora após a criação.', 'warning')
        return redirect(url_for('painel_estabelecimento'))

    cooperado = Cooperado.query.get(l.cooperado_id)
    if not cooperado:
        flash('Cooperado não encontrado!', 'danger')
        return redirect(url_for('painel_estabelecimento'))

    cooperado.credito += l.valor

    db.session.delete(l)
    db.session.commit()
    _invalidate_last_lanc_cache()
    flash('Lançamento excluído e crédito devolvido ao cooperado.', 'success')
    return redirect(url_for('painel_estabelecimento'))

# ========= CRIA BANCO + ADMIN MASTER =========
def criar_banco_e_admin():
    with app.app_context():
        db.create_all()
        ensure_schema()
        if not Admin.query.filter_by(username='coopex').first():
            admin = Admin(nome='Administrador Master', username='coopex')
            admin.set_senha('coopex05289')
            db.session.add(admin)
            db.session.commit()
            print('Admin criado: coopex / coopex05289')

# ========= MAIN =========
if __name__ == '__main__':
    criar_banco_e_admin()
    # Em produção (Render) você usa gunicorn. Aqui é dev/standalone:
    app.run(debug=False, host="0.0.0.0")
