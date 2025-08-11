from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from io import BytesIO
from sqlalchemy import text
import os

# tenta importar openpyxl sem derrubar o app no deploy
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except Exception:
    HAS_OPENPYXL = False

app = Flask(__name__)
app.secret_key = 'coopex-secreto'

# Configuração do PostgreSQL (Render)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql+psycopg://banco_dados_9ooo_user:4eebYkKJwygTnOzrU1PAMFphnIli4iCH@dpg-d28sr2juibrs73du5n80-a.oregon-postgres.render.com/banco_dados_9ooo'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Pastas de upload
app.config['UPLOAD_FOLDER_COOPERADOS'] = 'static/uploads'
app.config['UPLOAD_FOLDER_LOGOS'] = 'static/logos'
os.makedirs(app.config['UPLOAD_FOLDER_COOPERADOS'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER_LOGOS'], exist_ok=True)

db = SQLAlchemy(app)

# MODELS
class Cooperado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    credito = db.Column(db.Float, default=0)
    # legado: caminho do arquivo (mantido)
    foto = db.Column(db.String(120), nullable=True)
    # novos campos: foto no banco (BYTEA)
    foto_data = db.Column(db.LargeBinary, nullable=True)
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
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    os_numero = db.Column(db.String(50), nullable=False)
    cooperado_id = db.Column(db.Integer, db.ForeignKey('cooperado.id'), nullable=False)
    estabelecimento_id = db.Column(db.Integer, db.ForeignKey('estabelecimento.id'), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(250))
    cooperado = db.relationship('Cooperado')
    estabelecimento = db.relationship('Estabelecimento')

# ---- AJUSTE DE SCHEMA (sem Alembic): cria colunas de foto se faltarem ----
def ensure_schema():
    with app.app_context():
        cols = {r[0] for r in db.session.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'cooperado'"
        )).fetchall()}
        alter = []
        if 'foto_data' not in cols: alter.append("ADD COLUMN foto_data BYTEA")
        if 'foto_mimetype' not in cols: alter.append("ADD COLUMN foto_mimetype VARCHAR(50)")
        if 'foto_filename' not in cols: alter.append("ADD COLUMN foto_filename VARCHAR(120)")
        if alter:
            db.session.execute(text("ALTER TABLE cooperado " + ", ".join(alter)))
            db.session.commit()

# ---- FUNÇÕES AUXILIARES ----
def is_admin():
    return session.get('user_tipo') == 'admin'
def is_estabelecimento():
    return session.get('user_tipo') == 'estabelecimento'

def parse_date(s):
    if not s: return None
    try:
        return datetime.strptime(s, '%Y-%m-%d')
    except:
        return None

# ---- LOGIN/LOGOUT ----
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        tipo = request.form['tipo']
        username = request.form['username']
        senha = request.form['senha']
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

# ---- DASHBOARD (ADMIN) ----
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
    # usa ints na query (evita ::VARCHAR)
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

    lancamentos = query.all()
    total_pedidos = len(lancamentos)
    total_valor = sum(l.valor for l in lancamentos)
    total_cooperados = len(cooperados)
    total_estabelecimentos = len(estabelecimentos)
    cooperado_nomes = [c.nome for c in cooperados]
    cooperado_valores = [sum(l.valor for l in lancamentos if l.cooperado_id == c.id) for c in cooperados]
    if not cooperado_valores:
        cooperado_valores = [0]; cooperado_nomes = ["Nenhum cooperado"]
    return render_template('dashboard.html',
        admin=admin,
        cooperados=cooperados,
        estabelecimentos=estabelecimentos,
        total_pedidos=total_pedidos,
        total_valor=total_valor,
        total_cooperados=total_cooperados,
        total_estabelecimentos=total_estabelecimentos,
        cooperado_nomes=cooperado_nomes,
        cooperado_valores=cooperado_valores,
        lancamentos_contagem=cooperado_valores,
        filtros=filtros
    )

@app.route('/painel_admin')
def painel_admin():
    return redirect(url_for('dashboard'))

