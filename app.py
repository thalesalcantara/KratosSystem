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
from sqlalchemy import and_, or_, func, desc, ForeignKey, inspect
from sqlalchemy.orm import relationship

# ============================ APP / DB / LOGIN ============================

def _normalize_db_url(url: str) -> str:
    # Render costuma dar postgres://; SQLAlchemy 2 prefere postgresql+psycopg://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "troque_esta_chave")

_default_sqlite = "sqlite:///coopex.db"
db_url = _normalize_db_url(os.getenv("DATABASE_URL", _default_sqlite))
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

# Fuso
BRT = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")


def utcnow():
    return datetime.now(UTC)


# ===== Jinja: globais para evitar erro de template (callable/now) =====
@app.context_processor
def inject_template_globals():
    return {
        "year": datetime.now(BRT).year,
    }

app.jinja_env.globals["now"] = lambda: datetime.now(BRT)
app.jinja_env.globals["callable"] = callable


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
    # Armazenamento de foto no banco:
    foto = db.Column(db.String(255))      # opcional (caminho antigo, se existir)
    foto_data = db.Column(db.LargeBinary) # binário da imagem (recomendado)
    atualizado_em = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lancamentos = relationship("Lancamento", back_populates="cooperado")


class Lancamento(db.Model):
    __tablename__ = "lancamentos"
    id = db.Column(db.Integer, primary_key=True)
    # Guarde sempre em UTC. Registros antigos podem estar "naive".
    data = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
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

def br_day_bounds(d: date):
    """Início/fim do dia em BRT (timezone-aware) + versões naive (BRT)."""
    start_brt_aw = datetime.combine(d, time.min).replace(tzinfo=BRT)
    end_brt_aw = datetime.combine(d, time.max).replace(tzinfo=BRT)
    start_brt_naive = start_brt_aw.replace(tzinfo=None)
    end_brt_naive = end_brt_aw.replace(tzinfo=None)
    return start_brt_aw, end_brt_aw, start_brt_naive, end_brt_naive


def br_day_bounds_dual_utc_and_naive(d: date):
    """Retorna (start_utc, end_utc, start_brt_naive, end_brt_naive)."""
    start_brt_aw, end_brt_aw, start_brt_naive, end_brt_naive = br_day_bounds(d)
    return start_brt_aw.astimezone(UTC), end_brt_aw.astimezone(UTC), start_brt_naive, end_brt_naive


def br_month_bounds(dt_brt: datetime | None = None):
    """Início/fim do mês atual em BRT (aware) + naive."""
    now_brt = (dt_brt or datetime.now(BRT)).astimezone(BRT)
    first_brt_aw = now_brt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if first_brt_aw.month == 12:
        next_first_brt_aw = first_brt_aw.replace(year=first_brt_aw.year + 1, month=1)
    else:
        next_first_brt_aw = first_brt_aw.replace(month=first_brt_aw.month + 1)
    last_brt_aw = next_first_brt_aw - timedelta(microseconds=1)
    first_brt_naive = first_brt_aw.replace(tzinfo=None)
    last_brt_naive = last_brt_aw.replace(tzinfo=None)
    return first_brt_aw, last_brt_aw, first_brt_naive, last_brt_naive


def br_month_bounds_dual_utc_and_naive(dt_brt: datetime | None = None):
    """Retorna (start_utc, end_utc, start_brt_naive, end_brt_naive)."""
    first_brt_aw, last_brt_aw, first_brt_naive, last_brt_naive = br_month_bounds(dt_brt)
    return first_brt_aw.astimezone(UTC), last_brt_aw.astimezone(UTC), first_brt_naive, last_brt_naive


def to_brt_str(dt_aware: datetime) -> str:
    if dt_aware.tzinfo is None:
        dt_aware = dt_aware.replace(tzinfo=UTC)
    dt_brt = dt_aware.astimezone(BRT)
    return dt_brt.strftime("%d/%m/%Y %H:%M")


def to_brt_iso(dt_aware: datetime) -> str:
    if dt_aware.tzinfo is None:
        dt_aware = dt_aware.replace(tzinfo=UTC)
    dt_brt = dt_aware.astimezone(BRT)
    return dt_brt.strftime("%Y-%m-%dT%H:%M:%S")


# ============================ BOOTSTRAP / GUARDA DE TABELAS ============================

DB_READY = False

def _ensure_db():
    """Garante que todas as tabelas existam e cria admin padrão."""
    global DB_READY
    if DB_READY:
        return
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            need_create = any(not inspector.has_table(t)
                              for t in ("users", "cooperados", "estabelecimentos", "lancamentos"))
        except Exception:
            need_create = True

        if need_create:
            db.create_all()
        # Admin padrão
        if not User.query.filter_by(email="admin@coopex").first():
            admin = User(email="admin@coopex", tipo="admin")
            admin.set_password(os.getenv("ADMIN_PASSWORD", "123"))
            db.session.add(admin)
            db.session.commit()
        DB_READY = True

