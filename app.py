from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os

app = Flask(__name__)

app.secret_key = os.getenv('SECRET_KEY', 'coopex-secreto')

db_url = os.getenv('DATABASE_URL')
if db_url and db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql+psycopg://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///coopex.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER_COOPERADOS'] = 'static/uploads'
app.config['UPLOAD_FOLDER_LOGOS'] = 'static/logos'
os.makedirs(app.config['UPLOAD_FOLDER_COOPERADOS'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER_LOGOS'], exist_ok=True)

db = SQLAlchemy(app)

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
        credito = float(request.form['credito'])
        foto_file = request.files.get('foto')
        foto_filename = None
        if foto_file and foto_file.filename:
            foto_filename = secure_filename(f"foto_{username}_{foto_file.filename}")
            foto_file.save(os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], foto_filename))
        if Cooperado.query.filter_by(username=username).first():
            flash('Usuário já existe!', 'danger')
            return redirect(url_for('novo_cooperado'))
        cooperado = Cooperado(nome=nome, username=username, credito=credito, foto=foto_filename)
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
        cooperado.credito = float(request.form['credito'])
        foto_file = request.files.get('foto')
        if foto_file and foto_file.filename:
            foto_filename = secure_filename(f"foto_{cooperado.username}_{foto_file.filename}")
            foto_file.save(os.path.join(app.config['UPLOAD_FOLDER_COOPERADOS'], foto_filename))
            cooperado.foto = foto_filename
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

@app.route('/ajustar_credito', methods=['GET', 'POST'])
def ajustar_credito():
    if not is_admin():
        return redirect(url_for('login'))
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
    lancamentos = query.order_by(Lancamento.data.desc()).all()
    return render_template('lancamentos.html', admin=admin, cooperados=cooperados, estabelecimentos=estabelecimentos, lancamentos=lancamentos, filtros=filtros)

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

def criar_banco_e_admin():
    with app.app_context():
        db.create_all()
        if not Admin.query.filter_by(username='coopex').first():
            admin = Admin(nome='Administrador Master', username='coopex')
            admin.set_senha('coopex05289')
            db.session.add(admin)
            db.session.commit()
            print('Admin criado: coopex / coopex05289')

if __name__ == '__main__':
    criar_banco_e_admin()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
