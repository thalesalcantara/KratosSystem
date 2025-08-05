from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = 'coopex-secreto'

# BANCO DE DADOS RENDER (PostgreSQL) - USE O DRIVER psycopg (novo)
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
    foto = db.Column(db.String(120), nullable=True)

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

def is_admin():
    return session.get('user_tipo') == 'admin'
def is_estabelecimento():
    return session.get('user_tipo') == 'estabelecimento'

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

@app.route('/painel_admin')
def painel_admin():
    return redirect(url_for('dashboard'))

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
    query = Lancamento.query
    if filtros['cooperado_id']:
        query = query.filter_by(cooperado_id=filtros['cooperado_id'])
    if filtros['estabelecimento_id']:
        query = query.filter_by(estabelecimento_id=filtros['estabelecimento_id'])
    if filtros['data_inicio']:
        try:
            data_inicio = datetime.strptime(filtros['data_inicio'], '%Y-%m-%d')
            query = query.filter(Lancamento.data >= data_inicio)
        except: pass
    if filtros['data_fim']:
        try:
            data_fim = datetime.strptime(filtros['data_fim'], '%Y-%m-%d')
            query = query.filter(Lancamento.data <= data_fim)
        except: pass
    lancamentos = query.all()
    total_pedidos = len(lancamentos)
    total_valor = sum(l.valor for l in lancamentos)
    total_cooperados = len(cooperados)
    total_estabelecimentos = len(estabelecimentos)
    cooperado_nomes = [c.nome for c in cooperados]
    cooperado_valores = []
    for c in cooperados:
        valor = sum(l.valor for l in lancamentos if l.cooperado_id == c.id)
        cooperado_valores.append(valor)
    if not cooperado_valores:
        cooperado_valores = [0]
        cooperado_nomes = ["Nenhum cooperado"]
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

# ---------- ROTAS MÍNIMAS PARA O DASHBOARD NÃO QUEBRAR ----------
@app.route('/listar_cooperados')
def listar_cooperados():
    return "Página de Cooperados (em breve)"

@app.route('/novo_cooperado')
def novo_cooperado():
    return "Cadastrar novo cooperado (em breve)"

@app.route('/listar_estabelecimentos')
def listar_estabelecimentos():
    return "Página de Estabelecimentos (em breve)"

@app.route('/novo_estabelecimento')
def novo_estabelecimento():
    return "Cadastrar novo estabelecimento (em breve)"

@app.route('/listar_lancamentos')
def listar_lancamentos():
    return "Página de Lançamentos (em breve)"

@app.route('/novo_lancamento')
def novo_lancamento():
    return "Cadastrar novo lançamento (em breve)"

@app.route('/painel_estabelecimento')
def painel_estabelecimento():
    return "Painel do Estabelecimento (em breve)"

# ---------- ROTA TEMPORÁRIA PARA CRIAR O BANCO E O ADMIN MASTER ----------
@app.route('/initdb')
def initdb():
    db.create_all()
    if not Admin.query.filter_by(username='coopex').first():
        admin = Admin(nome='Administrador Master', username='coopex')
        admin.set_senha('coopex05289')
        db.session.add(admin)
        db.session.commit()
        return 'Banco criado e admin master (coopex/coopex05289) criado com sucesso!'
    else:
        return 'Banco já existe e admin master já criado.'
# -------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=False, host="0.0.0.0")
