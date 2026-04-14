"""
Flask Admin Panel for Insect Bot.
Runs on the same process as the Telegram bot, different port thread.
"""

from __future__ import annotations
import logging
from functools import wraps

from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, flash

import utils.db as db
from utils.inat import search_taxa, get_observations, get_key_status as inat_key_status
from utils.groq_client import get_key_status as groq_key_status
from config import ADMIN_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = ADMIN_SECRET_KEY


# ── Auth ──────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == ADMIN_USERNAME and
                request.form.get("password") == ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Неверные данные"
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Health endpoint (for UptimeRobot) ────────────────────────

@app.route("/health")
def health():
    try:
        db.get_client().table("settings").select("key").limit(1).execute()
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({"status": "ok" if db_ok else "degraded", "db": db_ok}), 200 if db_ok else 503


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


# ── Dashboard ─────────────────────────────────────────────────

@app.route("/admin")
@login_required
def dashboard():
    stats = db.get_stats()
    recent = db.get_recent_requests(20)
    inat_keys = inat_key_status()
    groq_keys = groq_key_status()
    settings = db.get_all_settings()
    return render_template_string(
        DASHBOARD_HTML,
        stats=stats,
        recent=recent,
        inat_keys=inat_keys,
        groq_keys=groq_keys,
        settings=settings,
    )


# ── Users ─────────────────────────────────────────────────────

@app.route("/admin/users")
@login_required
def users():
    page = int(request.args.get("page", 1))
    per_page = 50
    offset = (page - 1) * per_page
    user_list = db.get_all_users(limit=per_page, offset=offset)
    total = db.count_users()
    return render_template_string(
        USERS_HTML,
        users=user_list,
        page=page,
        total=total,
        per_page=per_page,
    )


@app.route("/admin/users/<int:telegram_id>/ban", methods=["POST"])
@login_required
def ban_user(telegram_id: int):
    db.set_user_ban(telegram_id, True)
    flash(f"Пользователь {telegram_id} заблокирован")
    return redirect(url_for("users"))


@app.route("/admin/users/<int:telegram_id>/unban", methods=["POST"])
@login_required
def unban_user(telegram_id: int):
    db.set_user_ban(telegram_id, False)
    flash(f"Пользователь {telegram_id} разблокирован")
    return redirect(url_for("users"))


@app.route("/admin/users/<int:telegram_id>/limit", methods=["POST"])
@login_required
def set_limit(telegram_id: int):
    limit = int(request.form.get("limit", 20))
    db.set_user_limit(telegram_id, limit)
    flash(f"Лимит для {telegram_id} установлен: {limit}")
    return redirect(url_for("users"))


# ── Settings ──────────────────────────────────────────────────

@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        for key in ["score_threshold", "daily_limit_default", "resize_max_kb", "bot_active", "groq_model"]:
            val = request.form.get(key)
            if val is not None:
                db.set_setting(key, val.strip())
        flash("Настройки сохранены")
        return redirect(url_for("settings_page"))
    settings = db.get_all_settings()
    return render_template_string(SETTINGS_HTML, settings=settings)


# ── iNaturalist tools ─────────────────────────────────────────

@app.route("/admin/inat", methods=["GET", "POST"])
@login_required
def inat_tools():
    results = []
    observations = []
    query = ""
    obs_taxon_id = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "search":
            query = request.form.get("query", "")
            results = search_taxa(query, per_page=10)
        elif action == "observations":
            obs_taxon_id = request.form.get("taxon_id")
            if obs_taxon_id:
                observations = get_observations(int(obs_taxon_id), per_page=10)

    return render_template_string(
        INAT_TOOLS_HTML,
        results=results,
        observations=observations,
        query=query,
        obs_taxon_id=obs_taxon_id,
    )


# ── API: key status (JSON) ────────────────────────────────────

@app.route("/admin/api/key-status")
@login_required
def api_key_status():
    return jsonify({
        "inat": inat_key_status(),
        "groq": groq_key_status(),
    })


# ── HTML Templates ────────────────────────────────────────────

