# app.py
from __future__ import annotations
import os
from io import BytesIO
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    send_file, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, current_user,
    login_required, logout_user
)
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import and_, func, desc, ForeignKey
from sqlalchemy.orm import relationship

# ============================ APP / DB / LOGIN ============================

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "troque_esta_chave")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", "sqlite:///coopex.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

# Fuso
BRT = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")


def utcnow():
    return datetime.now(UTC)


# ============================ MODELOS ============================

class Estabelecimento(db.Model):
    __tablename__ = "estabelecimentos"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    username = db.Column(db.String(120), unique=True, nullable=False)
    logo = db.Column(db.String(255))  # caminho do arquivo opcional
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    lancamentos = relationship("Lancamento", back_populates="estabelecimento")


class Cooperado(db.Model):
    __tablename__ = "cooperados"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    username = db.Column(db.String(120), unique=True, nullable=False)
    credito = db.Column(db.Numeric(12, 2), default=0)
    foto = db.Column(db.String(255))      # caminho arquivo (opcional)
    foto_data = db.Column(db.LargeBinary) # binário (opcional)
    atualizado_em = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lancamentos = relationship("Lancamento", back_populates="cooperado")


class Lancamento(db.Model):
    __tablename__ = "lancamentos"
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)  # UTC
    os_numero = db.Column(db.String(80), nullable=False)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    descricao = db.Column(db.String(255))

    cooperado_id = db.Column(db.Integer, ForeignKey("cooperados.id"), nullable=False)
    estabelecimento_id = db.Column(db.Integer, ForeignKey("estabelecimentos.id"), nullable=False)

    cooperado = relationship("Cooperado", back_populates="lancamentos")
    estabelecimento = relationship("Estabelecimento", back_populates="lancamentos")


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    # 'admin' | 'estabelecimento' | 'cooperado'
    tipo = db.Column(db.String(20), nullable=False, default="admin")
    # vínculo: para estabelecimento/cooperado, aponta para id correspondente
    vinculo_id = db.Column(db.Integer)

    def set_password(self, raw):
        self.senha_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.senha_hash, raw)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============================ HELPERS DE DATA ============================

def br_day_bounds_utc(d: date):
    """
    Início/fim do dia (BRT) convertidos para UTC.
    """
    start_brt = datetime.combine(d, time.min).replace(tzinfo=BRT)
    end_brt = datetime.combine(d, time.max).replace(tzinfo=BRT)
    return start_brt.astimezone(UTC), end_brt.astimezone(UTC)


def br_month_bounds_utc(dt_brt: datetime | None = None):
    """
    Início/fim do mês atual no fuso de Brasília, retornando UTC.
    """
    now_brt = (dt_brt or datetime.now(BRT)).astimezone(BRT)
    first_brt = now_brt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if first_brt.month == 12:
        next_first_brt = first_brt.replace(year=first_brt.year + 1, month=1)
    else:
        next_first_brt = first_brt.replace(month=first_brt.month + 1)
    last_brt = next_first_brt - timedelta(microseconds=1)
    return first_brt.astimezone(UTC), last_brt.astimezone(UTC)


def to_brt_str(dt_aware: datetime) -> str:
    dt_brt = dt_aware.astimezone(BRT)
    return dt_brt.strftime("%d/%m/%Y %H:%M")


def to_brt_iso(dt_aware: datetime) -> str:
    dt_brt = dt_aware.astimezone(BRT)
    return dt_brt.strftime("%Y-%m-%dT%H:%M:%S")


# ============================ ROTAS: LOGIN / LOGOUT ============================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(senha):
            flash("Credenciais inválidas", "danger")
            return render_template("login.html")

        login_user(user)

        # Redireciona automaticamente pelo perfil
        if user.tipo == "admin":
            return redirect(url_for("dashboard"))
        elif user.tipo == "estabelecimento":
            return redirect(url_for("painel_estabelecimento"))
        elif user.tipo == "cooperado":
            return redirect(url_for("painel_cooperado"))
        else:
            return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ============================ ROTAS BASE ============================

