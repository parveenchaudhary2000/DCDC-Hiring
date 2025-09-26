# app.py — HMS single-file app (Flask)
# Referrer error fixed: NO Flask-WTF. We use a small session CSRF (not enforced on /login).

import os, io, json, sqlite3, secrets, datetime
from functools import wraps
from urllib.parse import urlencode
from flask import (
    Flask, request, redirect, url_for, session, render_template_string, flash,
    send_from_directory, send_file, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import load_workbook, Workbook
from markupsafe import escape

import os
os.environ["WTF_CSRF_ENABLED"] = "false"   # env kill switch

# ------------------------------ App constants --------------------------------
BUILD_TAG   = "HMS-2025-09-26-CSRF-FIX-FULL"
APP_TITLE   = "Hiring Management System (HMS)"
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB  = os.path.join(BASE_DIR, "hms.db")
DB_PATH     = os.environ.get("HMS_DB_PATH") or "/home/yourusername/dcdchiringsystem/DCDC-Hiring/hms.db"
if not os.path.exists(os.path.dirname(DB_PATH)): DB_PATH = DEFAULT_DB
SECRET_KEY  = os.environ.get("HMS_SECRET") or os.environ.get("SECRET_KEY") or "dev-only-change-me"
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads"); os.makedirs(UPLOAD_DIR, exist_ok=True)
LOGO_FILE   = "logo.png"

ROLE_ADMIN="admin"; ROLE_VP="vp"; ROLE_HR="hr"; ROLE_MANAGER="manager"; ROLE_INTERVIEWER="interviewer"
POSTS = ["Trainee","Junior Technician","Senior Technician","Staff Nurse","Doctor","DMO","Others"]
ALLOWED_CV_EXTS = {".pdf",".doc",".docx"}

# ------------------------------- Flask setup ---------------------------------
app = Flask(__name__)
app.config["WTF_CSRF_ENABLED"] = False
app.config["WTF_CSRF_CHECK_DEFAULT"] = False
app.config["WTF_CSRF_SSL_STRICT"] = False
app.config["WTF_CSRF_TIME_LIMIT"] = None
app.secret_key = SECRET_KEY
app.config.update({
    "MAX_CONTENT_LENGTH": 16*1024*1024,
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
})
if os.environ.get("FLASK_ENV") == "production" or not app.debug:
    app.config["SESSION_COOKIE_SECURE"] = True

@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer-when-downgrade")
    if "user_id" in session: resp.headers["Cache-Control"] = "no-store"
    return resp

# ------------------------------ Utilities ------------------------------------
def h(x): return "" if x is None else str(escape(x))

def send_email(to, subject, html):
    """Safe no-op if SendGrid env vars are not present."""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        key = os.environ.get("SENDGRID_API_KEY"); frm = os.environ.get("MAIL_FROM")
        if not key or not frm or not to: return
        sg = SendGridAPIClient(key)
        msg = Mail(from_email=frm, to_emails=to, subject=subject, html_content=html)
        sg.send(msg)
    except Exception:
        pass

# ------------------------------ Database -------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    db = get_db(); c = db.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL,
      role TEXT NOT NULL,
      manager_id INTEGER,
      passcode TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS candidates(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      candidate_code TEXT,
      salutation TEXT,
      full_name TEXT NOT NULL,
      email TEXT,
      qualification TEXT,
      experience_years REAL,
      current_designation TEXT,
      phone TEXT,
      cv_path TEXT,
      current_salary TEXT,
      expected_salary TEXT,
      current_location TEXT,
      preferred_location TEXT,
      post_applied TEXT NOT NULL,
      interview_date TEXT,
      current_previous_company TEXT,
      assigned_region TEXT,
      status TEXT NOT NULL,
      decision_by INTEGER,
      remarks TEXT,
      created_by INTEGER,
      created_at TEXT DEFAULT (datetime('now')),
      interviewer_id INTEGER,
      manager_owner INTEGER,
      final_decision TEXT,
      final_remark TEXT,
      finalized_by INTEGER,
      finalized_at TEXT,
      hr_join_status TEXT,
      hr_joined_at TEXT
    );

    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      body TEXT,
      is_read INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS interviews(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      candidate_id INTEGER NOT NULL,
      interviewer_id INTEGER NOT NULL,
      feedback TEXT,
      rating INTEGER,
      decision TEXT,
      is_reinterview INTEGER DEFAULT 0,
      is_edit INTEGER DEFAULT 0,
      edited_from INTEGER,
      created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS password_resets(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_email TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'open',
      created_at TEXT DEFAULT (datetime('now')),
      token TEXT,
      expires_at TEXT,
      resolved_at TEXT,
      resolver_id INTEGER
    );
    """)
    db.commit(); db.close()

def ensure_bootstrap_data():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    if (cur.fetchone()[0] or 0) == 0:
        cur.execute("INSERT INTO users (name,email,role,passcode) VALUES (?,?,?,?)",
                    ("Admin","admin@example.com","admin",generate_password_hash("admin123")))
        admin_id = cur.lastrowid
        cur.execute("INSERT INTO notifications (user_id,title,body) VALUES (?,?,?)",
                    (admin_id,"Welcome","You have successfully installed HMS."))
        db.commit()
        print("[HMS] Default admin -> admin@example.com / admin123")
    db.close()

init_db()
ensure_bootstrap_data()

# --------------------------- Auth / CSRF helpers ------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid: return None
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    row = cur.fetchone(); db.close()
    return row

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user(): return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **kw):
            u = current_user()
            if not u or u["role"] not in roles:
                flash("You do not have permission.", "error")
                return redirect(url_for("dashboard"))
            return f(*a, **kw)
        return w
    return deco

def _get_or_make_csrf():
    tok = session.get("_csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf_token"] = tok
    return tok

@app.context_processor
def inject_csrf():
    # Use in templates: {{ csrf_token() }}
    return dict(csrf_token=_get_or_make_csrf)

def require_csrf(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if request.method in ("POST","PUT","PATCH","DELETE"):
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            stored = session.get("_csrf_token")
            if not sent or not stored or sent != stored:
                flash("Security token missing/invalid. Please try again.", "error")
                return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper

# ------------------------------ Common helpers --------------------------------
def user_id_by_email(email:str):
    db=get_db(); cur=db.cursor()
    cur.execute("SELECT id FROM users WHERE email=?", (email,))
    r=cur.fetchone(); db.close()
    return r["id"] if r else None

def user_email_by_id(uid:int):
    db=get_db(); cur=db.cursor()
    cur.execute("SELECT email FROM users WHERE id=?", (uid,))
    r=cur.fetchone(); db.close()
    return r["email"] if r else None

def interviewers_for_manager(mid:int):
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT id,name FROM users WHERE role='interviewer' AND manager_id=? ORDER BY name", (mid,))
    rows = cur.fetchall(); db.close()
    return rows

def all_interviewers():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT id,name FROM users WHERE role='interviewer' ORDER BY name")
    rows = cur.fetchall(); db.close()
    return rows

def is_hr_head(u): return u and u["role"]==ROLE_HR and (u["email"] or "").lower()=="jobs@dcdc.co.in"

def notify(user_id:int, title:str, body:str=""):
    db=get_db(); cur=db.cursor()
    cur.execute("INSERT INTO notifications(user_id,title,body,is_read,created_at) VALUES(?,?,?,?,?)",
                (user_id, title, body, 0, datetime.datetime.utcnow().isoformat()))
    db.commit(); db.close()
    em = user_email_by_id(user_id)
    if em:
        send_email(em, title, "<pre style='white-space:pre-wrap'>{}</pre>".format(body))

def next_candidate_code():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT MAX(id) FROM candidates")
    row = cur.fetchone(); db.close()
    return "DCDC_C{}".format((row[0] or 0) + 1)

@app.context_processor
def inject_unread():
    u = current_user()
    n = 0
    if u:
        db=get_db(); cur=db.cursor()
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (u["id"],))
        n = cur.fetchone()[0] or 0
        db.close()
    return dict(unread_notifications=n)

def manager_for_post(post:str):
    if post in ("Staff Nurse","Doctor","DMO"):
        return user_id_by_email("clinical_manager@dcdc.co.in")
    return user_id_by_email("dialysis.coord@dcdc.co.in")

def _safe_cv_filename(name):
    base = "{}_{}".format(datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S'), secrets.token_hex(4))
    ext = os.path.splitext(name.lower())[1]
    if ext not in ALLOWED_CV_EXTS: ext = ".bin"
    return base + ext

# --------------------------------- Layout ------------------------------------
BASE_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#fafafa;color:#0f172a}
header{background:#0b5394;color:#fff;padding:10px 12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header a{color:#e2e8f0;text-decoration:none;margin-right:12px}
.brand{font-weight:800;display:flex;align-items:center;gap:10px}
.brand img{height:28px;border-radius:6px;background:#fff}
.wrap{max-width:1100px;margin:14px auto;padding:0 12px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:14px;margin:12px 0}
.btn{display:inline-block;padding:8px 12px;border-radius:10px;border:1px solid #0b5394;background:#0b5394;color:#fff;text-decoration:none;cursor:pointer}
.btn.light{background:#fff;color:#0b5394}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;border:1px solid #e5e7eb;background:#f8fafc}
.flash{padding:8px 12px;border-radius:10px;margin:8px 0}
.ok{background:#ecfdf5;border:1px solid #10b981}
.error{background:#fef2f2;border:1px solid #b91c1c}
.nav .bell{position:relative;display:inline-block}
.nav .badge{position:absolute;top:-6px;right:-10px;background:#ef4444;color:#fff;border-radius:999px;padding:0 6px;font-size:12px;border:2px solid #fff;line-height:18px;min-width:18px;text-align:center}
table{width:100%;border-collapse:collapse}
th,td{padding:8px;border-bottom:1px solid #eee;text-align:left}
</style>
</head>
<body>
<header>
  <div class="brand">
    <img src="{{ url_for('brand_logo') }}" alt="logo" onerror="this.style.display='none'">
    <div>{{ app_title }}</div>
  </div>
  <nav class="nav">
    {% if user %}
      <a href="{{ url_for('dashboard') }}">Dashboard</a>
      <a href="{{ url_for('candidates_all') }}">Candidates</a>
      <a href="{{ url_for('add_candidate') }}">Add Candidate</a>
      <a href="{{ url_for('notifications') }}" class="bell">🔔
        {% if (unread_notifications or 0)|int > 0 %}
          <span class="badge">{{ unread_notifications }}</span>
        {% endif %}
      </a>
      <a href="{{ url_for('admin_users') }}">Admin</a>
      <a href="{{ url_for('logout') }}">Logout</a>
    {% else %}
      <a href="{{ url_for('login') }}">Login</a>
    {% endif %}
  </nav>
</header>
<div class="wrap">
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    {% for cat, msg in messages %}
      <div class="flash {{ 'ok' if cat=='message' else cat }}">{{ msg }}</div>
    {% endfor %}
  {% endif %}
{% endwith %}
{{ body|safe }}
</div>
</body>
</html>
"""

def render_page(title, body_html):
    return render_template_string(BASE_HTML, title=title, app_title=APP_TITLE, user=current_user(), body=body_html)

# ---------------------------------- Static -----------------------------------
@app.route("/brand-logo")
def brand_logo():
    path = os.path.join(BASE_DIR, LOGO_FILE)
    if os.path.exists(path):
        return send_from_directory(BASE_DIR, LOGO_FILE)
    # 1x1 gif fallback
    return Response(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\n\x00\x01\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;", mimetype="image/gif")

# ---------------------------------- Auth -------------------------------------
@app.route("/", methods=["GET"])
def home(): return redirect(url_for("login"))

# LOGIN — NOT CSRF-protected to avoid referrer issues
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email = (request.form.get("email") or "").strip().lower()
        pwd   = (request.form.get("passcode") or "").strip()
        db = get_db(); cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        u = cur.fetchone(); db.close()
        if u and check_password_hash(u["passcode"], pwd):
            session.clear(); session["user_id"] = u["id"]
            flash("Logged in successfully","message")
            return redirect(url_for("dashboard"))
        flash("Invalid credentials","error")
    body = f"""
    <div class="card" style="max-width:420px;margin:48px auto">
      <h2>Sign in</h2>
      <form method="post" novalidate>
        <input type="hidden" name="csrf_token" value="{_get_or_make_csrf()}">
        <p><input name="email" type="email" required placeholder="Email" style="width:260px"></p>
        <p><input name="passcode" type="password" required placeholder="Passcode" style="width:260px"></p>
        <button class="btn" type="submit">Login</button>
      </form>
      <p style="opacity:.6">Build: {h(BUILD_TAG)}</p>
    </div>
    """
    return render_page("Login", body)

@app.route("/logout")
def logout():
    session.clear(); flash("Logged out","message")
    return redirect(url_for("login"))

# -------------------------------- Dashboard ----------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM candidates"); total = cur.fetchone()[0]
    db.close()
    body = f"""
    <div class="card">
      <h2>Dashboard</h2>
      <p>Welcome {h(u['name'])}! Total candidates: <span class="tag">{total}</span></p>
      <p><a class="btn" href="{url_for('notifications')}">Notifications</a></p>
    </div>
    """
    return render_page("Dashboard", body)

# ----------------------------- Notifications ---------------------------------
@app.route("/notifications", methods=["GET"])
@login_required
def notifications():
    u = current_user()
    db = get_db(); cur = db.cursor()
    cur.execute("""SELECT id,title,body,is_read,created_at FROM notifications
                   WHERE user_id=? ORDER BY created_at DESC""", (u["id"],))
    rows = cur.fetchall(); db.close()
    items = []
    for n in rows:
        btn = ""
        if not n["is_read"]:
            btn = f"""
            <form method="post" action="{url_for('mark_notif_read', nid=n['id'])}" style="display:inline">
              <input type="hidden" name="csrf_token" value="{_get_or_make_csrf()}">
              <button class="btn" type="submit">Mark read</button>
            </form>
            """
        items.append(f"<li style='margin:10px 0'><strong>{h(n['title'])}</strong>{' <em style=\"opacity:.6\">(read)</em>' if n['is_read'] else ''}<br>{h(n['body'] or '')}<br>{btn}</li>")
    body = f"""
    <div class="card">
      <h2>Your Notifications</h2>
      <ul>{''.join(items) or '<li>No notifications.</li>'}</ul>
      <p><a class="btn light" href="{url_for('dashboard')}">Back</a></p>
    </div>
    """
    return render_page("Notifications", body)

@app.route("/notifications/read/<int:nid>", methods=["POST"])
@login_required
@require_csrf
def mark_notif_read(nid):
    u = current_user()
    db = get_db(); cur = db.cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (nid, u["id"]))
    db.commit(); db.close()
    return redirect(url_for("notifications"))

# ------------------------------ Candidates -----------------------------------
@app.route("/candidates")
@login_required
def candidates_all():
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates ORDER BY datetime(created_at) DESC LIMIT 100")
    rows = cur.fetchall(); db.close()
    trs = "".join([
        f"<tr><td>{h(r['candidate_code'] or '-')}</td>"
        f"<td>{h(r['full_name'])}</td>"
        f"<td>{h(r['post_applied'])}</td>"
        f"<td><span class='tag'>{h(r['status'])}</span></td>"
        f"<td>{h(r['final_decision'] or '-')}</td>"
        f"<td>{h((r['created_at'] or '')[:16].replace('T',' '))}</td></tr>"
        for r in rows
    ]) or "<tr><td colspan=6>No candidates</td></tr>"
    body = f"""
    <div class="card">
      <h2>All Candidates</h2>
      <p><a class="btn" href="{url_for('add_candidate')}">Add Candidate</a></p>
      <table>
        <thead><tr><th>ID</th><th>Name</th><th>Post</th><th>Status</th><th>Final</th><th>Created</th></tr></thead>
        <tbody>{trs}</tbody>
      </table>
    </div>
    """
    return render_page("Candidates", body)

@app.route("/cv/<path:path>")
@login_required
def download_cv(path):
    full = os.path.abspath(os.path.join(UPLOAD_DIR, os.path.basename(path)))
    if not full.startswith(os.path.abspath(UPLOAD_DIR)) or not os.path.exists(full):
        flash("File not found.","error"); return redirect(url_for("candidates_all"))
    return send_from_directory(UPLOAD_DIR, os.path.basename(full), as_attachment=True)

@app.route("/add", methods=["GET","POST"])
@login_required
@role_required(ROLE_HR, ROLE_ADMIN)
def add_candidate():
    if request.method=="POST":
        # CSRF
        sent = request.form.get("csrf_token"); 
        if not sent or sent != session.get("_csrf_token"): 
            flash("Security token missing/invalid.","error"); return redirect(url_for("add_candidate"))

        f = request.form
        file = request.files.get("cv"); cv_path=None
        if file and file.filename:
            safe = _safe_cv_filename(file.filename)
            file.save(os.path.join(UPLOAD_DIR, safe)); cv_path=safe

        raw_phone = (f.get("phone") or "").strip()
        digits_only = "".join(ch for ch in raw_phone if ch.isdigit())
        if len(digits_only) != 10:
            flash("Mobile number must be exactly 10 digits.","error"); return redirect(url_for("add_candidate"))

        candidate_code = (f.get("candidate_code") or "").strip() or next_candidate_code()

        fields = dict(
            candidate_code=candidate_code, salutation=f.get("salutation","").strip(),
            full_name=f.get("full_name","").strip(), email=f.get("email","").strip(),
            qualification=f.get("qualification","").strip(),
            experience_years=(f.get("experience_years") or "").strip(),
            current_designation=f.get("current_designation","").strip(), phone=digits_only,
            current_salary=f.get("current_salary","").strip(), expected_salary=f.get("expected_salary","").strip(),
            current_location=f.get("current_location","").strip(), preferred_location=f.get("preferred_location","").strip(),
            post_applied=f.get("post_applied","").strip(), interview_date=f.get("interview_date","").strip(),
            current_previous_company=f.get("current_previous_company","").strip(), assigned_region=f.get("assigned_region","").strip(),
            remarks=f.get("remarks","").strip(),
        )
        if not fields["full_name"] or fields["post_applied"] not in POSTS:
            flash("Name and valid Post Applied are required.","error"); return redirect(url_for("add_candidate"))

        try: ey = float(fields["experience_years"]) if fields["experience_years"] else None
        except: ey = None

        manager_id = manager_for_post(fields["post_applied"])
        status = "Assigned"; u=current_user(); now=datetime.datetime.utcnow().isoformat()
        db=get_db(); cur=db.cursor()
        cur.execute("""
        INSERT INTO candidates(candidate_code,salutation,full_name,email,qualification,experience_years,current_designation,phone,cv_path,current_salary,expected_salary,current_location,preferred_location,post_applied,interview_date,current_previous_company,assigned_region,status,decision_by,remarks,created_by,created_at,interviewer_id,manager_owner,final_decision,final_remark,finalized_by,finalized_at,hr_join_status,hr_joined_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(
            fields["candidate_code"],fields["salutation"],fields["full_name"],fields["email"],fields["qualification"],ey,fields["current_designation"],fields["phone"],cv_path,fields["current_salary"],fields["expected_salary"],fields["current_location"],fields["preferred_location"],fields["post_applied"],fields["interview_date"],fields["current_previous_company"],fields["assigned_region"],status,None,fields["remarks"],u["id"],now,None,manager_id,None,None,None,None,None,None
        ))
        db.commit(); db.close()

        if manager_id:
            notify(manager_id, "Candidate Assigned to Your Role",
                   "{} (ID {}) assigned to your role.".format(fields['full_name'], candidate_code))

        flash(f"Candidate added (ID: {candidate_code}).","message"); return redirect(url_for("candidates_all"))

    options="".join(["<option>{}</option>".format(p) for p in POSTS])
    token=_get_or_make_csrf()
    body=f"""
    <div class="card">
      <h2>Add Candidate</h2>
      <form method="post" enctype="multipart/form-data">
        <input type="hidden" name="csrf_token" value="{token}">
        <p><label>Candidate Id</label><input name="candidate_code" value="{h(next_candidate_code())}"></p>
        <p><label>Salutation</label><input name="salutation"></p>
        <p><label>Name</label><input name="full_name" required></p>
        <p><label>Email</label><input name="email" type="email"></p>
        <p><label>Qualification</label><input name="qualification"></p>
        <p><label>Experience (years)</label><input name="experience_years"></p>
        <p><label>Current designation</label><input name="current_designation"></p>
        <p><label>Mobile No.</label><input name="phone" required></p>
        <p><label>Current Salary</label><input name="current_salary"></p>
        <p><label>Expected Salary</label><input name="expected_salary"></p>
        <p><label>Current Location</label><input name="current_location"></p>
        <p><label>Preferred location</label><input name="preferred_location"></p>
        <p><label>Post applied</label><select name="post_applied">{options}</select></p>
        <p><label>Interview Date</label><input name="interview_date" type="date"></p>
        <p><label>Current/Previous company</label><input name="current_previous_company"></p>
        <p><label>Region</label><input name="assigned_region"></p>
        <p><label>CV</label><input type="file" name="cv" accept=".pdf,.doc,.docx"></p>
        <p><label>Remarks</label><input name="remarks"></p>
        <button class="btn">Save</button>
      </form>
    </div>
    """
    return render_page("Add Candidate", body)

# ----------------------- Manager: assign interviewer --------------------------
@app.route("/assign/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_MANAGER, ROLE_ADMIN)
def assign_candidate(candidate_id):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c: db.close(); flash("Not found.","error"); return redirect(url_for("candidates_all"))
    if u["role"]!=ROLE_ADMIN and c["manager_owner"] not in (None, u["id"]):
        db.close(); flash("You do not own this candidate.","error"); return redirect(url_for("candidates_all"))

    if request.method=="POST":
        if request.form.get("csrf_token") != session.get("_csrf_token"):
            db.close(); flash("Security token missing/invalid.","error"); return redirect(url_for("assign_candidate",candidate_id=candidate_id))
        iid = request.form.get("interviewer_id","").strip()
        if not iid.isdigit():
            db.close(); flash("Choose an interviewer.","error"); return redirect(url_for("assign_candidate",candidate_id=candidate_id))
        cur.execute("UPDATE candidates SET interviewer_id=?, manager_owner=?, status='Assigned' WHERE id=?", (int(iid), u["id"], candidate_id))
        db.commit(); db.close()
        notify(int(iid), "New Candidate Assigned",
               "{} / ID {} ({}) has been assigned to you.".format(c['full_name'], c['candidate_code'] or '-', c['post_applied']))
        flash("Assigned to interviewer.","message"); return redirect(url_for("candidates_all"))

    ivs = interviewers_for_manager(u["id"]) if u["role"] != ROLE_ADMIN else all_interviewers()
    opts = "".join(["<option value='{}'>{}</option>".format(i['id'], i['name']) for i in ivs]) or "<option disabled>No interviewers</option>"
    token=_get_or_make_csrf()
    body=f"""
    <div class="card" style="max-width:600px;margin:0 auto">
      <h2>Assign Interviewer</h2>
      <p><strong>{h(c['full_name'])}</strong> — <span class='tag'>{h(c['post_applied'])}</span></p>
      <form method="post">
        <input type="hidden" name="csrf_token" value="{token}">
        <label>Interviewer</label>
        <select name="interviewer_id">{opts}</select>
        <div style="margin-top:10px"><button class="btn">Save</button> <a class="btn light" href="{url_for('candidates_all')}">Back</a></div>
      </form>
    </div>
    """
    db.close(); return render_page("Assign Interviewer", body)

# -------- Interviewer: feedback / edit with history --------------------------
@app.route("/interview/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_INTERVIEWER)
def interview_feedback(candidate_id):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c or c["interviewer_id"]!=u["id"]:
        db.close(); flash("Not allowed.","error"); return redirect(url_for("candidates_all"))

    cur.execute("""SELECT * FROM interviews WHERE candidate_id=? AND interviewer_id=? ORDER BY id DESC LIMIT 1""",
                (candidate_id, u["id"]))
    last = cur.fetchone()

    if request.method=="POST":
        if request.form.get("csrf_token") != session.get("_csrf_token"):
            db.close(); flash("Security token missing/invalid.","error"); return redirect(url_for('interview_feedback',candidate_id=candidate_id))
        decision = (request.form.get("decision","") or "").strip().lower()
        rating = (request.form.get("rating","") or "").strip()
        feedback = (request.form.get("feedback","") or "").strip()
        mode = request.form.get("mode","new")
        try: r = int(rating)
        except: r = None
        now = datetime.datetime.utcnow().isoformat()
        if decision not in ("selected","rejected","reinterview"):
            db.close(); flash("Choose a decision.","error"); return redirect(url_for('interview_feedback',candidate_id=candidate_id))
        is_edit = 1 if (mode=="edit" and last) else 0
        edited_from = last["id"] if (mode=="edit" and last) else None
        cur.execute("""INSERT INTO interviews(candidate_id,interviewer_id,feedback,rating,decision,is_reinterview,is_edit,edited_from,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (candidate_id,u["id"],feedback,r,decision,1 if decision=="reinterview" else 0,is_edit,edited_from,now))
        cur.execute("UPDATE candidates SET status=? WHERE id=?", ("reinterview" if decision=="reinterview" else "Assigned", candidate_id))
        db.commit(); db.close()
        if c["manager_owner"]:
            if is_edit and last:
                body = "{}: EDITED feedback.\nOld: [{} | rating {}]\nNew: [{} | rating {}]".format(
                    c['full_name'], (last['decision'] or '').upper(), last['rating'] or '-',
                    decision.upper(), r or '-'
                )
            else:
                body = "{}: {} (rating: {})".format(c['full_name'], decision.upper(), r or '-')
            notify(c["manager_owner"], "Interview Feedback Submitted", body)
        flash("Feedback {}.".format("updated" if is_edit else "submitted"),"message")
        return redirect(url_for("candidates_all"))

    token=_get_or_make_csrf()
    cur.execute("""SELECT i.*, u.name AS iv_name
                   FROM interviews i JOIN users u ON u.id=i.interviewer_id
                   WHERE i.candidate_id=? ORDER BY i.id DESC LIMIT 10""",(candidate_id,))
    hist = cur.fetchall()
    history_html = "".join([
        "<div class='card'><b>{}</b> — {} &nbsp; <span class='tag'>{}</span><br>Rating: {}<br><div style='white-space:pre-wrap'>{}</div></div>"
        .format(h(r["iv_name"]), h(r["decision"]), "EDIT" if r["is_edit"] else ("RE-INT" if r["is_reinterview"] else "NEW"),
                h(r["rating"] or "-"), h((r["feedback"] or "").strip() or "-"))
        for r in hist
    ]) or "<p>No feedback yet.</p>"
    body=f"""
    <div class="card" style="max-width:760px;margin:0 auto">
      <h2>Interviewer Feedback</h2>
      <p><strong>{h(c['full_name'])}</strong> — <span class='tag'>{h(c['post_applied'])}</span></p>
      <form method="post">
        <input type="hidden" name="csrf_token" value="{token}">
        <p><label>Rating (1-5)</label><input name="rating" placeholder="e.g. 4"></p>
        <p><label>Decision</label>
          <select name="decision">
            <option value="selected">Selected</option>
            <option value="rejected">Rejected</option>
            <option value="reinterview">Ask Re-Interview</option>
          </select>
        </p>
        <p><label>Remarks</label><textarea name="feedback" rows="4"></textarea></p>
        <p><label>Mode</label>
          <select name="mode">
            <option value="new">New feedback</option>
            <option value="edit">Edit my last feedback</option>
          </select>
        </p>
        <button class="btn">Submit</button> <a class="btn light" href="{url_for('candidates_all')}">Back</a>
      </form>
    </div>
    <div class="card"><h3>Previous Decisions</h3>{history_html}</div>
    """
    return render_page("Interviewer Feedback", body)

# --------------------------- Finalize / HR join -------------------------------
@app.route("/finalize/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_MANAGER, ROLE_ADMIN)
def finalize_candidate(candidate_id):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c: db.close(); flash("Not found.","error"); return redirect(url_for("dashboard"))
    if u["role"]!=ROLE_ADMIN and c["manager_owner"] not in (None, u["id"]):
        db.close(); flash("You do not own this candidate.","error"); return redirect(url_for("dashboard"))

    cur.execute("""SELECT i.*,u.name interviewer_name
                   FROM interviews i JOIN users u ON u.id=i.interviewer_id
                   WHERE i.candidate_id=? ORDER BY i.id DESC LIMIT 2""",(candidate_id,))
    last2 = cur.fetchall()
    last_block = "<p>No interview yet.</p>" if not last2 else "".join([
        "<div class='card'><strong>Entry #{}</strong><br>By: {}<br>Rating: {}<br>Decision: {} <span class='tag'>{}</span><br>Notes:<div style='white-space:pre-wrap'>{}</div></div>"
        .format(idx+1, h(r['interviewer_name']), h(r['rating'] or '-'), h(r['decision']),
                "EDIT" if r["is_edit"] else ("RE-INT" if r["is_reinterview"] else "NEW"),
                h((r['feedback'] or '').strip() or '-'))
        for idx, r in enumerate(last2)
    ])

    if request.method=="POST":
        if request.form.get("csrf_token") != session.get("_csrf_token"):
            db.close(); flash("Security token missing/invalid.","error"); return redirect(url_for("finalize_candidate",candidate_id=candidate_id))
        action=request.form.get("action"); remark=request.form.get("remark","").strip()
        now=datetime.datetime.utcnow().isoformat()
        if action == "select":
            cur.execute("""UPDATE candidates SET status='finalized',final_decision='selected',final_remark=?,finalized_by=?,finalized_at=?,decision_by=?,interviewer_id=NULL WHERE id=?""",
                        (remark, u["id"], now, u["id"], candidate_id))
        elif action == "reject":
            cur.execute("""UPDATE candidates SET status='finalized',final_decision='rejected',final_remark=?,finalized_by=?,finalized_at=?,decision_by=?,interviewer_id=NULL WHERE id=?""",
                        (remark, u["id"], now, u["id"], candidate_id))
        elif action=="reinterview":
            cur.execute("UPDATE candidates SET status='reinterview', final_decision=NULL, final_remark=?, interviewer_id=NULL WHERE id=?",
                        (remark,candidate_id))
        else:
            db.close(); flash("Invalid action.","error"); return redirect(url_for("finalize_candidate",candidate_id=candidate_id))
        db.commit(); db.close()

        for uid in filter(None, [c["created_by"], c["interviewer_id"], c["manager_owner"]]):
            notify(uid, "Candidate Finalized", "{} -> {}. Remark: {}".format(c['full_name'], action.upper(), (remark or '-')))

        flash("Final decision updated.","message"); return redirect(url_for("dashboard"))

    token=_get_or_make_csrf()
    body=f"""
    <div class="card" style="max-width:720px;margin:0 auto">
      <h2>Finalize Candidate</h2>
      <p><strong>{h(c['full_name'])}</strong> — <span class='tag'>{h(c['post_applied'])}</span></p>
      {last_block}
      <form method="post">
        <input type="hidden" name="csrf_token" value="{token}">
        <p><label>Final Remark</label><textarea name="remark" rows="3"></textarea></p>
        <button name="action" value="select" class="btn">Select</button>
        <button name="action" value="reject" class="btn">Reject</button>
        <button name="action" value="reinterview" class="btn">Re-Interview</button>
        <a class="btn light" href="{url_for('dashboard')}">Cancel</a>
      </form>
    </div>
    """
    return render_page("Finalize", body)

@app.route("/hr/queue")
@login_required
@role_required(ROLE_HR, ROLE_ADMIN)
def hr_join_queue():
    u = current_user(); db = get_db(); cur = db.cursor()
    base_sql = """
    SELECT c.id, c.full_name, c.post_applied,
           COALESCE(c.final_remark,'-') AS final_remark,
           strftime('%Y-%m-%d %H:%M', c.finalized_at) AS finalized_at,
           mu.name AS finalized_by_name
    FROM candidates c
    LEFT JOIN users mu ON mu.id = c.finalized_by
    WHERE c.status='finalized' AND lower(c.final_decision)='selected' AND c.hr_join_status IS NULL
    """
    if is_hr_head(u) or u["role"]==ROLE_ADMIN:
        cur.execute(base_sql + " ORDER BY c.finalized_at DESC")
    else:
        cur.execute(base_sql + " AND c.created_by=? ORDER BY c.finalized_at DESC", (u["id"],))
    rows = cur.fetchall(); db.close()
    trs = "".join([
        f"<tr><td>{h(r['full_name'])}</td><td>{h(r['post_applied'])}</td><td>{h(r['finalized_by_name'] or '-')}</td><td>{h(r['final_remark'])}</td><td>{h(r['finalized_at'] or '-')}</td><td><a class='btn' href='{url_for('hr_join_update', candidate_id=r['id'])}'>Mark Join</a></td></tr>"
        for r in rows
    ]) or "<tr><td colspan=6>None</td></tr>"
    body=f"""
    <div class="card">
      <h2>Awaiting Join Status</h2>
      <table><thead><tr><th>Name</th><th>Post</th><th>Finalized By</th><th>Final Remark</th><th>Finalized At</th><th>Action</th></tr></thead>
      <tbody>{trs}</tbody></table>
    </div>
    """
    return render_page("HR Actions", body)

@app.route("/hr/join/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_HR, ROLE_ADMIN)
def hr_join_update(candidate_id):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c: db.close(); flash("Not found.","error"); return redirect(url_for("hr_join_queue"))
    if u["role"]==ROLE_HR and not is_hr_head(u) and c["created_by"]!=u["id"]:
        db.close(); flash("You cannot edit another HR's candidate.","error"); return redirect(url_for("hr_join_queue"))
    if (c["final_decision"] or "").lower()!="selected" or c["status"]!="finalized":
        db.close(); flash("Only finalized 'Selected' candidates are updatable.","error"); return redirect(url_for("hr_join_queue"))

    if request.method == "POST":
        if request.form.get("csrf_token") != session.get("_csrf_token"):
            db.close(); flash("Security token missing/invalid.","error"); return redirect(url_for("hr_join_update", candidate_id=candidate_id))
        st = request.form.get("status")
        reason = request.form.get("reason","").strip() if st=="not_joined" else None
        if st not in ("joined","not_joined"):
            db.close(); flash("Invalid status.","error"); return redirect(url_for("hr_join_update", candidate_id=candidate_id))
        if st=="not_joined" and not reason:
            db.close(); flash("Please provide reason for Not Joined.","error"); return redirect(url_for("hr_join_update", candidate_id=candidate_id))
        now = datetime.datetime.utcnow().isoformat()
        cur.execute("UPDATE candidates SET hr_join_status=?, hr_joined_at=?, status='closed', final_remark=? WHERE id=?",
                    (st, now, reason if reason else c["final_remark"], candidate_id))
        db.commit(); db.close()
        msg = "{} join status: {}".format(c['full_name'], st.upper()) + ((" (Reason: {})".format(reason)) if reason else "")
        for uid in filter(None, [c["manager_owner"], c["finalized_by"], c["created_by"]]): notify(uid, "Join Status Updated", msg)
        flash("Join status updated.","message"); return redirect(url_for("dashboard"))

    token=_get_or_make_csrf()
    cur2=get_db().cursor()
    cur2.execute("SELECT name FROM users WHERE id=?", (c["manager_owner"],)); row_mgr = cur2.fetchone()
    cur2.execute("SELECT name FROM users WHERE id=?", (c["finalized_by"],)); row_fin = cur2.fetchone()
    manager_name = row_mgr["name"] if row_mgr else "-"
    finalized_by_name = row_fin["name"] if row_fin else "-"
    body=f"""
    <div class="card" style="max-width:700px;margin:0 auto">
      <h2>HR: Mark Join Status</h2>
      <p><strong>{h(c['full_name'])}</strong> — <span class='tag'>{h(c['post_applied'])}</span></p>
      <p>Manager: <strong>{h(manager_name)}</strong> &nbsp; | &nbsp; Finalized By: <strong>{h(finalized_by_name)}</strong></p>
      <p>Final Remark: <span style="white-space:pre-wrap">{h(c['final_remark'] or '-')}</span></p>
      <form method="post">
        <input type="hidden" name="csrf_token" value="{token}">
        <p><label>Status</label>
          <select name="status" id="status" onchange="document.getElementById('reasonBox').style.display=(this.value==='not_joined')?'block':'none';">
            <option value="joined">Joined</option>
            <option value="not_joined">Not Joined</option>
          </select>
        </p>
        <div id="reasonBox" style="display:none">
          <label>Reason (if Not Joined)</label><textarea name="reason" rows="3"></textarea>
        </div>
        <button class="btn">Save</button> <a class="btn light" href="{url_for('hr_join_queue')}">Back</a>
      </form>
    </div>
    """
    return render_page("HR Join Update", body)

# ---------------------------------- Admin ------------------------------------
@app.route("/admin/users", methods=["GET","POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_users():
    db=get_db(); cur=db.cursor()
    if request.method=="POST":
        if request.form.get("csrf_token") != session.get("_csrf_token"):
            db.close(); flash("Security token missing/invalid.","error"); return redirect(url_for("admin_users"))
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        role=request.form.get("role","").strip()
        manager_id=request.form.get("manager_id","").strip()
        passcode=request.form.get("passcode","").strip()
        if not name or not email or role not in (ROLE_ADMIN,ROLE_VP,ROLE_HR,ROLE_MANAGER,ROLE_INTERVIEWER) or not passcode:
            flash("Provide name, email, role, passcode.","error")
        else:
            mid=int(manager_id) if manager_id.isdigit() else None
            try:
                cur.execute("INSERT INTO users(name,email,role,manager_id,passcode,created_at) VALUES(?,?,?,?,?,?)",
                            (name,email,role,mid,generate_password_hash(passcode),datetime.datetime.utcnow().isoformat()))
                db.commit(); flash("User added.","message")
            except sqlite3.IntegrityError:
                flash("Email already exists.","error")
    cur.execute("SELECT id,name,email,role,manager_id FROM users ORDER BY role,name")
    users=cur.fetchall()
    cur.execute("SELECT id,name FROM users WHERE role='manager' ORDER BY name")
    mgrs = cur.fetchall()
    opts_role="".join([f"<option value='{r}'>{r}</option>" for r in [ROLE_ADMIN,ROLE_VP,ROLE_HR,ROLE_MANAGER,ROLE_INTERVIEWER]])
    opts_mgr="<option value=''>—</option>" + "".join([f"<option value='{m['id']}'>{h(m['name'])}</option>" for m in mgrs])
    rows="".join([f"<tr><td>{u['id']}</td><td>{h(u['name'])}</td><td>{h(u['email'])}</td><td>{h(u['role'])}</td><td>{h(u['manager_id'] or '-')}</td></tr>" for u in users]) or "<tr><td colspan=5>No users</td></tr>"
    token=_get_or_make_csrf()
    body=f"""
    <div class="card">
      <h2>Add User</h2>
      <form method="post">
        <input type="hidden" name="csrf_token" value="{token}">
        <p><label>Name</label><input name="name" required></p>
        <p><label>Email</label><input name="email" required></p>
        <p><label>Role</label><select name="role">{opts_role}</select></p>
        <p><label>Manager (if interviewer)</label><select name="manager_id">{opts_mgr}</select></p>
        <p><label>Passcode</label><input name="passcode" required></p>
        <button class="btn">Create</button>
      </form>
    </div>
    <div class="card">
      <h2>Users</h2>
      <table><thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Role</th><th>Manager</th></tr></thead>
      <tbody>{rows}</tbody></table>
    </div>
    """
    db.close(); return render_page("Admin: Users", body)

# --------------------------------- Bulk upload --------------------------------
@app.route("/bulk/sample")
@login_required
@role_required(ROLE_HR, ROLE_ADMIN)
def bulk_sample():
    headers = ["Candidate Id","Salutation","Name","Email","Qualification","Experience (years)","Current designation",
               "Mobile No.","Current Salary","Expected Salary","Current Location","Preferred location","Post applied",
               "Interview Date","Current/Previous company","Region","Status","remarks"]
    wb = Workbook(); ws = wb.active; ws.title = "Candidates"
    for i,hv in enumerate(headers, start=1): ws.cell(row=1, column=i).value = hv
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name="bulk_sample.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/bulk", methods=["GET","POST"])
@login_required
@role_required(ROLE_HR, ROLE_ADMIN)
def bulk_upload():
    if request.method=="POST":
        if request.form.get("csrf_token") != session.get("_csrf_token"):
            flash("Security token missing/invalid.","error"); return redirect(url_for("bulk_upload"))
        file = request.files.get("xlsx")
        if not file or not file.filename.lower().endswith(".xlsx"):
            flash("Please upload an .xlsx file.","error"); return redirect(url_for("bulk_upload"))

        safe = "bulk_{}_{}.xlsx".format(datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S'), secrets.token_hex(3))
        xpath = os.path.join(UPLOAD_DIR, safe); file.save(xpath)

        try:
            wb = load_workbook(xpath); ws = wb.active
            headers = [ (ws.cell(row=1,column=i).value or "").strip().lower() for i in range(1, ws.max_column+1) ]
            def idx(label): 
                l=label.strip().lower(); 
                return headers.index(l) if l in headers else None
            m = { k:idx(k) for k in [
                "candidate id","salutation","name","email","qualification","experience (years)","current designation",
                "mobile no.","current salary","expected salary","current location","preferred location","post applied",
                "interview date","current/previous company","region","status","remarks"
            ]}

            inserted=0; bad_phone=0; bad_post_or_name=0
            db=get_db(); cur=db.cursor()
            now = datetime.datetime.utcnow().isoformat()
            u=current_user()
            cur.execute("SELECT MAX(id) FROM candidates"); next_base = (cur.fetchone()[0] or 0)

            for r in range(2, ws.max_row+1):
                def v(key):
                    ci = m.get(key); return (ws.cell(row=r, column=ci+1).value if ci is not None else "") or ""
                post=str(v("post applied")).strip(); full_name=str(v("name")).strip()
                if post not in POSTS or not full_name: bad_post_or_name+=1; continue
                digits = "".join(ch for ch in str(v("mobile no.")).strip() if ch.isdigit())
                if len(digits) != 10: bad_phone+=1; continue
                try: ey=float(v("experience (years)")) if str(v("experience (years)"))!="" else None
                except: ey=None
                cand_code = str(v("candidate id")).strip()
                if not cand_code: next_base += 1; cand_code = "DCDC_C{}".format(next_base)
                manager_id = manager_for_post(post); status = "Assigned"
                cur.execute("""
                INSERT INTO candidates(candidate_code,salutation,full_name,email,qualification,experience_years,current_designation,phone,cv_path,current_salary,expected_salary,current_location,preferred_location,post_applied,interview_date,current_previous_company,assigned_region,status,decision_by,remarks,created_by,created_at,interviewer_id,manager_owner,final_decision,final_remark,finalized_by,finalized_at,hr_join_status,hr_joined_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,(
                    cand_code, str(v("salutation")).strip(), full_name, str(v("email")).strip(),
                    str(v("qualification")).strip(), ey, str(v("current designation")).strip(), digits,
                    None, str(v("current salary")).strip(), str(v("expected salary")).strip(), str(v("current location")).strip(),
                    str(v("preferred location")).strip(), post, str(v("interview date")).strip(), str(v("current/previous company")).strip(),
                    str(v("region")).strip(), status, None, str(v("remarks")).strip(),
                    u["id"], now, None, manager_id, None, None, None, None, None, None
                ))
                inserted+=1
                if manager_id:
                    notify(manager_id, "Candidate Assigned to Your Role",
                           "{} (ID {}) assigned to your role.".format(full_name, cand_code))
            db.commit(); db.close()
            flash(f"Bulk upload complete. Inserted {inserted}. Skipped {bad_post_or_name} (bad name/post), {bad_phone} (invalid phone).","message")
            return redirect(url_for('candidates_all'))
        except Exception as e:
            flash(f"Upload failed: {e}. Supported format: .xlsx","error"); return redirect(url_for('bulk_upload'))

    sample_cols = ", ".join([
        "Candidate Id","Salutation","Name","Email","Qualification","Experience (years)","Current designation",
        "Mobile No.","Current Salary","Expected Salary","Current Location","Preferred location","Post applied",
        "Interview Date","Current/Previous company","Region","Status","remarks"
    ])
    token=_get_or_make_csrf()
    body=f"""
    <div class="card" style="max-width:700px;margin:0 auto">
      <h2>Bulk Upload (Excel .xlsx)</h2>
      <p>Expected columns: <span class="tag">{h(sample_cols)}</span></p>
      <p><a class="btn light" href="{url_for('bulk_sample')}">Download Sample Excel</a></p>
      <form method="post" enctype="multipart/form-data">
        <input type="hidden" name="csrf_token" value="{token}">
        <label>Choose .xlsx file</label><input type="file" name="xlsx" accept=".xlsx" required>
        <div style="margin-top:10px"><button class="btn">Upload</button> <a class="btn light" href="{url_for('candidates_all')}">Back</a></div>
      </form>
    </div>
    """
    return render_page("Bulk Upload", body)

# ---------------------------------- Main -------------------------------------
if __name__ == "__main__":
    print("=== RUNNING", BUILD_TAG, "DB:", DB_PATH, "===")
    app.run(debug=True, host="0.0.0.0", port=5000)