BASE_STYLE = """
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #f4f6f9; color: #222; }
  nav { background: #1a1a2e; color: white; padding: 14px 24px; display: flex; align-items: center; gap: 24px; }
  nav a { color: #ccc; text-decoration: none; font-size: 14px; }
  nav a:hover, nav a.active { color: white; }
  nav .brand { font-weight: bold; font-size: 18px; color: white; margin-right: auto; }
  .container { max-width: 1100px; margin: 0 auto; padding: 24px; }
  h1, h2 { margin-top: 0; }
  .card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat { background: white; border-radius: 8px; padding: 20px; text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .stat .num { font-size: 32px; font-weight: bold; color: #3b5bdb; }
  .stat .label { font-size: 13px; color: #666; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 12px; background: #f1f3f5; border-bottom: 1px solid #dee2e6; }
  td { padding: 8px 12px; border-bottom: 1px solid #f0f0f0; }
  tr:hover td { background: #fafafa; }
  .btn { display: inline-block; padding: 6px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; text-decoration: none; }
  .btn-sm { padding: 4px 10px; font-size: 12px; }
  .btn-primary { background: #3b5bdb; color: white; }
  .btn-danger { background: #e03131; color: white; }
  .btn-success { background: #2f9e44; color: white; }
  .btn-gray { background: #868e96; color: white; }
  input, select, textarea { padding: 8px 12px; border: 1px solid #ced4da; border-radius: 6px; font-size: 14px; width: 100%; margin-bottom: 10px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: bold; }
  .badge-green { background: #d3f9d8; color: #2f9e44; }
  .badge-red { background: #ffe3e3; color: #c92a2a; }
  .badge-yellow { background: #fff3bf; color: #e67700; }
  .flash { background: #d3f9d8; color: #2f9e44; padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; }
  form { margin: 0; display: inline; }
  .inat-card { border: 1px solid #dee2e6; border-radius: 8px; padding: 16px; margin-bottom: 12px; display: flex; gap: 16px; }
  .inat-card img { width: 80px; height: 80px; object-fit: cover; border-radius: 6px; background: #f0f0f0; }
</style>
"""

NAV = """
<nav>
  <span class="brand">🦋 Insect Bot Admin</span>
  <a href="/admin">Дашборд</a>
  <a href="/admin/users">Пользователи</a>
  <a href="/admin/settings">Настройки</a>
  <a href="/admin/inat">iNaturalist</a>
  <a href="/logout">Выйти</a>
</nav>
"""

LOGIN_HTML = """<!doctype html><html><head><title>Вход</title>""" + BASE_STYLE + """</head><body>
<div style="max-width:360px;margin:100px auto;">
  <div class="card">
    <h2>🦋 Вход в админку</h2>
    {% if error %}<div class="badge badge-red" style="padding:8px;margin-bottom:12px;">{{ error }}</div>{% endif %}
    <form method="post">
      <input name="username" placeholder="Логин" required>
      <input name="password" type="password" placeholder="Пароль" required>
      <button type="submit" class="btn btn-primary" style="width:100%;padding:10px;">Войти</button>
    </form>
  </div>
</div>
</body></html>"""