@app.route("/")
def home():
    if current_user.is_authenticated:
        if current_user.tipo == "admin":
            return redirect(url_for("dashboard"))
        elif current_user.tipo == "estabelecimento":
            return redirect(url_for("painel_estabelecimento"))
        elif current_user.tipo == "cooperado":
            return redirect(url_for("painel_cooperado"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    # Métricas simples para o admin
    total_pedidos = db.session.query(func.count(Lancamento.id)).scalar() or 0
    total_valor = db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).scalar() or 0
    total_cooperados = Cooperado.query.count()
    total_estabelecimentos = Estabelecimento.query.count()

    # Top cooperados por soma (últimos 90 dias como exemplo)
    start_utc, end_utc = br_month_bounds_utc()
    rows = (
        db.session.query(Cooperado.nome, func.coalesce(func.sum(Lancamento.valor), 0).label("soma"))
        .join(Lancamento, Lancamento.cooperado_id == Cooperado.id)
        .filter(Lancamento.data >= start_utc, Lancamento.data <= end_utc)
        .group_by(Cooperado.id)
        .order_by(desc("soma"))
        .limit(10)
        .all()
    )
    cooperado_nomes = [r[0] for r in rows]
    lancamentos_contagem = [float(r[1]) for r in rows]

    # último lançamento p/ beep
    last = db.session.query(func.max(Lancamento.id)).scalar() or 0

    return render_template(
        "dashboard.html",
        total_pedidos=total_pedidos,
        total_valor=float(total_valor),
        total_cooperados=total_cooperados,
        total_estabelecimentos=total_estabelecimentos,
        cooperado_nomes=cooperado_nomes,
        lancamentos_contagem=lancamentos_contagem,
        ultimo_lancamento_id=last,
        cooperados=Cooperado.query.order_by(Cooperado.nome.asc()).all(),
        estabelecimentos=Estabelecimento.query.order_by(Estabelecimento.nome.asc()).all(),
        filtros={"cooperado_id": "", "estabelecimento_id": "", "data_inicio": "", "data_fim": ""}
    )


@app.route("/painel-estabelecimento")
@login_required
def painel_estabelecimento():
    if current_user.tipo != "estabelecimento":
        return redirect(url_for("dashboard"))

    est = Estabelecimento.query.get(current_user.vinculo_id)
    if not est:
        flash("Estabelecimento não localizado.", "danger")
        return redirect(url_for("dashboard"))

    # Cooperados e seus créditos (para lançar)
    cooperados = Cooperado.query.order_by(Cooperado.nome.asc()).all()

    # Lançamentos desse estabelecimento (mês atual por padrão)
    start_utc, end_utc = br_month_bounds_utc()
    lancs = (
        Lancamento.query
        .filter(
            Lancamento.estabelecimento_id == est.id,
            Lancamento.data >= start_utc,
            Lancamento.data <= end_utc
        )
        .order_by(Lancamento.data.desc())
        .all()
    )

    # Prepara p/ template do parceiro (aquele que você me mandou)
    lancamentos_view = [{
        "id": l.id,
        "os_numero": l.os_numero,
        "valor": float(l.valor),
        "descricao": l.descricao or "",
        "cooperado": l.cooperado,
        "data_brasilia": to_brt_str(l.data),
        "data": l.data.astimezone(BRT)  # se o template usar .isoformat() no Jinja
    } for l in lancs]

    return render_template(
        "painel_estabelecimento.html",
        est=est,
        cooperados=cooperados,
        lancamentos=lancamentos_view
    )


@app.route("/painel-cooperado")
@login_required
def painel_cooperado():
    if current_user.tipo != "cooperado":
        return redirect(url_for("dashboard"))

    coop = Cooperado.query.get(current_user.vinculo_id)
    if not coop:
        flash("Cooperado não localizado.", "danger")
        return redirect(url_for("dashboard"))

    # Seus próprios lançamentos (mês atual)
    start_utc, end_utc = br_month_bounds_utc()
    lancs = (
        Lancamento.query
        .filter(Lancamento.cooperado_id == coop.id,
                Lancamento.data >= start_utc, Lancamento.data <= end_utc)
        .order_by(Lancamento.data.desc())
        .all()
    )

    lancamentos_view = [{
        "id": l.id,
        "os_numero": l.os_numero,
        "valor": float(l.valor),
        "descricao": l.descricao or "",
        "estabelecimento": l.estabelecimento,
        "data_brasilia": to_brt_str(l.data),
        "data": l.data.astimezone(BRT)
    } for l in lancs]

    return render_template(
        "painel_cooperado.html",
        cooperado=coop,
        lancamentos=lancamentos_view
    )


# ============================ LISTAGENS BÁSICAS ============================

@app.route("/cooperados")
@login_required
def listar_cooperados():
    cooperados = Cooperado.query.order_by(Cooperado.nome.asc()).all()
    return render_template("cooperados.html", cooperados=cooperados)


@app.route("/estabelecimentos")
@login_required
def listar_estabelecimentos():
    estabelecimentos = Estabelecimento.query.order_by(Estabelecimento.nome.asc()).all()
    return render_template("estabelecimentos.html", estabelecimentos=estabelecimentos)


# ============================ LANÇAMENTOS (FILTRO COM FUSO) ============================

@app.route("/lancamentos")
@login_required
def listar_lancamentos():
    q = Lancamento.query

    # Se usuário é estabelecimento, filtra automaticamente
    if current_user.tipo == "estabelecimento" and current_user.vinculo_id:
        q = q.filter(Lancamento.estabelecimento_id == current_user.vinculo_id)

    cooperado_id = request.args.get("cooperado_id") or ""
    estabelecimento_id = request.args.get("estabelecimento_id") or ""
    data_inicio = request.args.get("data_inicio") or ""
    data_fim = request.args.get("data_fim") or ""

    # Filtros de relacionamento
    if cooperado_id:
        q = q.filter(Lancamento.cooperado_id == int(cooperado_id))
    if estabelecimento_id and not (current_user.tipo == "estabelecimento"):
        q = q.filter(Lancamento.estabelecimento_id == int(estabelecimento_id))

    # Filtro de datas — sempre interpretando as datas em BRT
    if data_inicio and data_fim:
        try:
            di = date.fromisoformat(data_inicio)
            df = date.fromisoformat(data_fim)
            di_utc, _ = br_day_bounds_utc(di)
            _, df_utc = br_day_bounds_utc(df)
            q = q.filter(and_(Lancamento.data >= di_utc, Lancamento.data <= df_utc))
        except Exception:
            pass
    else:
        # padrão: mês atual (BRT)
        month_start_utc, month_end_utc = br_month_bounds_utc()
        q = q.filter(and_(Lancamento.data >= month_start_utc, Lancamento.data <= month_end_utc))

    q = q.order_by(Lancamento.data.desc())
    lancamentos = q.all()

    # Converte p/ BRT para exibição
    lancamentos_view = []
    for l in lancamentos:
        dt_aware = l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)
        lancamentos_view.append({
            "id": l.id,
            "os_numero": l.os_numero,
            "valor": float(l.valor),
            "descricao": l.descricao or "",
            "cooperado": l.cooperado,
            "estabelecimento": l.estabelecimento,
            "data_fmt": to_brt_str(dt_aware),
            "data_iso": to_brt_iso(dt_aware)
        })

    cooperados = Cooperado.query.order_by(Cooperado.nome.asc()).all()
    estabelecimentos = Estabelecimento.query.order_by(Estabelecimento.nome.asc()).all()

    filtros = dict(
        cooperado_id=cooperado_id,
        estabelecimento_id=estabelecimento_id or (current_user.vinculo_id if current_user.tipo == "estabelecimento" else ""),
        data_inicio=data_inicio,
        data_fim=data_fim
    )

    return render_template(
        "lancamentos.html",
        lancamentos=lancamentos_view,
        cooperados=cooperados,
        estabelecimentos=estabelecimentos,
        filtros=filtros
    )


