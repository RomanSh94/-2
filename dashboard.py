"""X20 Admin Dashboard — Flask с поддержкой CSV экспорта"""
import threading, csv, io
from functools import wraps
from flask import Flask, render_template_string, request, redirect, session, url_for, send_file
from markupsafe import escape as _e
from config import ADMIN_PASSWORD, ADMIN_PORT, DASHBOARD_HOST, DASHBOARD_SECRET
from database import (
    sync_stats, sync_daily, sync_users, sync_user, sync_user_messages,
    sync_user_summary, sync_moderation, sync_risk_breakdown,
    sync_outcome_stats, sync_ab_stats, sync_quality_stats,
    sync_adverse_events, sync_validator_blocks, sync_export_query_safe,
    sync_get_profile, sync_get_profile_history,
    sync_unreviewed_flags, sync_mark_flag_reviewed, sync_toxic_blocks,
    sync_crisis_with_protective,
    _EXPORT_ALLOWED_TABLES,
)

app = Flask(__name__)
app.secret_key = DASHBOARD_SECRET or (ADMIN_PASSWORD + "_x20")

def auth(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("ok"): return redirect(url_for("login"))
        return f(*a, **k)
    return w

@app.route("/login", methods=["GET","POST"])
def login():
    err = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["ok"] = True; return redirect("/")
        err = "Wrong password"
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>X20 Admin</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>body{{background:linear-gradient(135deg,#0f0c29,#302b63);min-height:100vh;display:flex;align-items:center;justify-content:center}}
.card{{border:none;border-radius:18px;width:360px}}</style></head><body>
<div class="card p-4 shadow-lg">
  <div class="text-center mb-3" style="font-size:2.4rem">🤖</div>
  <h5 class="text-center fw-bold mb-1">X20 Admin</h5>
  <p class="text-center text-muted mb-3" style="font-size:.82rem">Psychological Safety Dashboard</p>
  {f'<div class="alert alert-danger py-2 small">{err}</div>' if err else ''}
  <form method="POST">
    <input type="password" name="password" class="form-control mb-3" placeholder="Password" autofocus required>
    <button class="btn btn-primary w-100 fw-bold">Login</button>
  </form>
</div></body></html>"""

@app.route("/logout")
def logout():
    session.clear(); return redirect("/login")

@app.route("/")
@auth
def index():
    stats = sync_stats()
    daily = sync_daily(7)
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>X20 Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<style>
body{{background:#f4f6fb}}
.card{{border:none;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.06)}}
.stat-num{{font-size:1.8rem;font-weight:700;line-height:1}}
</style></head><body>
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid">
    <a class="navbar-brand" href="/">🤖 X20 Admin</a>
    <div class="ms-auto">
      <a href="/export" class="btn btn-sm btn-outline-light">📊 Export CSV</a>
      <a href="/logout" class="btn btn-sm btn-outline-danger">Logout</a>
    </div>
  </div>
</nav>
<div class="container-fluid p-4">
  <h4 class="mb-4">📊 Dashboard Overview</h4>
  <div class="row g-3 mb-4">
    <div class="col-md-3"><div class="card p-3"><div class="stat-num">{stats['total_users']}</div><small class="text-muted">Total Users</small></div></div>
    <div class="col-md-3"><div class="card p-3"><div class="stat-num">{stats['total_messages']}</div><small class="text-muted">Messages</small></div></div>
    <div class="col-md-3"><div class="card p-3"><div class="stat-num">{stats['interventions']}</div><small class="text-muted">Interventions</small></div></div>
    <div class="col-md-3"><div class="card p-3"><div class="stat-num">{stats['avg_improvement']}</div><small class="text-muted">Avg Improvement</small></div></div>
  </div>
  <div class="row g-3">
    <div class="col-md-6"><div class="card p-4"><h6 class="fw-bold mb-3">Risk Events (7 days)</h6>
      <p>Critical: {stats['mod_critical']} | High: {stats['mod_total']} | Today: {stats['mod_today']}</p></div></div>
    <div class="col-md-6"><div class="card p-4"><h6 class="fw-bold mb-3">Quality Metrics</h6>
      <p>Positive Feedback: {stats['quality_positive']} | Adverse Events: {stats['adverse_events']}</p></div></div>
  </div>
  <div class="mt-4">
    <a href="/users" class="btn btn-primary">👥 Users</a>
    <a href="/moderation" class="btn btn-warning">🛡️ Moderation</a>
    <a href="/safety" class="btn btn-danger">🆘 Safety review</a>
    <a href="/research" class="btn btn-info">🔬 Research</a>
  </div>
</div>
</body></html>"""

@app.route("/users")
@auth
def users():
    users_list = sync_users(100)
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Users</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body><div class="container-fluid p-4">
<a href="/" class="btn btn-secondary mb-3">← Back</a>
<h4>Users ({len(users_list)})</h4>
<div class="table-responsive"><table class="table table-striped">
<tr><th>ID</th><th>Name</th><th>Messages</th><th>Last Seen</th><th>Actions</th></tr>
{''.join(f"<tr><td>{u['id']}</td><td>{_e(u['first_name'] or '')} (@{_e(u['username'] or '')})</td><td>{u['message_count']}</td><td>{u['last_seen'][:16]}</td><td><a href='/user/{u['id']}' class='btn btn-sm btn-outline-primary'>View</a></td></tr>" for u in users_list)}
</table></div></div></body></html>"""

@app.route("/user/<int:uid>")
@auth
def user_detail(uid):
    user = sync_user(uid)
    msgs = sync_user_messages(uid)
    summary = sync_user_summary(uid)
    if not user: return "User not found", 404
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>User {uid}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body><div class="container-fluid p-4">
<a href="/users" class="btn btn-secondary mb-3">← Back</a>
<a href="/profile/{user['id']}" class="btn btn-outline-info mb-3">📊 Psychology profile</a>
<h4>User {user['id']}: {_e(user['first_name'] or '')}</h4>
{f'<div class="alert alert-info"><b>Memory Summary:</b><br>{_e(summary)}</div>' if summary else ''}
<h6>Recent Messages ({len(msgs)})</h6>
<div>{''.join(f"<div style='margin:5px 0;padding:8px;background:#f0f0f0;border-radius:4px'><b>{_e(m['role'].upper())}</b> [{_e(m['scenario'])}]<br>{_e(m['content'][:200])}</div>" for m in msgs)}</div>
</div></body></html>"""

@app.route("/safety/review/<int:flag_id>")
@auth
def safety_mark_reviewed(flag_id):
    sync_mark_flag_reviewed(flag_id)
    return redirect("/safety")


@app.route("/safety")
@auth
def safety_review():
    import json
    pf = sync_crisis_with_protective(50)
    flags = sync_unreviewed_flags(50)
    toxic = sync_toxic_blocks(50)

    def pf_row(r):
        uid, level, pj, excerpt, created = r
        try:
            anchors = ", ".join(json.loads(pj or "[]"))
        except Exception:
            anchors = ""
        return (f"<tr><td>{created[:16]}</td><td><a href='/profile/{uid}'>{uid}</a></td>"
                f"<td><span class='badge bg-danger'>{_e(level)}</span></td>"
                f"<td>{_e(anchors)}</td><td>{_e((excerpt or '')[:80])}</td></tr>")

    def flag_row(r):
        fid, uid, ftype, ctx, created = r
        return (f"<tr><td>{created[:16]}</td><td><a href='/profile/{uid}'>{uid}</a></td>"
                f"<td>{_e(ftype)}</td><td>{_e(ctx or '')}</td>"
                f"<td><a class='btn btn-sm btn-outline-success' href='/safety/review/{fid}'>✓ просмотрено</a></td></tr>")

    def toxic_row(r):
        uid, matched, original, created = r
        return (f"<tr><td>{created[:16]}</td><td><a href='/profile/{uid}'>{uid}</a></td>"
                f"<td>{_e(matched or '')}</td><td>{_e((original or '')[:100])}</td></tr>")

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Safety review</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body><div class="container-fluid p-4">
<a href="/" class="btn btn-secondary mb-3">← Back</a>
<h4>Безопасность — на проверку</h4>

<h6 class="mt-4">🟦 Резкое улучшение — на ревью ({len(flags)})</h6>
<div class="table-responsive"><table class="table table-sm table-striped">
<tr><th>Время</th><th>User</th><th>Тип</th><th>Контекст</th><th></th></tr>
{''.join(flag_row(r) for r in flags) or '<tr><td colspan=5 class="text-muted">пусто</td></tr>'}
</table></div>

<h6 class="mt-4">🛟 Кризисы с опорами ({len(pf)})</h6>
<div class="table-responsive"><table class="table table-sm table-striped">
<tr><th>Время</th><th>User</th><th>Уровень</th><th>Опоры</th><th>Сообщение</th></tr>
{''.join(pf_row(r) for r in pf) or '<tr><td colspan=5 class="text-muted">пусто</td></tr>'}
</table></div>

<h6 class="mt-4">🚫 Блоки токсичной валидации ({len(toxic)})</h6>
<div class="table-responsive"><table class="table table-sm table-striped">
<tr><th>Время</th><th>User</th><th>Сработало</th><th>Заблокированный ответ</th></tr>
{''.join(toxic_row(r) for r in toxic) or '<tr><td colspan=4 class="text-muted">пусто</td></tr>'}
</table></div>
</div></body></html>"""


@app.route("/profile/<int:uid>")
@auth
def admin_profile(uid):
    import json
    p = sync_get_profile(uid)
    if not p:
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Profile {uid}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body><div class="container-fluid p-4">
<a href="/user/{uid}" class="btn btn-secondary mb-3">← Back</a>
<div class="alert alert-secondary">No psychology profile yet for user {uid}.</div>
</div></body></html>"""

    dims = [
        ("Loneliness", "loneliness_value", "loneliness_confidence"),
        ("Hopelessness", "hopelessness_value", "hopelessness_confidence"),
        ("Anxiety", "anxiety_value", "anxiety_confidence"),
        ("Self-criticism", "self_criticism_value", "self_criticism_confidence"),
        ("Social support", "social_support_value", "social_support_confidence"),
        ("Future orientation", "future_orientation_value", "future_orientation_confidence"),
        ("Energy", "energy_value", "energy_confidence"),
        ("Sleep problems", "sleep_problems_value", "sleep_problems_confidence"),
    ]
    def bar(v, c):
        pct = round((v or 0) * 100)
        col = "secondary" if (c or 0) < 0.3 else ("danger" if pct >= 66 else "warning" if pct >= 33 else "success")
        lab = f"{pct}%" + (" (low data)" if (c or 0) < 0.3 else "")
        return (f"<div class='progress' style='height:20px'>"
                f"<div class='progress-bar bg-{col}' style='width:{pct}%'>{lab}</div></div>")
    rows = "".join(
        f"<tr><td>{name}</td><td style='width:60%'>{bar(p[v], p[c])}</td>"
        f"<td>conf {round((p[c] or 0)*100)}%</td></tr>" for name, v, c in dims)

    trend = p.get("mood_trend", "stable")
    trend_badge = {"deteriorating": "danger", "improving": "success"}.get(trend, "secondary")
    alert = ""
    if trend == "deteriorating" and (p.get("crisis_risk") or 0) >= 0.7:
        alert = ("<div class='alert alert-danger'><b>⚠️ Attention:</b> deteriorating trend "
                 "with elevated crisis risk. Consider manual outreach.</div>")
    themes = ", ".join(json.loads(p.get("main_themes") or "[]")) or "—"

    hist = sync_get_profile_history(uid, 30)
    hist_rows = ""
    for snap_json, created in hist:
        try:
            s = json.loads(snap_json)
            hist_rows += (f"<tr><td>{created[:16]}</td>"
                          f"<td>{round((s.get('hopelessness_value') or 0)*100)}%</td>"
                          f"<td>{round((s.get('loneliness_value') or 0)*100)}%</td>"
                          f"<td>{round((s.get('anxiety_value') or 0)*100)}%</td>"
                          f"<td>{_e(s.get('mood_trend',''))}</td></tr>")
        except Exception:
            continue

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Profile {uid}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body><div class="container-fluid p-4">
<a href="/user/{uid}" class="btn btn-secondary mb-3">← Back</a>
<h4>Psychology profile — user {uid}</h4>
<p class="text-muted">Deterministic aggregate of risk signals — not a diagnosis. Based on {p.get('messages_analyzed',0)} messages. Trend: <span class="badge bg-{trend_badge}">{_e(trend)}</span> · crisis_risk {round((p.get('crisis_risk') or 0)*100)}%</p>
{alert}
<p><b>Themes:</b> {_e(themes)}</p>
<table class="table table-sm"><tbody>{rows}</tbody></table>
<h6 class="mt-4">History (last {len(hist)} snapshots)</h6>
<div class="table-responsive"><table class="table table-striped table-sm">
<tr><th>Time</th><th>Hopelessness</th><th>Loneliness</th><th>Anxiety</th><th>Trend</th></tr>
{hist_rows}
</table></div>
</div></body></html>"""


@app.route("/moderation")
@auth
def moderation():
    logs = sync_moderation(100)
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Moderation</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body><div class="container-fluid p-4">
<h4>Moderation Log ({len(logs)})</h4>
<div class="table-responsive"><table class="table table-striped">
<tr><th>Time</th><th>User</th><th>Risk Level</th><th>Score</th><th>Categories</th><th>Message</th></tr>
{''.join(f"<tr><td>{l['created_at'][:16]}</td><td>{_e(l['first_name'] or '')}</td><td><span class='badge bg-{('danger' if l['risk_level']=='critical' else 'warning' if l['risk_level']=='high' else 'info')}'>{_e(l['risk_level'])}</span></td><td>{l['risk_score']}</td><td>{_e(l['risk_cats'] or '')}</td><td>{_e(l['message_text'][:100] if l['message_text'] else '')}</td></tr>" for l in logs)}
</table></div></div></body></html>"""

@app.route("/research")
@auth
def research():
    outcomes = sync_outcome_stats()
    ab_data = sync_ab_stats()
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Research</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body><div class="container-fluid p-4">
<h4>Research Metrics</h4>
<h6 class="mt-4">Outcome by Scenario</h6>
<div class="table-responsive"><table class="table table-striped">
<tr><th>Scenario</th><th>Sessions</th><th>Avg Delta</th><th>% Helped</th></tr>
{''.join(f"<tr><td>{r['scenario']}</td><td>{r['total']}</td><td>{r['avg_delta']}</td><td>{r['pct_helped']}%</td></tr>" for r in outcomes)}
</table></div>
<h6 class="mt-4">A/B Test Results</h6>
<div class="table-responsive"><table class="table table-striped">
<tr><th>Variant</th><th>Sessions</th><th>Avg Delta</th><th>% Helped</th></tr>
{''.join(f"<tr><td>{a['ab_variant']}</td><td>{a['sessions']}</td><td>{a['avg_delta']}</td><td>{a['pct_helped']}%</td></tr>" for a in ab_data)}
</table></div>
</div></body></html>"""

@app.route("/export")
@auth
def export():
    table = request.args.get("table", "intervention_results")
    if table == "all_data":
        table = "intervention_results"
    if table not in _EXPORT_ALLOWED_TABLES:
        return f"Invalid table. Allowed: {', '.join(sorted(_EXPORT_ALLOWED_TABLES))}", 400
    cols, rows = sync_export_query_safe(table)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(cols)
    writer.writerows(rows)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"x20_{table}.csv"
    )

def start_dashboard():
    if ADMIN_PASSWORD == "change_me":
        print("⚠️  WARNING: ADMIN_PASSWORD is default 'change_me' — set it in .env")
    t = threading.Thread(
        target=lambda: app.run(host=DASHBOARD_HOST, port=ADMIN_PORT, debug=False, use_reloader=False),
        daemon=True, name="x20-dashboard")
    t.start()
    print(f"✅ Dashboard → http://{DASHBOARD_HOST}:{ADMIN_PORT}")