DASHBOARD_HTML = """<!doctype html><html><head><title>Дашборд</title>""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="container">
  <h1>Дашборд</h1>
  <div class="stats-grid">
    <div class="stat"><div class="num">{{ stats.total_users }}</div><div class="label">Пользователей</div></div>
    <div class="stat"><div class="num">{{ stats.requests_today }}</div><div class="label">Запросов сегодня</div></div>
    <div class="stat"><div class="num">{{ stats.requests_week }}</div><div class="label">За 7 дней</div></div>
    <div class="stat"><div class="num">{{ stats.total_requests }}</div><div class="label">Всего запросов</div></div>
    <div class="stat"><div class="num">{{ stats.errors }}</div><div class="label">Ошибок</div></div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
    <div class="card">
      <h2>Топ видов (7 дней)</h2>
      {% if stats.top_taxa %}
      <table>
        <tr><th>#</th><th>Вид</th><th>Раз</th></tr>
        {% for name, count in stats.top_taxa %}
        <tr><td>{{ loop.index }}</td><td>{{ name }}</td><td>{{ count }}</td></tr>
        {% endfor %}
      </table>
      {% else %}<p>Нет данных</p>{% endif %}
    </div>

    <div class="card">
      <h2>Статус API ключей</h2>
      <b>iNaturalist</b>
      {% for k in inat_keys %}
      <div style="margin:6px 0;">
        {{ k.key_hint }} — 
        <span class="badge {{ 'badge-green' if k.available else 'badge-red' }}">
          {{ 'доступен' if k.available else 'cooldown ' + k.cooldown_remaining|string + 's' }}
        </span>
        использован {{ k.use_count }} раз
      </div>
      {% endfor %}
      <br><b>Groq</b>
      {% for k in groq_keys %}
      <div style="margin:6px 0;">
        {{ k.key_hint }} — 
        <span class="badge {{ 'badge-green' if k.available else 'badge-red' }}">
          {{ 'доступен' if k.available else 'cooldown ' + k.cooldown_remaining|string + 's' }}
        </span>
        использован {{ k.use_count }} раз
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    <h2>Последние запросы</h2>
    <table>
      <tr><th>Время</th><th>Пользователь</th><th>Вид</th><th>Score</th><th>Время отв.</th><th>Статус</th></tr>
      {% for r in recent %}
      <tr>
        <td>{{ r.created_at[:19] }}</td>
        <td>{{ r.users.username if r.users else r.telegram_id }}</td>
        <td>{{ r.taxon_name or '—' }}</td>
        <td>{{ '%.0f%%'|format(r.score * 100) if r.score else '—' }}</td>
        <td>{{ r.response_time_ms }}ms</td>
        <td><span class="badge {{ 'badge-green' if r.success else 'badge-red' }}">{{ 'OK' if r.success else 'ERR' }}</span></td>
      </tr>
      {% endfor %}
    </table>
  </div>
</div></body></html>"""

USERS_HTML = """<!doctype html><html><head><title>Пользователи</title>""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="container">
  <h1>Пользователи ({{ total }})</h1>
  {% with messages = get_flashed_messages() %}
    {% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}
  {% endwith %}
  <div class="card" style="padding:0;overflow:hidden;">
  <table>
    <tr><th>ID</th><th>Username</th><th>Имя</th><th>Сегодня</th><th>Лимит</th><th>Статус</th><th>Регистрация</th><th>Действия</th></tr>
    {% for u in users %}
    <tr>
      <td>{{ u.telegram_id }}</td>
      <td>{{ '@' + u.username if u.username else '—' }}</td>
      <td>{{ u.first_name }} {{ u.last_name or '' }}</td>
      <td>{{ u.requests_today }}</td>
      <td>
        <form method="post" action="/admin/users/{{ u.telegram_id }}/limit" style="display:flex;gap:4px;align-items:center;">
          <input name="limit" value="{{ u.daily_limit }}" style="width:60px;margin:0;padding:4px 6px;">
          <button type="submit" class="btn btn-sm btn-gray">✓</button>
        </form>
      </td>
      <td><span class="badge {{ 'badge-red' if u.is_banned else 'badge-green' }}">{{ 'Бан' if u.is_banned else 'OK' }}</span></td>
      <td>{{ u.created_at[:10] }}</td>
      <td>
        {% if u.is_banned %}
        <form method="post" action="/admin/users/{{ u.telegram_id }}/unban">
          <button class="btn btn-sm btn-success">Разбан</button>
        </form>
        {% else %}
        <form method="post" action="/admin/users/{{ u.telegram_id }}/ban">
          <button class="btn btn-sm btn-danger">Бан</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  </div>
  <div style="display:flex;gap:12px;margin-top:12px;">
    {% if page > 1 %}<a href="?page={{ page-1 }}" class="btn btn-gray">← Назад</a>{% endif %}
    {% if users|length == per_page %}<a href="?page={{ page+1 }}" class="btn btn-primary">Вперёд →</a>{% endif %}
  </div>
</div></body></html>"""