# cria no import (worker do gunicorn)
_ensure_db()

# reforça em cada request até confirmar
@app.before_request
def _before_any_request():
    _ensure_db()


# ============================ ROTAS: LOGIN / LOGOUT ============================

@app.route("/login", methods=["GET", "POST"])
def login():
    # reforço: se SQLite for apagado a cada deploy, isso recria as tabelas
    _ensure_db()

    if request.method == "POST":
        # Aceita tanto <input name="email"> quanto <input name="username">
        login_id = (request.form.get("email") or request.form.get("username") or "").strip().lower()
        senha = request.form.get("senha") or ""

        # Autentica apenas pela tabela Users (sem selecionar tipo manualmente)
        user = User.query.filter_by(email=login_id).first()
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
    total_pedidos = db.session.query(func.count(Lancamento.id)).scalar() or 0
    total_valor = db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).scalar() or 0
    total_cooperados = Cooperado.query.count()
    total_estabelecimentos = Estabelecimento.query.count()

    # Top cooperados por soma no mês atual (BRT) cobrindo aware e naive
    start_utc, end_utc, start_brt_naive, end_brt_naive = br_month_bounds_dual_utc_and_naive()
    rows = (
        db.session.query(Cooperado.nome, func.coalesce(func.sum(Lancamento.valor), 0).label("soma"))
        .join(Lancamento, Lancamento.cooperado_id == Cooperado.id)
        .filter(
            or_(
                and_(Lancamento.data >= start_utc, Lancamento.data <= end_utc),
                and_(Lancamento.data >= start_brt_naive, Lancamento.data <= end_brt_naive),
            )
        )
        .group_by(Cooperado.id)
        .order_by(desc("soma"))
        .limit(10)
        .all()
    )
    cooperado_nomes = [r[0] for r in rows]
    lancamentos_contagem = [float(r[1]) for r in rows]

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

    cooperados = Cooperado.query.order_by(Cooperado.nome.asc()).all()

    start_utc, end_utc, start_brt_naive, end_brt_naive = br_month_bounds_dual_utc_and_naive()
    lancs = (
        Lancamento.query
        .filter(
            Lancamento.estabelecimento_id == est.id,
            or_(
                and_(Lancamento.data >= start_utc, Lancamento.data <= end_utc),
                and_(Lancamento.data >= start_brt_naive, Lancamento.data <= end_brt_naive),
            )
        )
        .order_by(Lancamento.data.desc())
        .all()
    )

    lancamentos_view = [{
        "id": l.id,
        "os_numero": l.os_numero,
        "valor": float(l.valor),
        "descricao": l.descricao or "",
        "cooperado": l.cooperado,
        "data_brasilia": to_brt_str(l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)),
        "data": (l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)).astimezone(BRT),
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

    start_utc, end_utc, start_brt_naive, end_brt_naive = br_month_bounds_dual_utc_and_naive()
    lancs = (
        Lancamento.query
        .filter(
            Lancamento.cooperado_id == coop.id,
            or_(
                and_(Lancamento.data >= start_utc, Lancamento.data <= end_utc),
                and_(Lancamento.data >= start_brt_naive, Lancamento.data <= end_brt_naive),
            )
        )
        .order_by(Lancamento.data.desc())
        .all()
    )

    lancamentos_view = [{
        "id": l.id,
        "os_numero": l.os_numero,
        "valor": float(l.valor),
        "descricao": l.descricao or "",
        "estabelecimento": l.estabelecimento,
        "data_brasilia": to_brt_str(l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)),
        "data": (l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)).astimezone(BRT),
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

    # Filtro de datas (BRT) — robusto para registros antigos sem tz
    if data_inicio and data_fim:
        try:
            di = date.fromisoformat(data_inicio)
            df = date.fromisoformat(data_fim)
            di_utc, _, di_brt_naive, _ = br_day_bounds_dual_utc_and_naive(di)
            _, df_utc, _, df_brt_naive = br_day_bounds_dual_utc_and_naive(df)
            q = q.filter(
                or_(
                    and_(Lancamento.data >= di_utc, Lancamento.data <= df_utc),
                    and_(Lancamento.data >= di_brt_naive, Lancamento.data <= df_brt_naive),
                )
            )
        except Exception:
            pass
    else:
        # padrão: mês atual (BRT), cobrindo aware UTC e naive BRT
        month_start_utc, month_end_utc, month_start_naive, month_end_naive = br_month_bounds_dual_utc_and_naive()
        q = q.filter(
            or_(
                and_(Lancamento.data >= month_start_utc, Lancamento.data <= month_end_utc),
                and_(Lancamento.data >= month_start_naive, Lancamento.data <= month_end_naive),
            )
        )

    q = q.order_by(Lancamento.data.desc())
    lancamentos = q.all()

    # Converte p/ BRT para exibição
    lancamentos_view = []
    for l in lancamentos:
        dt = l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)
        lancamentos_view.append({
            "id": l.id,
            "os_numero": l.os_numero,
            "valor": float(l.valor),
            "descricao": l.descricao or "",
            "cooperado": l.cooperado,
            "estabelecimento": l.estabelecimento,
            "data_fmt": to_brt_str(dt),
            "data_iso": to_brt_iso(dt),
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
            di_utc, _, di_brt_naive, _ = br_day_bounds_dual_utc_and_naive(di)
            _, df_utc, _, df_brt_naive = br_day_bounds_dual_utc_and_naive(df)
            q = q.filter(
                or_(
                    and_(Lancamento.data >= di_utc, Lancamento.data <= df_utc),
                    and_(Lancamento.data >= di_brt_naive, Lancamento.data <= df_brt_naive),
                )
            )
        except Exception:
            pass
    else:
        month_start_utc, month_end_utc, month_start_naive, month_end_naive = br_month_bounds_dual_utc_and_naive()
        q = q.filter(
            or_(
                and_(Lancamento.data >= month_start_utc, Lancamento.data <= month_end_utc),
                and_(Lancamento.data >= month_start_naive, Lancamento.data <= month_end_naive),
            )
        )

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
        ws = writer.sheets["Lancamentos"]
        ws.set_column(0, 0, 20)
        ws.set_column(1, 1, 14)
        ws.set_column(2, 3, 28)
        ws.set_column(4, 4, 14)
        ws.set_column(5, 5, 40)

    output.seek(0)
    fname = f"lancamentos_{datetime.now(BRT).strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(output, as_attachment=True, download_name=fname, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ============================ APIs auxiliares (dashboard/admin) ============================

@app.route("/api/ultimo-lancamento")
@login_required
def api_ultimo_lancamento():
    last = db.session.query(func.max(Lancamento.id)).scalar() or 0
    return jsonify({"last_id": int(last)})

@app.route("/api/ultimo_lancamento")
@login_required
def api_ultimo_lancamento_alias():
    return api_ultimo_lancamento()


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
    dt = l.data if l.data.tzinfo else l.data.replace(tzinfo=UTC)
    return jsonify({
        "ok": True,
        "id": l.id,
        "cooperado": l.cooperado.nome if l.cooperado else "",
        "cooperado_nome": l.cooperado.nome if l.cooperado else "",
        "estabelecimento": l.estabelecimento.nome if l.estabelecimento else "",
        "valor": float(l.valor),
        "data": to_brt_str(dt),
    })


# ============================ UTIL: foto de cooperado / arquivos estáticos extras ============================

def _detect_image_mimetype(blob: bytes) -> str:
    if not blob:
        return "application/octet-stream"
    if blob.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


@app.route("/foto-cooperado/<int:id>")
@login_required
def foto_cooperado(id: int):
    c = Cooperado.query.get(id)
    if not c:
        return ("", 404)
    if c.foto_data:
        mime = _detect_image_mimetype(c.foto_data)
        return send_file(BytesIO(c.foto_data), mimetype=mime)
    if c.foto and os.path.exists(c.foto):
        lower = c.foto.lower()
        if lower.endswith(".png"): mt = "image/png"
        elif lower.endswith(".gif"): mt = "image/gif"
        elif lower.endswith(".webp"): mt = "image/webp"
        else: mt = "image/jpeg"
        return send_file(c.foto, mimetype=mt)
    return ("", 404)


@app.route("/statics/<path:filename>")
def statics_files(filename):
    base = os.path.join(app.root_path, "statics")
    path = os.path.join(base, filename)
    if not os.path.isfile(path):
        return ("", 404)
    return send_file(path)


# ============================ COMANDOS ÚTEIS (DEV) ============================

@app.cli.command("initdb")
def initdb():
    """Cria as tabelas e um admin padrão (EMAIL=admin@coopex, SENHA=123 ou ADMIN_PASSWORD)."""
    db.create_all()

    if not User.query.filter_by(email="admin@coopex").first():
        admin = User(email="admin@coopex", tipo="admin")
        admin.set_password(os.getenv("ADMIN_PASSWORD", "123"))
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
        _ensure_db()
    app.run(debug=True)