# ============================ EXPORTAÇÃO EXCEL ============================

@app.route("/exportar-lancamentos")
@login_required
def exportar_lancamentos():
    import pandas as pd

    # mesma lógica de filtros da listagem
    q = Lancamento.query
    if current_user.tipo == "estabelecimento" and current_user.vinculo_id:
        q = q.filter(Lancamento.estabelecimento_id == current_user.vinculo_id)

    cooperado_id = request.args.get("cooperado_id") or ""
    estabelecimento_id = request.args.get("estabelecimento_id") or ""
    data_inicio = request.args.get("data_inicio") or ""
    data_fim = request.args.get("data_fim") or ""

    if cooperado_id:
        q = q.filter(Lancamento.cooperado_id == int(cooperado_id))
    if estabelecimento_id and not (current_user.tipo == "estabelecimento"):
        q = q.filter(Lancamento.estabelecimento_id == int(estabelecimento_id))

    if data_inicio and data_fim:
        try:
            di = date.fromisoformat(data_inicio)
            df = date.fromisoformat(data_fim)
            di_utc, _ = br_day_bounds_utc(di)
            _, df_utc = br_day_bounds_utc(df)
            q = q.filter(and_(Lancamento.data >= di_utc, Lancamento.data <= df_utc))
        except Exception:
            pass
    else:
        month_start_utc, month_end_utc = br_month_bounds_utc()
        q = q.filter(and_(Lancamento.data >= month_start_utc, Lancamento.data <= month_end_utc))

    q = q.order_by(Lancamento.data.desc())
    rows = q.all()

    data = []
    for l in rows:
        dt = l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)
        data.append({
            "Data (Brasília)": to_brt_str(dt),
            "Nº OS": l.os_numero,
            "Cooperado": l.cooperado.nome if l.cooperado else "",
            "Estabelecimento": l.estabelecimento.nome if l.estabelecimento else "",
            "Valor (R$)": float(l.valor),
            "Descrição": l.descricao or ""
        })

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Lancamentos")
        # formatação simples
        ws = writer.sheets["Lancamentos"]
        ws.set_column(0, 0, 20)  # Data
        ws.set_column(1, 1, 14)  # OS
        ws.set_column(2, 3, 28)  # Cooperado/Estabelecimento
        ws.set_column(4, 4, 14)  # Valor
        ws.set_column(5, 5, 40)  # Descrição

    output.seek(0)
    fname = f"lancamentos_{datetime.now(BRT).strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(output, as_attachment=True, download_name=fname, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ============================ APIs auxiliares (dashboard/admin) ============================