SETTINGS_HTML = """<!doctype html><html><head><title>Настройки</title>""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="container">
  <h1>Настройки бота</h1>
  {% with messages = get_flashed_messages() %}
    {% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}
  {% endwith %}
  <div class="card" style="max-width:600px;">
    <form method="post">
      <label>Минимальный score iNaturalist (0.0–1.0)
        <input name="score_threshold" value="{{ settings.score_threshold.value if settings.score_threshold else '0.40' }}">
      </label>
      <label>Лимит запросов по умолчанию (запросов/день)
        <input name="daily_limit_default" value="{{ settings.daily_limit_default.value if settings.daily_limit_default else '20' }}">
      </label>
      <label>Максимальный размер фото (KB)
        <input name="resize_max_kb" value="{{ settings.resize_max_kb.value if settings.resize_max_kb else '500' }}">
      </label>
      <label>Groq модель
        <input name="groq_model" value="{{ settings.groq_model.value if settings.groq_model else '' }}">
      </label>
      <label>Бот активен
        <select name="bot_active">
          <option value="true" {{ 'selected' if (settings.bot_active.value if settings.bot_active else 'true') == 'true' }}>Да</option>
          <option value="false" {{ 'selected' if (settings.bot_active.value if settings.bot_active else 'true') == 'false' }}>Нет</option>
        </select>
      </label>
      <button type="submit" class="btn btn-primary">Сохранить</button>
    </form>
  </div>
  {% for key, s in settings.items() %}
  {% if key not in ['score_threshold','daily_limit_default','resize_max_kb','groq_model','bot_active'] %}
  <div class="card" style="max-width:600px;padding:12px 20px;">
    <b>{{ key }}</b>: {{ s.value }}
    <span style="color:#999;font-size:12px;">— {{ s.description or '' }}</span>
  </div>
  {% endif %}
  {% endfor %}
</div></body></html>"""

INAT_TOOLS_HTML = """<!doctype html><html><head><title>iNaturalist</title>""" + BASE_STYLE + """</head><body>
""" + NAV + """
<div class="container">
  <h1>iNaturalist инструменты</h1>

  <div class="card">
    <h2>🔍 Поиск таксона</h2>
    <form method="post" style="display:flex;gap:10px;align-items:flex-end;">
      <input type="hidden" name="action" value="search">
      <div style="flex:1;"><input name="query" value="{{ query }}" placeholder="Название вида (латынь или рус.)" style="margin:0;"></div>
      <button type="submit" class="btn btn-primary">Найти</button>
    </form>
    {% if results %}
    <div style="margin-top:16px;">
      {% for r in results %}
      <div class="inat-card">
        {% if r.photo_url %}<img src="{{ r.photo_url }}" alt="">{% else %}<div style="width:80px;height:80px;background:#f0f0f0;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:24px;">🦋</div>{% endif %}
        <div style="flex:1;">
          <b>{{ r.name }}</b> {% if r.common_name %}<span style="color:#666;">({{ r.common_name }})</span>{% endif %}
          <br><span class="badge badge-yellow">{{ r.rank }}</span>
          <span style="color:#888;font-size:12px;margin-left:8px;">{{ r.observations_count|default(0) }} наблюдений</span>
          <div style="margin-top:8px;display:flex;gap:8px;">
            {% if r.wikipedia_url %}<a href="{{ r.wikipedia_url }}" target="_blank" class="btn btn-sm btn-gray">Wikipedia</a>{% endif %}
            <form method="post">
              <input type="hidden" name="action" value="observations">
              <input type="hidden" name="taxon_id" value="{{ r.id }}">
              <button type="submit" class="btn btn-sm btn-primary">Наблюдения</button>
            </form>
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>

  {% if observations %}
  <div class="card">
    <h2>Последние наблюдения (taxon_id={{ obs_taxon_id }})</h2>
    <table>
      <tr><th>Дата</th><th>Место</th><th>Пользователь</th><th>Качество</th><th>Ссылка</th></tr>
      {% for o in observations %}
      <tr>
        <td>{{ o.observed_on or o.created_at[:10] }}</td>
        <td>{{ o.place_guess or '—' }}</td>
        <td>{{ o.user.login if o.user else '—' }}</td>
        <td><span class="badge badge-green">{{ o.quality_grade }}</span></td>
        <td><a href="https://www.inaturalist.org/observations/{{ o.id }}" target="_blank" class="btn btn-sm btn-gray">→</a></td>
      </tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}
</div></body></html>"""