# ---- COOPERADOS CRUD ----
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
        foto_filename = None; foto_data = None; foto_mimetype = None
        if foto_file and foto_file.filename:
            foto_filename = secure_filename(f"foto_{username}_{foto_file.filename}")
            foto_file.stream.seek(0); raw = foto_file.read()
            foto_data = raw; foto_mimetype = foto_file.mimetype
            # opcional: ainda salva no disco (legado)
            try:
                with open(os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], foto_filename), 'wb') as f:
                    f.write(raw)
            except:
                pass
        if Cooperado.query.filter_by(username=username).first():
            flash('Usuário já existe!', 'danger'); return redirect(url_for('novo_cooperado'))
        cooperado = Cooperado(
            nome=nome, username=username, credito=credito,
            foto=foto_filename, foto_data=foto_data,
            foto_mimetype=foto_mimetype, foto_filename=foto_filename
        )
        db.session.add(cooperado); db.session.commit()
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
            foto_file.stream.seek(0); raw = foto_file.read()
            cooperado.foto = foto_filename
            cooperado.foto_filename = foto_filename
            cooperado.foto_data = raw
            cooperado.foto_mimetype = foto_file.mimetype
            try:
                with open(os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], foto_filename), 'wb') as f:
                    f.write(raw)
            except:
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
    db.session.delete(cooperado); db.session.commit()
    flash('Cooperado excluído!', 'success')
    return redirect(url_for('listar_cooperados'))

# ---- SERVE FOTO DO COOPERADO (do banco) ----
@app.route('/cooperados/foto/<int:id>')
def foto_cooperado(id):
    c = Cooperado.query.get_or_404(id)
    if c.foto_data:
        return send_file(BytesIO(c.foto_data), mimetype=c.foto_mimetype or 'image/jpeg',
                         download_name=c.foto_filename or f'cooperado_{id}.jpg')
    if c.foto:
        path = os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], c.foto)
        if os.path.exists(path):
            return send_file(path, mimetype='image/jpeg')
    return send_file(BytesIO(b''), mimetype='image/jpeg')

# ---- ESTABELECIMENTOS CRUD ----
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
        db.session.add(est); db.session.commit()
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
    db.session.delete(est); db.session.commit()
    flash('Estabelecimento excluído!', 'success')
    return redirect(url_for('listar_estabelecimentos'))

# ---- LANÇAMENTOS (listar e exportar) ----
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
    return render_template('lancamentos.html', admin=admin, cooperados=cooperados, estabelecimentos=estabelecimentos, lancamentos=lancamentos, filtros=filtros)

@app.route('/lancamentos/exportar')
def exportar_lancamentos():
    if not is_admin():
        return redirect(url_for('login'))

    # garante openpyxl (sem derrubar o app se faltou no deploy)
    global HAS_OPENPYXL
    if not HAS_OPENPYXL:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment
            from openpyxl.utils import get_column_letter
            HAS_OPENPYXL = True
        except Exception:
            flash('Exportação indisponível: adicione "openpyxl>=3.1.2" ao requirements.txt e redeploy.', 'danger')
            return redirect(url_for('listar_lancamentos'))

    coop_id = request.args.get('cooperado_id')
    est_id = request.args.get('estabelecimento_id')
    di_s = request.args.get('data_inicio')
    df_s = request.args.get('data_fim')

    coop_id_i = int(coop_id) if coop_id else None
    est_id_i  = int(est_id) if est_id else None
    di = parse_date(di_s); df = parse_date(df_s)

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

    # Monta planilha
    wb = Workbook(); ws = wb.active; ws.title = "Lançamentos"
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

    bio = BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(
        bio, as_attachment=True, download_name="lancamentos.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ---- PAINEL ESTABELECIMENTO ----
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
                l = Lancamento(
                    data=datetime.utcnow(),
                    os_numero=os_numero,
                    cooperado_id=c.id,
                    estabelecimento_id=est.id,
                    valor=float(valor),
                    descricao=descricao
                )
                db.session.add(l)
                c.credito += float(valor)
                db.session.commit()
                flash('Lançamento realizado com sucesso!', 'success')
            else:
                flash('Cooperado não encontrado!', 'danger')
        else:
            flash('Preencha todos os campos obrigatórios!', 'danger')
    lancamentos = Lancamento.query.filter_by(estabelecimento_id=est.id).order_by(Lancamento.data.desc()).all()
    for l in lancamentos:
        hora_brasilia = l.data
        try:
            from pytz import timezone, utc
            hora_brasilia = l.data.replace(tzinfo=utc).astimezone(timezone('America/Sao_Paulo'))
        except:
            pass
        l.data_brasilia = hora_brasilia.strftime('%d/%m/%Y %H:%M')
    return render_template('painel_estabelecimento.html', est=est, cooperados=cooperados, lancamentos=lancamentos)

# ---- CRIA BANCO, AJUSTA SCHEMA E ADMIN MASTER AUTOMÁTICO ----
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

if __name__ == '__main__':
    criar_banco_e_admin()
    app.run(debug=False, host="0.0.0.0")