@app.route("/api/ultimo-lancamento")
@login_required
def api_ultimo_lancamento():
    last = db.session.query(func.max(Lancamento.id)).scalar() or 0
    return jsonify({"last_id": int(last)})


@app.route("/api/lancamento-info")
@login_required
def api_lancamento_info():
    try:
        lid = int(request.args.get("id", "0"))
    except Exception:
        return jsonify({"ok": False}), 400
    l = Lancamento.query.get(lid)
    if not l:
        return jsonify({"ok": False}), 404
    return jsonify({
        "ok": True,
        "id": l.id,
        "cooperado": l.cooperado.nome if l.cooperado else "",
        "cooperado_nome": l.cooperado.nome if l.cooperado else "",
        "estabelecimento": l.estabelecimento.nome if l.estabelecimento else "",
        "valor": float(l.valor),
        "data": to_brt_str(l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC))
    })


# ============================ UTIL: foto de cooperado / arquivos estáticos extras ============================

@app.route("/foto-cooperado/<int:id>")
@login_required
def foto_cooperado(id: int):
    c = Cooperado.query.get(id)
    if not c:
        return ("", 404)
    # prioridade: binário; depois caminho; senão 404
    if c.foto_data:
        return send_file(BytesIO(c.foto_data), mimetype="image/jpeg")
    if c.foto and os.path.exists(c.foto):
        return send_file(c.foto)
    return ("", 404)


@app.route("/statics/<path:filename>")
def statics_files(filename):
    # ajuste este caminho para onde estão seus mp3/imagens auxiliares
    base = os.path.join(app.root_path, "statics")
    path = os.path.join(base, filename)
    if not os.path.isfile(path):
        return ("", 404)
    return send_file(path)


# ============================ COMANDOS ÚTEIS (DEV) ============================

@app.cli.command("initdb")
def initdb():
    """Cria as tabelas e um admin padrão (EMAIL=admin@coopex, SENHA=123)."""
    db.create_all()

    if not User.query.filter_by(email="admin@coopex").first():
        admin = User(email="admin@coopex", tipo="admin")
        admin.set_password("123")
        db.session.add(admin)

    if not Estabelecimento.query.first():
        e = Estabelecimento(nome="Loja Exemplo", username="loja")
        db.session.add(e)

    if not Cooperado.query.first():
        c = Cooperado(nome="Maria Silva", username="maria", credito=1000)
        db.session.add(c)

    db.session.commit()
    print("Banco inicializado.")


# ============================ MAIN ============================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
