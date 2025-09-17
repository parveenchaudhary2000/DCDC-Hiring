import os, re, sqlite3, datetime, secrets, io, json
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash, send_from_directory, send_file
from openpyxl import load_workbook, Workbook

# Security & CSRF
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf

# Email (SendGrid)
def send_email(to, subject, html):
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        key = os.environ.get("SENDGRID_API_KEY")
        frm = os.environ.get("MAIL_FROM")
        if not key or not frm or not to:
            return
        sg = SendGridAPIClient(key)
        msg = Mail(from_email=frm, to_emails=to, subject=subject, html_content=html)
        sg.send(msg)
    except Exception:
        # no crash if email not configured
        pass

APP_TITLE = "Hiring Management System (HMS)"
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "hms.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Require a secret via env (set it in your WSGI)
SECRET_KEY = os.environ["HMS_SECRET"]
LOGO_FILENAME = "logo.png"
POSTS = ["Trainee","Junior Technician","Senior Technician","Staff Nurse","Doctor","DMO","Others"]

ROLE_ADMIN="admin"; ROLE_VP="vp"; ROLE_HR="hr"; ROLE_MANAGER="manager"; ROLE_INTERVIEWER="interviewer"
ALLOWED_CV_EXTS = {".pdf",".doc",".docx"}

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# Session / cookie hardening (tuned for PythonAnywhere; set SESSION_COOKIE_SECURE=0 in env for local http dev)
secure_cookie = os.environ.get("SESSION_COOKIE_SECURE", "").lower() not in ("0","false","no","off")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=secure_cookie,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=8)
)

# CSRF protection
csrf = CSRFProtect(app)

# Make csrf_token() available in templates
@app.context_processor
def inject_csrf():
    return dict(csrf_token=generate_csrf)

# Auto-inject CSRF hidden input into every POST form in HTML responses
@app.after_request
def inject_csrf_inputs(response):
    try:
        if response.content_type.startswith("text/html"):
            html = response.get_data(as_text=True)
            token = generate_csrf()
            # insert after each <form ... method="post" ...> opening tag
            pattern = re.compile(r'(<form\b[^>]*\bmethod=["\']?post["\']?[^>]*>)', re.IGNORECASE)
            html = pattern.sub(lambda m: m.group(1) + f'\n<input type="hidden" name="csrf_token" value="{token}">', html)
            response.set_data(html)
    except Exception:
        pass
    return response

# ---------- DB ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db(); c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL,
      role TEXT NOT NULL,
      manager_id INTEGER,
      passcode TEXT NOT NULL,
      created_at TEXT NOT NULL
    );""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS password_resets(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_email TEXT NOT NULL,
      state TEXT NOT NULL,
      created_at TEXT NOT NULL,
      resolved_at TEXT,
      resolver_id INTEGER,
      new_passcode TEXT
    );""")

    c.execute("""
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
      created_by INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      interviewer_id INTEGER,
      manager_owner INTEGER,
      final_decision TEXT,
      final_remark TEXT,
      finalized_by INTEGER,
      finalized_at TEXT,
      hr_join_status TEXT,
      hr_joined_at TEXT
    );""")

    # interviews table includes editable audit fields
    c.execute("""
    CREATE TABLE IF NOT EXISTS interviews(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      candidate_id INTEGER NOT NULL,
      interviewer_id INTEGER NOT NULL,
      feedback TEXT,
      rating INTEGER,
      decision TEXT,
      is_reinterview INTEGER DEFAULT 0,
      created_at TEXT NOT NULL,
      -- audit for edits
      prev_feedback TEXT,
      prev_rating INTEGER,
      prev_decision TEXT,
      is_edited INTEGER DEFAULT 0,
      edited_at TEXT,
      edited_by INTEGER
    );""")

    # NEW: notifications
    c.execute("""
    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      body TEXT,
      is_read INTEGER DEFAULT 0,
      created_at TEXT NOT NULL
    );""")

    # Seed users if empty
    c.execute("SELECT COUNT(*) AS ct FROM users")
    if c.fetchone()["ct"] == 0:
        now = datetime.datetime.utcnow().isoformat()
        seed = [
            ("Mr. Parveen Chaudhary","clinicalanalyst@dcdc.co.in",ROLE_ADMIN,None,"admin12345"),
            ("Mr. Deepak Agarwal","drdeepak@dcdc.co.in",ROLE_VP,None,"vp123456"),
            ("Ms. Barkha","jobs@dcdc.co.in",ROLE_HR,None,"hr123456"),
            ("Deepika","hiring@dcdc.co.in",ROLE_HR,None,"hrdp1234"),
            ("Karishma","hr_hiring@dcdc.co.in",ROLE_HR,None,"hrka1234"),
            ("Kajal","hiring_1@dcdc.co.in",ROLE_HR,None,"hrkj1234"),
            ("Sneha","hiring_2@dcdc.co.in",ROLE_HR,None,"hrsn1234"),
            ("Ravi","hiring_3@dcdc.co.in",ROLE_HR,None,"hrrv1234"),
            ("Shivani","recruitments@dcdc.co.in",ROLE_HR,None,"hrsv1234"),
            ("Udita","careers@dcdc.co.in",ROLE_HR,None,"hrud1234"),
            ("Dr. Yasir Anis","clinical_manager@dcdc.co.in",ROLE_MANAGER,None,"yasir1234"),
            ("Ms. Prachi","infectioncontroller@dcdc.co.in",ROLE_INTERVIEWER,None,"prachi1234"),
            ("Mr. Shaikh Saadi","dialysis.coord@dcdc.co.in",ROLE_MANAGER,None,"saadi1234"),
            ("Ms. Pankaja","rmclinical_4@dcdc.co.in",ROLE_INTERVIEWER,None,"pankaja1234"),
            ("Mr. Yekula Bhanu Prakash","rmclinical_6@dcdc.co.in",ROLE_INTERVIEWER,None,"bhanu1234"),
            ("Mr. Rohit","clinical_therapist@dcdc.co.in",ROLE_INTERVIEWER,None,"rohit1234"),
        ]
        for n,e,r,m,p in seed:
            c.execute(
                "INSERT INTO users(name,email,role,manager_id,passcode,created_at) VALUES(?,?,?,?,?,?)",
                (n,e,r,m,generate_password_hash(p),now)
            )

        # link interviewers to managers
        def uid(em):
            c.execute("SELECT id FROM users WHERE email=?", (em,)); rr=c.fetchone(); return rr["id"] if rr else None
        yasir = uid("clinical_manager@dcdc.co.in")
        saadi = uid("dialysis.coord@dcdc.co.in")
        c.execute("UPDATE users SET manager_id=? WHERE email='infectioncontroller@dcdc.co.in'", (yasir,))
        for em in ("rmclinical_4@dcdc.co.in","rmclinical_6@dcdc.co.in","clinical_therapist@dcdc.co.in"):
            c.execute("UPDATE users SET manager_id=? WHERE email=?", (saadi, em))

    # PRAGMA + helpful indexes for speed at scale
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_created_at    ON candidates(created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_status        ON candidates(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_final_dec     ON candidates(final_decision)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_join_status   ON candidates(hr_join_status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_post          ON candidates(post_applied)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_region        ON candidates(assigned_region)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_manager       ON candidates(manager_owner)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cand_interviewer   ON candidates(interviewer_id)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_cand_code_nonnull ON candidates(candidate_code) WHERE candidate_code IS NOT NULL")

    conn.commit(); conn.close()

    # Lightweight migration for existing DBs to add new audit columns
    migrate_db()

def migrate_db():
    conn = get_db(); c = conn.cursor()
    c.execute("PRAGMA table_info(interviews)")
    cols = {row[1] for row in c.fetchall()}
    def addcol(name, type_sql):
        c.execute(f"ALTER TABLE interviews ADD COLUMN {name} {type_sql}")
    if "prev_feedback" not in cols: addcol("prev_feedback","TEXT")
    if "prev_rating"   not in cols: addcol("prev_rating","INTEGER")
    if "prev_decision" not in cols: addcol("prev_decision","TEXT")
    if "is_edited"     not in cols: addcol("is_edited","INTEGER DEFAULT 0")
    if "edited_at"     not in cols: addcol("edited_at","TEXT")
    if "edited_by"     not in cols: addcol("edited_by","INTEGER")
    conn.commit(); conn.close()

def user_id_by_email(email:str):
    db=get_db(); cur=db.cursor()
    cur.execute("SELECT id FROM users WHERE email=?", (email,))
    r=cur.fetchone(); db.close()
    return r["id"] if r else None

def user_email_by_id(uid:int):
    db=get_db(); cur=db.cursor()
    cur.execute("SELECT email FROM users WHERE id=?", (uid,))
    r=cur.fetchone(); db.close()
    return (r["email"] if r else None)

def notify(user_id:int, title:str, body:str=""):
    # store in-app notif
    db=get_db(); cur=db.cursor()
    cur.execute("INSERT INTO notifications(user_id,title,body,is_read,created_at) VALUES(?,?,?,?,?)",
                (user_id, title, body, 0, datetime.datetime.utcnow().isoformat()))
    db.commit(); db.close()
    # email (best-effort)
    em = user_email_by_id(user_id)
    if em:
        send_email(em, title, f"<p>{body}</p>")

# ---------- Helpers / Auth ----------

def current_user():
    uid = session.get("user_id")
    if not uid: return None
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    row = cur.fetchone(); db.close()
    return row

def is_hr_head(u): return u and u["role"]==ROLE_HR and u["email"].lower()=="jobs@dcdc.co.in"

def login_required(f):
    @wraps(f)
    def w(*a,**kw):
        if not current_user(): return redirect(url_for("login"))
        return f(*a,**kw)
    return w

def role_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a,**kw):
            u = current_user()
            if not u or u["role"] not in roles:
                flash("You do not have permission.","error")
                return redirect(url_for("dashboard"))
            return f(*a,**kw)
        return w
    return deco

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

def candidate_role_scope_where(u):
    """
    Returns (where_sql, args) for candidate queries, respecting the user's role/scope.
    """
    if u["role"] in (ROLE_ADMIN, ROLE_VP) or (u["role"]==ROLE_HR and is_hr_head(u)):
        return "1=1", []
    elif u["role"] == ROLE_HR:
        return "created_by=?", [u["id"]]
    elif u["role"] == ROLE_MANAGER:
        return "manager_owner=?", [u["id"]]
    else:
        return "interviewer_id=?", [u["id"]]

# unread notifications badge
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

# ---------- UI (FIXED HEADER + BELL) ----------

BASE_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<style>
:root { --vein-blue:#0b5394; --artery-red:#b91c1c; --muted:#e5e7eb; --text:#0f172a; }
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#fafafa;color:var(--text)}
header{background:var(--vein-blue);color:#fff;padding:10px 12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header a{color:#e2e8f0;text-decoration:none;margin-right:12px}
.brand{font-weight:800;display:flex;align-items:center;gap:10px}
.brand img{height:28px;width:auto;display:block;border-radius:6px;background:#fff}
.wrap{max-width:1100px;margin:14px auto;padding:0 12px}
.card{background:#fff;border:1px solid var(--muted);border-radius:14px;padding:14px;margin:12px 0;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.row{display:flex;flex-wrap:wrap;gap:12px}
.col{flex:1;min-width:280px}
.btn{display:inline-block;padding:8px 12px;border-radius:10px;border:1px solid var(--vein-blue);background:var(--vein-blue);color:#fff;text-decoration:none;cursor:pointer}
.btn.light{background:#fff;color:#0b5394}
.btn.warn{background:#fff;color:#b45309;border-color:#b45309}
.btn.danger{background:var(--artery-red);border-color:var(--artery-red)}
input,select,textarea{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:10px;margin-top:6px}
label{font-weight:600}
table{width:100%;border-collapse:collapse}
th,td{padding:8px;border-bottom:1px solid #eee;text-align:left}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;border:1px solid var(--muted);background:#f8fafc}
.flash{padding:8px 12px;border-radius:10px;margin:8px 0}
.ok{background:#ecfdf5;border:1px solid #10b981}
.error{background:#fef2f2;border:1px solid var(--artery-red)}
.nav{display:flex;align-items:center;flex-wrap:wrap}
.nav a{margin-right:10px}
.form-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.badge{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef6ff;border:1px solid #dbeafe;color:#0b5394}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:880px){ .grid-2{grid-template-columns:1fr} }
.field{display:flex;flex-direction:column;gap:6px}
.field small.hint{color:#64748b;font-size:12px;margin-top:-4px}
.req::after{content:" *"; color:#b91c1c; font-weight:700}
.sticky-actions{position:sticky;bottom:10px;display:flex;gap:10px;justify-content:flex-end;padding-top:10px}
.card.section{padding:16px 16px 10px;border-left:6px solid #e5e7eb}
.card.section h4{margin:0 0 8px 0}
.section.blue{border-left-color:#0b53941a}
.chip{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef6ff;border:1px solid #dbeafe;color:#0b5394;margin-right:6px}

/* Inline bell badge inside nav */
.nav .bell-link{
  position:relative;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  text-decoration:none;
  font-size:18px; /* emoji size */
  margin-left:4px;
}
.nav .bell-badge{
  position:absolute;
  top:-6px;
  right:-10px;
  background:#ef4444;
  color:#fff;
  border-radius:999px;
  padding:0 6px;
  font-size:12px;
  border:2px solid #fff;
  line-height:18px;
  min-width:18px;
  text-align:center;
}
.subnav{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.subnav a{border:1px solid #dbeafe;background:#eff6ff;color:#0b5394;padding:6px 10px;border-radius:999px;text-decoration:none}
.subnav a.active{background:#0b5394;color:#fff}
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
      {% if user['role'] in ['hr','admin'] %}
        <a href="{{ url_for('add_candidate') }}">Add Candidate</a>
        <a href="{{ url_for('bulk_upload') }}">Bulk Upload</a>
        <a href="{{ url_for('hr_join_queue') }}">HR Actions</a>
      {% endif %}
      {% if user['role'] in ['admin'] %}
        <a href="{{ url_for('admin_users') }}">Admin</a>
      {% endif %}
      <a href="{{ url_for('profile') }}">Profile</a>

      <!-- Logout as POST for CSRF protection -->
      <form method="post" action="{{ url_for('logout') }}" style="display:inline; margin-left:10px">
        <button type="submit" class="btn light" style="padding:2px 8px">Logout</button>
      </form>

      <!-- Bell INSIDE nav -->
      <a href="{{ url_for('notifications') }}" class="bell-link" title="Notifications" aria-label="Notifications">🔔
        {% if (unread_notifications or 0)|int > 0 %}
          <span class="bell-badge">{{ unread_notifications }}</span>
        {% endif %}
      </a>
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

<!-- Bell auto-refresh every 30s -->
<script>
(function(){
  function updateBell(){
    fetch("{{ url_for('__unread') }}", {cache: 'no-store'})
      .then(r => r.text())
      .then(t => {
        var m = t.match(/unread=(\d+)/);
        var n = m ? parseInt(m[1], 10) : 0;
        var link = document.querySelector('.bell-link');
        if(!link) return;
        var badge = link.querySelector('.bell-badge');
        if(!badge && n>0){
          badge = document.createElement('span');
          badge.className = 'bell-badge';
          link.appendChild(badge);
        }
        if(badge){
          if(n>0){ badge.textContent = n; badge.style.display='inline-block'; }
          else{ badge.style.display='none'; }
        }
      })
      .catch(()=>{});
  }
  setInterval(updateBell, 30000); // 30s
})();
</script>

</body>
</html>
"""
def render_page(title, body_html):
    return render_template_string(BASE_HTML, title=title, app_title=APP_TITLE, user=current_user(), body=body_html)

# serve logo
@app.route("/brand-logo")
def brand_logo():
    path = os.path.join(BASE_DIR, LOGO_FILENAME)
    if os.path.exists(path):
        return send_from_directory(BASE_DIR, LOGO_FILENAME)
    from flask import Response
    return Response(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\n\x00\x01\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;", mimetype="image/gif")

@app.route("/__unread")
@login_required
def __unread():
    u = current_user()
    n = 0
    if u:
        db = get_db(); cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (u["id"],))
        n = cur.fetchone()[0] or 0
        db.close()
    return f"<pre>logged_in={bool(u)} role={u['role'] if u else '-'} unread={n}</pre>"

# ---------- Auth ----------

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email = request.form.get("email","").strip().lower()
        passcode = request.form.get("passcode","").strip()
        db=get_db(); cur=db.cursor()
        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        u = cur.fetchone(); db.close()
        if u and check_password_hash(u["passcode"], passcode):
            session["user_id"]=u["id"]; flash("Welcome!","message"); return redirect(url_for("dashboard"))
        flash("Invalid email or passcode.","error")
    body = f"""
    <div class="card" style="max-width:520px;margin:48px auto;text-align:center">
      <img src="{url_for('brand_logo')}" alt="logo" style="height:60px;margin-bottom:10px" onerror="this.style.display='none'">
      <h2 style="margin:6px 0">Sign in</h2>
      <form method="post" style="text-align:left">
        <label>Email</label><input name="email" required>
        <label>Passcode</label><input name="passcode" type="password" required>
        <div style="margin-top:10px"><button class="btn">Login</button> <a class="btn light" href="{url_for('forgot_password')}">Forgot?</a></div>
      </form>
    </div>
    """
    return render_page("Login", body)

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.","message")
    return redirect(url_for("login"))

@app.route("/forgot", methods=["GET","POST"])
def forgot_password():
    if request.method=="POST":
        email = request.form.get("email","").strip().lower()
        now = datetime.datetime.utcnow().isoformat()
        db=get_db(); cur=db.cursor()
        cur.execute("SELECT 1 FROM users WHERE email=?", (email,))
        if cur.fetchone():
            cur.execute("INSERT INTO password_resets(user_email,state,created_at) VALUES(?,?,?)",(email,"open",now))
            db.commit()
            # optional mail to user
            send_email(email, "HMS Reset Request", "<p>If you requested a reset, admin will update your passcode soon.</p>")
        db.close()
        flash("If the email exists, a reset request has been created.","message")
        return redirect(url_for("login"))
    body=f"""
    <div class="card" style="max-width:420px;margin:48px auto">
      <h3>Forgot Passcode</h3>
      <form method="post">
        <label>Your Email</label><input name="email" required>
        <div style="margin-top:10px"><button class="btn">Create Reset Request</button>
        <a class="btn light" href="{url_for('login')}">Back</a></div>
      </form>
    </div>
    """
    return render_page("Forgot Password", body)

@app.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    u=current_user()
    if request.method=="POST":
        old=request.form.get("old",""); new=request.form.get("new","")
        if not new or len(new)<8: 
            flash("New passcode must be at least 8 characters.","error")
        else:
            db=get_db(); cur=db.cursor()
            cur.execute("SELECT passcode FROM users WHERE id=?", (u["id"],))
            if not check_password_hash(cur.fetchone()["passcode"], old):
                db.close(); flash("Old passcode incorrect.","error"); return redirect(url_for("profile"))
            cur.execute("UPDATE users SET passcode=? WHERE id=?", (generate_password_hash(new),u["id"]))
            db.commit(); db.close(); flash("Passcode changed.","message"); return redirect(url_for("dashboard"))
    body=f"""
    <div class="card" style="max-width:480px;margin:0 auto">
      <h3>My Profile</h3>
      <p><span class="tag">{u['name']}</span> &nbsp; <span class="tag">{u['email']}</span> &nbsp; <span class="tag">{u['role']}</span></p>
      <form method="post">
        <label>Old Passcode</label><input name="old" type="password" required>
        <label>New Passcode</label><input name="new" type="password" required>
        <div style="margin-top:10px"><button class="btn">Update</button></div>
      </form>
    </div>
    """
    return render_page("Profile", body)

# ---------- Dashboard ----------

@app.route("/")
@login_required
def dashboard():
    u = current_user()
    db = get_db(); cur = db.cursor()

    q_status = (request.args.get("status") or "").strip()
    q_post = (request.args.get("post") or "").strip()
    q_region = (request.args.get("region") or "").strip()
    q_from = (request.args.get("from") or "").strip()
    q_to = (request.args.get("to") or "").strip()

    restrict_hr = (u["role"]=="hr") and (u["email"].lower()!="jobs@dcdc.co.in")
    where = ["1=1"]; args=[]
    if restrict_hr: where.append("created_by=?"); args.append(u["id"])
    if u["role"]==ROLE_MANAGER: where.append("manager_owner=?"); args.append(u["id"])
    if u["role"]==ROLE_INTERVIEWER: where.append("interviewer_id=?"); args.append(u["id"])

    if q_status:
        if q_status.lower()=="joined":
            where.append("hr_join_status='joined'")
        elif q_status.lower() in ("selected","rejected"):
            where.append("lower(final_decision)=?"); args.append(q_status.lower())
        else:
            where.append("status=?"); args.append(q_status)
    if q_post: where.append("post_applied=?"); args.append(q_post)
    if q_region: where.append("assigned_region=?"); args.append(q_region)
    if q_from: where.append("date(substr(created_at,1,10))>=date(?)"); args.append(q_from)
    if q_to: where.append("date(substr(created_at,1,10))<=date(?)"); args.append(q_to)

    WHERE = " AND ".join(where)

    def scalar(sql, a=()):
        cur.execute(sql, a); r = cur.fetchone(); return r[0] if r else 0

    total = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE}", args)
    selected = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND lower(final_decision)='selected'", args)
    rejected = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND lower(final_decision)='rejected'", args)
    assigned = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND status='Assigned'", args)
    joined = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND hr_join_status='joined'", args)

    cur.execute("SELECT DISTINCT post_applied FROM candidates ORDER BY post_applied")
    posts = [r[0] for r in cur.fetchall() if r[0]]
    cur.execute("SELECT DISTINCT assigned_region FROM candidates WHERE assigned_region IS NOT NULL AND assigned_region<>'' ORDER BY assigned_region")
    regions = [r[0] for r in cur.fetchall()]

    cur.execute(f"""SELECT id,full_name,post_applied,status,final_decision,hr_join_status,created_at,assigned_region
                    FROM candidates WHERE {WHERE}
                    ORDER BY datetime(created_at) DESC LIMIT 20""", args)
    recent = cur.fetchall()
    recent_rows = "".join([
        (f"<tr><td>{r['full_name']}</td><td>{r['post_applied']}</td>"
         f"<td><span class='tag'>{r['status']}</span></td>"
         f"<td>{r['final_decision'] or '-'}</td><td>{r['hr_join_status'] or '-'}</td>"
         f"<td>{r['assigned_region'] or '-'}</td><td>{r['created_at'][:19].replace('T',' ')}</td></tr>")
        for r in recent
    ]) or "<tr><td colspan=7>No candidates match your filters.</td></tr>"

    cur.execute(f"SELECT COALESCE(final_decision,'(no final)') k, COUNT(*) c FROM candidates WHERE {WHERE} GROUP BY k ORDER BY c DESC", args)
    status_rows = cur.fetchall()
    cur.execute(f"SELECT COALESCE(NULLIF(assigned_region,''),'(Unassigned)') k, COUNT(*) c FROM candidates WHERE {WHERE} GROUP BY k ORDER BY c DESC", args)
    region_rows = cur.fetchall()

    cur.execute(f"""SELECT strftime('%Y-%m', COALESCE(finalized_at, created_at)) m,
                    SUM(CASE WHEN lower(final_decision)='selected' THEN 1 ELSE 0 END) sel
                    FROM candidates WHERE {WHERE} GROUP BY m ORDER BY m""", args)
    sel_map = { r["m"]: r["sel"] for r in cur.fetchall() if r["m"] }
    cur.execute(f"""SELECT strftime('%Y-%m', hr_joined_at) m, COUNT(*) j
                    FROM candidates WHERE {WHERE} AND hr_join_status='joined' AND hr_joined_at IS NOT NULL
                    GROUP BY m ORDER BY m""", args)
    join_map = { r["m"]: r["j"] for r in cur.fetchall() if r["m"] }

    months = sorted(set(list(sel_map.keys()) + list(join_map.keys())))[-12:]
    line_labels = months
    line_sel = [ sel_map.get(m,0) for m in months ]
    line_join = [ join_map.get(m,0) for m in months ]

    db.close()

    opts_status = "".join([f"<option value='{s}' {'selected' if q_status==s else ''}>{s or 'All'}</option>"
                           for s in ["","Pending","Assigned","reinterview","finalized","Selected","Rejected","Joined"]])
    opts_post = "<option value=''>All</option>" + "".join([f"<option value='{p}' {'selected' if q_post==p else ''}>{p}</option>" for p in posts])
    opts_region = "<option value=''>All</option>" + "".join([f"<option value='{r}' {'selected' if q_region==r else ''}>{r}</option>" for r in regions])

    page_css = """
    <style>
    .dash-grid{display:grid;grid-template-columns:1fr;gap:14px}
    @media(min-width:900px){ .tiles{display:grid;grid-template-columns:repeat(5,1fr);gap:12px} }
    .filter-bar{position:sticky;top:8px;z-index:5;background:#fff;border:1px solid var(--muted);border-radius:14px;padding:12px}
    .filters{display:grid;grid-template-columns:repeat(5, minmax(160px,1fr));gap:10px}
    .tile{background:#fff;border:1px solid var(--muted);border-radius:18px;padding:14px}
    .tile h4{margin:0 0 6px 0;color:#64748b}
    .tile .num{font-size:28px;font-weight:800}
    .t1{background:#eff6ff;border-color:#dbeafe}
    .t2{background:#f0fdf4;border-color:#dcfce7}
    .t3{background:#fef2f2;border-color:#fee2e2}
    .t4{background:#f5f3ff;border-color:#ede9fe}
    .t5{background:#fffbeb;border-color:#fef3c7}
    </style>
    """

    charts_html = f"""
    <div class="card">
      <h3>Charts</h3>
      <div class="row">
        <div class="col">
          <h4>Selected / Rejected / Assigned (%)</h4>
          <canvas id="statusPie" height="240"></canvas>
        </div>
        <div class="col">
          <h4>Region-wise Candidates</h4>
          <canvas id="regionBar" height="240"></canvas>
        </div>
      </div>
      <div class="row" style="margin-top:12px">
        <div class="col">
          <h4>Selected vs Joined (Monthly)</h4>
          <canvas id="selJoinLine" height="240"></canvas>
        </div>
      </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

    <script>
    (function(){{
      const statusLabels = ["Selected","Rejected","Assigned"];
      const statusCounts = [{selected}, {rejected}, {assigned}];
      const regionLabels = {json.dumps([r["k"] for r in region_rows])};
      const regionCounts = {json.dumps([r["c"] for r in region_rows])};
      const lineLabels = {json.dumps(line_labels)};
      const lineSel = {json.dumps(line_sel)};
      const lineJoin = {json.dumps(line_join)};

      new Chart(document.getElementById('statusPie'), {{
        type: 'pie',
        data: {{ labels: statusLabels, datasets: [{{ data: statusCounts }}] }},
        options: {{
          responsive: true,
          plugins: {{
            tooltip: {{
              callbacks: {{
                label: function(ctx){{
                  const total = ctx.dataset.data.reduce((a,b)=>a+b,0) || 1;
                  const v = ctx.parsed;
                  const pct = ((v/total)*100).toFixed(1) + '%';
                  return `${{ctx.label}}: ${{v}} (${{pct}})`;
                }}
              }}
            }},
            legend: {{
              labels: {{
                generateLabels(chart){{
                  const d = chart.data.datasets[0].data, l = chart.data.labels;
                  const total = d.reduce((a,b)=>a+b,0) || 1;
                  return l.map((lab,i)=>{{
                    const pct = ((d[i]/total)*100).toFixed(1);
                    const meta = chart.getDatasetMeta(0).controller;
                    const style = meta.getStyle(i);
                    return {{
                      text: `${{lab}} (${{pct}}%)`,
                      fillStyle: style.backgroundColor,
                      strokeStyle: style.borderColor,
                      lineWidth: style.borderWidth,
                      hidden: isNaN(d[i]) || d[i] === null,
                      index: i
                    }};
                  }});
                }}
              }}
            }}
          }}
        }}
      }});

      new Chart(document.getElementById('regionBar'), {{
        type: 'bar',
        data: {{ labels: regionLabels, datasets: [{{ label: 'Candidates', data: regionCounts }}] }},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
      }});

      new Chart(document.getElementById('selJoinLine'), {{
        type: 'line',
        data: {{
          labels: lineLabels,
          datasets: [
            {{ label: 'Selected', data: lineSel, tension: 0.35 }},
            {{ label: 'Joined', data: lineJoin, tension: 0.35 }}
          ]
        }},
        options: {{ responsive: true }}
      }});
    }})();
    </script>
    """

    recent_html = f"""
    <div class="card">
      <h3>Newly Added (Latest 20)</h3>
      <div class="chip">Status: {q_status or 'All'}</div>
      <div class="chip">Post: {q_post or 'All'}</div>
      <div class="chip">Region: {q_region or 'All'}</div>
      <div class="chip">From: {q_from or '—'}</div>
      <div class="chip">To: {q_to or '—'}</div>
      <table>
        <thead><tr><th>Name</th><th>Post</th><th>Status</th><th>Final</th><th>Joined</th><th>Region</th><th>Created</th></tr></thead>
        <tbody>{recent_rows}</tbody>
      </table>
    </div>
    """

    filters_html = f"""
    <div class="filter-bar">
      <form method="get">
        <div class="filters">
          <div><label>Status</label><select name="status">{opts_status}</select></div>
          <div><label>Post</label><select name="post">{opts_post}</select></div>
          <div><label>Region</label><select name="region">{opts_region}</select></div>
          <div><label>From</label><input type="date" name="from" value="{q_from}"></div>
          <div><label>To</label><input type="date" name="to" value="{q_to}"></div>
        </div>
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn">Apply Filters</button>
          <a class="btn light" href="{url_for('dashboard')}">Clear</a>
        </div>
      </form>
    </div>
    """

    tiles_html = f"""
    <div class="tiles">
      <div class="tile t1"><h4>Total Candidates</h4><div class="num">{total}</div></div>
      <div class="tile t2"><h4>Selected</h4><div class="num">{selected}</div></div>
      <div class="tile t3"><h4>Rejected</h4><div class="num">{rejected}</div></div>
      <div class="tile t4"><h4>Assigned</h4><div class="num">{assigned}</div></div>
      <div class="tile t5"><h4>Joined</h4><div class="num">{joined}</div></div>
    </div>
    """

    body = page_css + "<div class='dash-grid'>" + filters_html + tiles_html + charts_html + recent_html + "</div>"
    return render_page("Dashboard", body)

# ---------- Notifications ----------

@app.route("/notifications")
@login_required
def notifications():
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("""SELECT id,title,body,is_read,created_at
                   FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 200""", (u["id"],))
    rows = cur.fetchall(); db.close()
    trs = "".join([
        f"<tr><td>{r['created_at'][:19].replace('T',' ')}</td>"
        f"<td><strong>{r['title']}</strong><br><div style='white-space:pre-wrap'>{r['body'] or ''}</div></td>"
        f"<td>{'Unread' if not r['is_read'] else 'Read'}</td>"
        f"<td><form method='post' action='{url_for('mark_notif_read', nid=r['id'])}' style='display:inline'>"
        f"<button class='btn'>Mark read</button></form></td></tr>"
    ]) or "<tr><td colspan=4>No notifications</td></tr>"
    body = f"""<div class="card"><h3>Notifications</h3>
    <form method="post" action="{url_for('mark_all_notif_read')}" style="margin-bottom:10px">
      <button class="btn light">Mark all as read</button>
    </form>
    <table><thead><tr><th>Time</th><th>Message</th><th>Status</th><th></th></tr></thead>
    <tbody>{trs}</tbody></table></div>"""
    return render_page("Notifications", body)

@app.route("/notifications/read/<int:nid>", methods=["POST"])
@login_required
def mark_notif_read(nid):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (nid, u["id"]))
    db.commit(); db.close()
    return redirect(url_for('notifications'))

@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notif_read():
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (u["id"],))
    db.commit(); db.close()
    return redirect(url_for('notifications'))

# ---------- Candidates (Search, Pagination, Manager Sub-pages by Post) ----------

@app.route("/candidates")
@login_required
def candidates_all():
    u = current_user(); db = get_db(); cur = db.cursor()

    # Search, post filter & pagination
    q = (request.args.get("q") or "").strip()
    post_filter = (request.args.get("post_filter") or "").strip()
    try:
        per_page = max(5, min(100, int(request.args.get("per_page", "25"))))
    except:
        per_page = 25
    try:
        page = max(1, int(request.args.get("page", "1")))
    except:
        page = 1
    offset = (page - 1) * per_page

    # Role scope
    base_where, args = candidate_role_scope_where(u)

    # Manager subnav posts list (only for managers)
    posts = []
    if u["role"] == ROLE_MANAGER:
        cur.execute(f"SELECT DISTINCT post_applied FROM candidates WHERE {base_where} AND IFNULL(post_applied,'')<>'' ORDER BY post_applied", args)
        posts = [r[0] for r in cur.fetchall()]

    # Build WHERE with search and optional post filter
    where = base_where
    if post_filter:
        where += " AND post_applied=?"; args = args + [post_filter]
    search_sql = ""
    if q:
        search_sql = " AND (full_name LIKE ? OR email LIKE ? OR phone LIKE ? OR candidate_code LIKE ? OR post_applied LIKE ? OR current_previous_company LIKE ?)"
        wild = f"%{q}%"
        args += [wild, wild, wild, wild, wild, wild]

    # Count for pagination
    cur.execute(f"SELECT COUNT(*) FROM candidates WHERE {where}{search_sql}", args)
    total = cur.fetchone()[0] or 0

    # Fetch paginated rows
    cur.execute(f"""
      SELECT *
      FROM candidates
      WHERE {where}{search_sql}
      ORDER BY datetime(created_at) DESC
      LIMIT ? OFFSET ?
    """, args + [per_page, offset])
    rows = cur.fetchall()
    db.close()

    pages = (total + per_page - 1) // per_page

    def actions(r):
        role = current_user()['role']
        if role in (ROLE_MANAGER, ROLE_ADMIN):
            return (f"<a class='btn light' href='{url_for('assign_candidate', candidate_id=r['id'])}'>Assign</a> "
                    f"<a class='btn' href='{url_for('finalize_candidate', candidate_id=r['id'])}'>Finalize</a>")
        if role==ROLE_INTERVIEWER and r['interviewer_id']==current_user()['id']:
            return f"<a class='btn' href='{url_for('interview_feedback', candidate_id=r['id'])}'>Feedback</a>"
        return "-"

    header_action = ""
    if current_user()['role'] in (ROLE_MANAGER, ROLE_ADMIN):
        header_action = f"<div style='margin-bottom:10px'><a class='btn' href='{url_for('bulk_assign')}'>Bulk Assign</a></div>"

    # Build table rows
    rows_html_list = []
    for r in rows:
        cv_html = f'<a href="{url_for("download_cv", path=r["cv_path"])}">CV</a>' if r['cv_path'] else '-'
        detail_link = f"<a href='{url_for('candidate_detail', candidate_id=r['id'])}'>{r['full_name']}</a>"
        rows_html_list.append(
            f"<tr>"
            f"<td>{r['candidate_code'] or '-'}</td>"
            f"<td>{detail_link}</td>"
            f"<td>{r['post_applied']}</td>"
            f"<td><span class='tag'>{r['status']}</span></td>"
            f"<td>{r['final_decision'] or '-'}</td>"
            f"<td>{r['hr_join_status'] or '-'}</td>"
            f"<td>{r['created_at'][:19].replace('T',' ')}</td>"
            f"<td>{cv_html}</td>"
            f"<td>{actions(r)}</td>"
            f"</tr>"
        )
    rows_html = "".join(rows_html_list) or "<tr><td colspan=9>No data</td></tr>"

    # Pagination controls
    qkeep = f"&q={q}" if q else ""
    qkeep += f"&per_page={per_page}"
    if post_filter:
        qkeep += f"&post_filter={post_filter}"
    prev_url = url_for('candidates_all') + f"?page={page-1}{qkeep}" if page>1 else None
    next_url = url_for('candidates_all') + f"?page={page+1}{qkeep}" if page<pages else None
    showing_from = offset+1 if total else 0
    showing_to = min(offset+per_page, total)

    # Export link (keeps search & post)
    export_url = url_for('export_candidates') + (f"?q={q}" if q else "")
    if post_filter:
        export_url += ("&" if "?" in export_url else "?") + f"post_filter={post_filter}"

    # Manager subnav (posts as sub-pages)
    subnav = ""
    if posts:
        chips = [f"<a href='{url_for('candidates_all')}' class='{'active' if not post_filter else ''}'>All</a>"]
        for p in posts:
            active = "active" if p == post_filter else ""
            chips.append(f"<a href='{url_for('candidates_all')}?post_filter={p}' class='{active}'>{p}</a>")
        subnav = "<div class='subnav'>" + " ".join(chips) + "</div>"

    body = f"""
    <div class="card"><h3>All Candidates</h3>
      {subnav}
      <form method="get" style="margin-bottom:10px; display:flex; gap:8px; flex-wrap:wrap">
        <input name="q" value="{q}" placeholder="Search name/email/phone/ID/post/company" style="flex:1; min-width:280px">
        <input type="hidden" name="post_filter" value="{post_filter}">
        <select name="per_page">
          {''.join([f"<option value='{n}' {'selected' if per_page==n else ''}>{n}/page</option>" for n in (10,25,50,100)])}
        </select>
        <button class="btn">Search</button>
        <a class="btn light" href="{url_for('candidates_all')}">Clear</a>
        <a class="btn" href="{export_url}">Export XLSX</a>
      </form>
      {header_action}
      <table>
        <thead>
          <tr>
            <th>Candidate ID</th>
            <th>Name</th>
            <th>Post</th>
            <th>Status</th>
            <th>Final</th>
            <th>HR Join</th>
            <th>Created</th>
            <th>CV</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      <div style="display:flex;align-items:center;gap:10px;justify-content:space-between;margin-top:10px">
        <div class="tag">Showing {showing_from}-{showing_to} of {total}</div>
        <div style="display:flex;gap:8px">
          {'<a class="btn light" href="'+prev_url+'">← Prev</a>' if prev_url else '<span class="btn light" style="opacity:.5;pointer-events:none">← Prev</span>'}
          <span class="tag">Page {page} / {pages or 1}</span>
          {'<a class="btn light" href="'+next_url+'">Next →</a>' if next_url else '<span class="btn light" style="opacity:.5;pointer-events:none">Next →</span>'}
        </div>
      </div>
    </div>
    """
    return render_page("Candidates", body)

@app.route("/candidate/<int:candidate_id>")
@login_required
def candidate_detail(candidate_id):
    u = current_user(); db = get_db(); cur = db.cursor()

    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    c = cur.fetchone()
    if not c:
        db.close(); flash("Candidate not found.","error"); return redirect(url_for("candidates_all"))

    # Enforce role scope
    scope_where, scope_args = candidate_role_scope_where(u)
    cur.execute(f"SELECT 1 FROM candidates WHERE id=? AND {scope_where}", [candidate_id]+scope_args)
    ok = cur.fetchone()
    if not ok:
        db.close(); flash("You do not have access to this candidate.","error"); return redirect(url_for("candidates_all"))

    # Interviews
    cur.execute("""
      SELECT i.*, u.name AS interviewer_name
      FROM interviews i
      LEFT JOIN users u ON u.id = i.interviewer_id
      WHERE i.candidate_id=?
      ORDER BY i.id DESC
    """, (candidate_id,))
    ivs = cur.fetchall(); db.close()

    cv_html = f'<a class="btn light" href="{url_for("download_cv", path=c["cv_path"])}">Download CV</a>' if c["cv_path"] else "<span class='tag'>No CV</span>"

    # Context actions
    acts = []
    if u["role"] in (ROLE_MANAGER, ROLE_ADMIN):
        acts.append(f"<a class='btn light' href='{url_for('assign_candidate', candidate_id=c['id'])}'>Assign</a>")
        acts.append(f"<a class='btn' href='{url_for('finalize_candidate', candidate_id=c['id'])}'>Finalize</a>")
    if u["role"] == ROLE_INTERVIEWER and c["interviewer_id"] == u["id"]:
        acts.append(f"<a class='btn' href='{url_for('interview_feedback', candidate_id=c['id'])}'>Submit / Edit Feedback</a>")
    if u["role"] in (ROLE_HR, ROLE_ADMIN) and c["status"]=='finalized' and (c["final_decision"] or '').lower()=='selected' and not c["hr_join_status"]:
        acts.append(f"<a class='btn' href='{url_for('hr_join_update', candidate_id=c['id'])}'>Mark Join</a>")
    actions_html = (" ".join(acts)) if acts else "<span class='tag'>No actions</span>"

    if not ivs:
        iv_html = "<p>No interviews yet.</p>"
    else:
        iv_rows = "".join([
            f"<tr><td>{(iv['created_at'] or '')[:19].replace('T',' ')}</td>"
            f"<td>{iv['interviewer_name'] or '-'}</td>"
            f"<td>{iv['rating'] or '-'}</td>"
            f"<td>{iv['decision']}</td>"
            f"<td style='white-space:pre-wrap'>{(iv['feedback'] or '').strip() or '-'}</td>"
            f"<td>{'Yes' if iv['is_edited'] else 'No'}</td>"
            f"<td>{iv['prev_decision'] or '-'}</td>"
            f"<td style='white-space:pre-wrap'>{(iv['prev_feedback'] or '').strip() or '-'}</td>"
            f"</tr>"
            for iv in ivs
        ])
        iv_html = f"""
        <table>
          <thead><tr><th>Time</th><th>Interviewer</th><th>Rating</th><th>Decision</th><th>Feedback</th><th>Edited?</th><th>Prev Decision</th><th>Prev Feedback</th></tr></thead>
          <tbody>{iv_rows}</tbody>
        </table>
        """

    body = f"""
    <div class="card">
      <h3>Candidate Details</h3>
      <div class="row">
        <div class="col">
          <p><strong>{c['full_name']}</strong> — <span class="tag">{c['post_applied']}</span></p>
          <p><span class="tag">ID: {c['candidate_code'] or '-'}</span> <span class="tag">Status: {c['status']}</span> <span class="tag">Final: {c['final_decision'] or '-'}</span> <span class="tag">HR Join: {c['hr_join_status'] or '-'}</span></p>
          <p>Email: {c['email'] or '-'}<br>Phone: {c['phone'] or '-'}<br>Qualification: {c['qualification'] or '-'}</p>
          <p>Experience: {c['experience_years'] or '-'} years<br>Designation: {c['current_designation'] or '-'}</p>
          <p>Company: {c['current_previous_company'] or '-'}</p>
          <p>Current Location: {c['current_location'] or '-'}<br>Preferred: {c['preferred_location'] or '-'}</p>
          <p>Region: {c['assigned_region'] or '-'}</p>
          <p>Created: {(c['created_at'] or '')[:19].replace('T',' ')}{f"<br>Interview Date: {c['interview_date']}" if c['interview_date'] else ''}</p>
          <p>{cv_html}</p>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>Interviews</h3>
      {iv_html}
    </div>

    <div class="card">
      <h3>Actions</h3>
      {actions_html}
      <div style="margin-top:10px"><a class="btn light" href="{url_for('candidates_all')}">Back</a></div>
    </div>
    """
    return render_page("Candidate", body)

@app.route("/cv/<path:path>")
@login_required
def download_cv(path):
    full = os.path.abspath(os.path.join(UPLOAD_DIR, os.path.basename(path)))
    if not full.startswith(os.path.abspath(UPLOAD_DIR)) or not os.path.exists(full):
        flash("File not found.","error"); return redirect(url_for("candidates_all"))
    return send_from_directory(UPLOAD_DIR, os.path.basename(full), as_attachment=True)

# ---------- Export ----------

@app.route("/export/candidates.xlsx")
@login_required
def export_candidates():
    u = current_user(); db = get_db(); cur = db.cursor()

    q = (request.args.get("q") or "").strip()
    post_filter = (request.args.get("post_filter") or "").strip()

    where, args = candidate_role_scope_where(u)
    if post_filter:
        where += " AND post_applied=?"; args += [post_filter]
    if q:
        where += " AND (full_name LIKE ? OR email LIKE ? OR phone LIKE ? OR candidate_code LIKE ? OR post_applied LIKE ? OR current_previous_company LIKE ?)"
        wild = f"%{q}%"
        args += [wild, wild, wild, wild, wild, wild]

    cur.execute(f"""
      SELECT candidate_code, full_name, post_applied, status, final_decision, hr_join_status,
             created_at, email, phone, qualification, experience_years, current_designation,
             current_previous_company, assigned_region, current_location, preferred_location
      FROM candidates
      WHERE {where}
      ORDER BY datetime(created_at) DESC
    """, args)
    rows = cur.fetchall(); db.close()

    wb = Workbook(); ws = wb.active; ws.title = "Candidates"
    headers = ["Candidate ID","Name","Post","Status","Final","HR Join","Created",
               "Email","Phone","Qualification","Experience (years)","Current designation",
               "Current/Previous company","Region","Current Location","Preferred Location"]
    ws.append(headers)
    for r in rows:
        ws.append([
            r["candidate_code"] or "-", r["full_name"], r["post_applied"], r["status"],
            r["final_decision"] or "-", r["hr_join_status"] or "-",
            (r["created_at"] or "")[:19].replace("T"," "),
            r["email"] or "-", r["phone"] or "-", r["qualification"] or "-",
            r["experience_years"] or "-", r["current_designation"] or "-",
            r["current_previous_company"] or "-", r["assigned_region"] or "-",
            r["current_location"] or "-", r["preferred_location"] or "-"
        ])

    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    base = "candidates"
    if post_filter:
        base += "_" + re.sub(r"\W+","_", post_filter).strip("_")
    if q:
        base += "_" + re.sub(r"\W+","_", q)[:30].strip("_")
    fname = base + ".xlsx"
    return send_file(bio, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------- Add Candidate ----------

def _safe_cv_filename(name):
    base = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    ext = os.path.splitext(name.lower())[1]
    if ext not in ALLOWED_CV_EXTS: ext = ".bin"
    return base + ext

def manager_for_post(post:str):
    if post in ("Staff Nurse","Doctor","DMO"):
        return user_id_by_email("clinical_manager@dcdc.co.in")
    return user_id_by_email("dialysis.coord@dcdc.co.in")

@app.route("/add", methods=["GET","POST"])
@login_required
@role_required(ROLE_HR,ROLE_ADMIN)
def add_candidate():
    if request.method=="POST":
        f = request.form
        file = request.files.get("cv")
        cv_path=None
        if file and file.filename:
            safe = _safe_cv_filename(file.filename)
            file.save(os.path.join(UPLOAD_DIR, safe))
            cv_path=safe

        raw_phone = (f.get("phone") or "").strip()
        digits_only = "".join(ch for ch in raw_phone if ch.isdigit())
        if len(digits_only) != 10:
            flash("Mobile number must be exactly 10 digits.","error")
            return redirect(url_for("add_candidate"))

        candidate_code = (f.get("candidate_code") or "").strip() or None

        fields = dict(
            salutation=f.get("salutation","").strip(),
            full_name=f.get("full_name","").strip(),
            email=f.get("email","").strip(),
            qualification=f.get("qualification","").strip(),
            experience_years=(f.get("experience_years") or "").strip(),
            current_designation=f.get("current_designation","").strip(),
            phone=digits_only,
            current_salary=f.get("current_salary","").strip(),
            expected_salary=f.get("expected_salary","").strip(),
            current_location=f.get("current_location","").strip(),
            preferred_location=f.get("preferred_location","").strip(),
            post_applied=f.get("post_applied","").strip(),
            interview_date=f.get("interview_date","").strip(),
            current_previous_company=f.get("current_previous_company","").strip(),
            assigned_region=f.get("assigned_region","").strip(),
            remarks=f.get("remarks","").strip(),
        )
        if not fields["full_name"] or fields["post_applied"] not in POSTS:
            flash("Name and valid Post Applied are required.","error")
            return redirect(url_for("add_candidate"))

        try: ey = float(fields["experience_years"]) if fields["experience_years"] else None
        except: ey = None

        manager_id = manager_for_post(fields["post_applied"])
        status = "Assigned"

        u=current_user(); now=datetime.datetime.utcnow().isoformat()
        db=get_db(); cur=db.cursor()
        cur.execute("""
        INSERT INTO candidates(candidate_code,salutation,full_name,email,qualification,experience_years,current_designation,phone,cv_path,current_salary,expected_salary,current_location,preferred_location,post_applied,interview_date,current_previous_company,assigned_region,status,decision_by,remarks,created_by,created_at,interviewer_id,manager_owner,final_decision,final_remark,finalized_by,finalized_at,hr_join_status,hr_joined_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(
            candidate_code,fields["salutation"],fields["full_name"],fields["email"],fields["qualification"],ey,fields["current_designation"],fields["phone"],cv_path,fields["current_salary"],fields["expected_salary"],fields["current_location"],fields["preferred_location"],fields["post_applied"],fields["interview_date"],fields["current_previous_company"],fields["assigned_region"],status,None,fields["remarks"],u["id"],now,None,manager_id,None,None,None,None,None,None
        ))
        cid = cur.lastrowid
        if candidate_code is None:
            candidate_code = f"DCDC_C{cid}"
            cur.execute("UPDATE candidates SET candidate_code=? WHERE id=?", (candidate_code, cid))
        db.commit(); db.close()

        # Notify manager (so bell shows candidates assigned to manager's role)
        if manager_id:
            notify(manager_id, "Candidate Assigned to Your Role",
                   f"{fields['full_name']} (ID {candidate_code}) assigned to your role.")

        flash(f"Candidate added (ID: {candidate_code}).","message")
        return redirect(url_for("dashboard"))

    default_code = ""  # leave blank; auto-generated after insert if not provided
    options="".join([f"<option>{p}</option>" for p in POSTS])
    body=f"""
    <div class="card">
      <div class="form-header">
        <h3 style="margin:0">Add Candidate</h3>
        <span class="badge">Auto-assigned to Clinical managers</span>
      </div>

      <form id="addForm" method="post" enctype="multipart/form-data">
        <div class="card section blue">
          <h4>Identity & Contact</h4>
          <div class="grid-2">
            <div class="field"><label>Candidate Id</label>
              <input name="candidate_code" value="{default_code}" placeholder="Auto-generated if left blank">
            </div>
            <div class="field"><label>Current Salary</label>
              <input name="current_salary" placeholder="₹ / month">
            </div>

            <div class="field"><label>Salutation</label>
              <input name="salutation" placeholder="Mr/Ms/Dr">
            </div>
            <div class="field"><label>Expected Salary</label>
              <input name="expected_salary" placeholder="₹ / month">
            </div>

            <div class="field"><label class="req">Name</label>
              <input name="full_name" required placeholder="Full name">
            </div>
            <div class="field"><label>Current Location</label>
              <input name="current_location" placeholder="City / State">
            </div>

            <div class="field"><label>Email</label>
              <input name="email" type="email" placeholder="name@example.com">
            </div>
            <div class="field"><label>Preferred location</label>
              <input name="preferred_location" placeholder="City / Region">
            </div>

            <div class="field"><label>Mobile No.</label>
              <input name="phone" placeholder="10 digits" type="tel" inputmode="numeric" maxlength="10"
                oninput="this.value=this.value.replace(/\\D/g,'').slice(0,10)" required>
              <small class="hint">Exactly 10 digits; numbers only. Example: 9876543210</small>
            </div>
            <div class="field"><label class="req">Post applied</label>
              <select name="post_applied">{options}</select>
            </div>
          </div>
        </div>

        <div class="card section blue">
          <h4>Experience & Attachments</h4>
          <div class="grid-2">
            <div class="field"><label>Qualification</label><input name="qualification"></div>
            <div class="field"><label>Experience (years)</label><input name="experience_years" placeholder="e.g. 3.5"></div>

            <div class="field"><label>Current designation</label><input name="current_designation"></div>
            <div class="field"><label>Current/Previous company</label><input name="current_previous_company"></div>

            <div class="field"><label>Interview Date</label><input type="date" name="interview_date"></div>
            <div class="field"><label>Region</label><input name="assigned_region" placeholder="e.g. North / South"></div>

            <div class="field"><label>CV Upload (pdf/doc/docx)</label><input type="file" name="cv" accept=".pdf,.doc,.docx"></div>
            <div class="field"><label>Remarks</label><input name="remarks"></div>
          </div>
        </div>

        <div class="sticky-actions">
          <button class="btn">Save Candidate</button>
          <a class="btn light" href="{url_for('dashboard')}">Cancel</a>
        </div>
      </form>
    </div>
    """
    return render_page("Add Candidate", body)

# ---------- Manager: Assign interviewer ----------

@app.route("/assign/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_MANAGER, ROLE_ADMIN)
def assign_candidate(candidate_id):
    u=current_user()
    db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c: db.close(); flash("Not found.","error"); return redirect(url_for("candidates_all"))
    if u["role"]!=ROLE_ADMIN and c["manager_owner"]!=u["id"]:
        db.close(); flash("You do not own this candidate.","error"); return redirect(url_for("candidates_all"))

    if request.method=="POST":
        iid = request.form.get("interviewer_id","").strip()
        if not iid.isdigit():
            db.close(); flash("Choose an interviewer.","error"); return redirect(url_for("assign_candidate",candidate_id=candidate_id))
        cur.execute("UPDATE candidates SET interviewer_id=?, status='Assigned' WHERE id=?", (int(iid), candidate_id))
        db.commit(); db.close()

        # Notifications: interviewer + candidate creator + manager (already sees)
        notify(int(iid), "New Candidate Assigned",
               f"{c['full_name']} / ID {c['candidate_code'] or '-'} ({c['post_applied']}) has been assigned to you.")
        if c["created_by"]:
            notify(c["created_by"], "Candidate Assigned", f"{c['full_name']} assigned to interviewer (ID {iid}).")
        flash("Assigned to interviewer.","message"); return redirect(url_for("candidates_all"))

    ivs = all_interviewers() if u["role"] == ROLE_ADMIN else interviewers_for_manager(u["id"])
    opts = "".join([f"<option value='{i['id']}' {'selected' if c['interviewer_id']==i['id'] else ''}>{i['name']}</option>" for i in ivs]) or "<option disabled>No interviewers</option>"
    body=f"""
    <div class="card" style="max-width:600px;margin:0 auto">
      <h3>Assign Interviewer</h3>
      <p><strong>{c['full_name']}</strong> — <span class="tag">{c['post_applied']}</span></p>
      <form method="post">
        <label>Interviewer</label>
        <select name="interviewer_id">{opts}</select>
        <div style="margin-top:10px"><button class="btn">Save</button> <a class="btn light" href="{url_for('candidates_all')}">Back</a></div>
      </form>
    </div>
    """
    return render_page("Assign Interviewer", body)

@app.route("/assign/bulk", methods=["GET", "POST"])
@login_required
@role_required(ROLE_MANAGER, ROLE_ADMIN)
def bulk_assign():
    u = current_user(); db = get_db(); cur = db.cursor()

    base_where = "1=1"; args = []
    if u["role"] != ROLE_ADMIN:
        base_where = "manager_owner=?"; args = [u["id"]]

    if request.method == "GET":
        cur.execute(f"""
        SELECT id, candidate_code, full_name, post_applied, status,
        COALESCE((SELECT name FROM users uu WHERE uu.id=c.interviewer_id), '-') as current_iv
        FROM candidates c
        WHERE {base_where}
        AND (c.interviewer_id IS NULL OR c.status IN ('Assigned','reinterview'))
        ORDER BY datetime(c.created_at) DESC
        """, args)
        rows = cur.fetchall()

        ivs = all_interviewers() if u["role"] == ROLE_ADMIN else interviewers_for_manager(u["id"])
        iv_opts = "".join([f"<option value='{i['id']}'>{i['name']}</option>" for i in ivs]) if ivs else "<option disabled>No interviewers</option>"

        trs = "".join([
            f"<tr>"
            f"<td><input type='checkbox' name='ids' value='{r['id']}'></td>"
            f"<td>{r['candidate_code'] or '-'}</td>"
            f"<td>{r['full_name']}</td>"
            f"<td>{r['post_applied']}</td>"
            f"<td><span class='tag'>{r['status']}</span></td>"
            f"<td>{r['current_iv']}</td>"
            f"</tr>"
            for r in rows
        ]) or "<tr><td colspan='6'>No candidates available for bulk assignment.</td></tr>"

        body = f"""
        <div class="card" style="max-width:960px;margin:0 auto">
          <h3>Bulk Assign Candidates</h3>
          <form method="post">
            <div class="row" style="margin-bottom:10px">
              <div class="col">
                <label>Assign to Interviewer</label>
                <select name="interviewer_id" required>
                  <option value="">— select —</option>
                  {iv_opts}
                </select>
              </div>
            </div>

            <table>
              <thead>
                <tr>
                  <th><input type='checkbox' id='selAll' onclick='toggleAll(this)'></th>
                  <th>ID</th><th>Name</th><th>Post</th><th>Status</th><th>Current Interviewer</th>
                </tr>
              </thead>
              <tbody>{trs}</tbody>
            </table>

            <div class="sticky-actions">
              <button class="btn">Assign Selected</button>
              <a class="btn light" href="{url_for('candidates_all')}">Cancel</a>
            </div>
          </form>
        </div>
        <script>
        function toggleAll(cb){
          document.querySelectorAll("input[name='ids']").forEach(el => {{ el.checked = cb.checked; }});
        }
        </script>
        """
        db.close(); return render_page("Bulk Assign", body)

    iid = request.form.get("interviewer_id", "").strip()
    ids = request.form.getlist("ids")
    if not iid.isdigit() or not ids:
        db.close(); flash("Pick an interviewer and at least one candidate.", "error"); return redirect(url_for("bulk_assign"))

    placeholders = ",".join("?" for _ in ids)
    params = [int(iid)] + ids
    owner_guard = ""
    if u["role"] != ROLE_ADMIN:
        owner_guard = " AND manager_owner=?"; params.append(u["id"])

    cur.execute(f"UPDATE candidates SET interviewer_id=?, status='Assigned' WHERE id IN ({placeholders}){owner_guard}", params)

    # Fetch details to include in the notification body
    cur.execute(f"SELECT candidate_code, full_name, post_applied FROM candidates WHERE id IN ({placeholders})", ids)
    det_rows = cur.fetchall()

    db.commit(); db.close()

    details = "\n".join([f"- {r['full_name']} (ID {r['candidate_code'] or '-'}) — {r['post_applied']}" for r in det_rows]) or "-"
    notify(int(iid), "Candidates Assigned", f"{len(det_rows)} candidates have been assigned to you:\n{details}")

    flash("Candidates assigned.","message")
    return redirect(url_for("candidates_all"))

# ---------- Interviewer: feedback (new + editable with audit) ----------

@app.route("/interview/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_INTERVIEWER)
def interview_feedback(candidate_id):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c or c["interviewer_id"]!=u["id"]:
        db.close(); flash("Not allowed.","error"); return redirect(url_for("candidates_all"))

    # Load the most recent feedback by THIS interviewer for audit/edit
    cur.execute("""SELECT * FROM interviews WHERE candidate_id=? AND interviewer_id=? ORDER BY id DESC LIMIT 1""",
                (candidate_id, u["id"]))
    last_my = cur.fetchone()

    if request.method=="POST":
        mode = (request.form.get("mode") or "new").strip()  # "new" or "edit"
        decision = request.form.get("decision","").strip().lower()
        rating = request.form.get("rating","").strip()
        feedback = request.form.get("feedback","").strip()
        try: r = int(rating)
        except: r = None
        now = datetime.datetime.utcnow().isoformat()
        if decision not in ("selected","rejected","reinterview"):
            db.close(); flash("Choose a decision.","error"); return redirect(url_for('interview_feedback',candidate_id=candidate_id))

        if mode == "edit":
            if not last_my:
                db.close(); flash("No previous feedback to edit.","error"); return redirect(url_for('interview_feedback',candidate_id=candidate_id))
            # Preserve the original wrong remark once; subsequent edits keep the first prev_* values
            prev_fb = last_my["prev_feedback"] if last_my["prev_feedback"] is not None else (last_my["feedback"] or "")
            prev_rt = last_my["prev_rating"]   if last_my["prev_rating"]   is not None else last_my["rating"]
            prev_dc = last_my["prev_decision"] if last_my["prev_decision"] is not None else (last_my["decision"] or "")
            cur.execute("""
                UPDATE interviews
                SET feedback=?, rating=?, decision=?,
                    prev_feedback=?, prev_rating=?, prev_decision=?,
                    is_edited=1, edited_at=?, edited_by=?
                WHERE id=?""",
                (feedback, r, decision, prev_fb, prev_rt, prev_dc, now, u["id"], last_my["id"])
            )
            # Update candidate status to reflect the corrected decision
            if decision=="reinterview":
                cur.execute("UPDATE candidates SET status='reinterview' WHERE id=?", (candidate_id,))
            else:
                cur.execute("UPDATE candidates SET status='Assigned' WHERE id=?", (candidate_id,))
            db.commit(); db.close()

            # Notify manager that feedback was corrected
            if c["manager_owner"]:
                notify(c["manager_owner"], "Interview Feedback Updated",
                       f"{c['full_name']}: INTERVIEWER UPDATED feedback. New decision: {decision.upper()}")
            flash("Feedback updated and sent to manager.","message")
            return redirect(url_for("candidates_all"))

        # mode == "new" -> insert a new interview record
        cur.execute("INSERT INTO interviews(candidate_id,interviewer_id,feedback,rating,decision,is_reinterview,created_at) VALUES(?,?,?,?,?,?,?)",
                    (candidate_id,u["id"],feedback,r,decision,1 if decision=="reinterview" else 0,now))
        if decision=="reinterview":
            cur.execute("UPDATE candidates SET status='reinterview' WHERE id=?", (candidate_id,))
        else:
            cur.execute("UPDATE candidates SET status='Assigned' WHERE id=?", (candidate_id,))
        db.commit(); db.close()

        # Notify manager
        if c["manager_owner"]:
            notify(c["manager_owner"], "Interview Feedback Submitted",
                   f"{c['full_name']}: {decision.upper()} (rating: {r or '-'})")
        flash("Feedback submitted to manager.","message"); return redirect(url_for("candidates_all"))

    # GET: render both "new submission" and "edit last" (if exists)
    last_block = ""
    edit_form = ""
    if last_my:
        prev_info = ""
        if last_my["is_edited"]:
            prev_info = f"""
              <div class="tag">Edited at: {(last_my['edited_at'] or '')[:19].replace('T',' ')}</div>
              <div><strong>Previous Decision:</strong> {last_my['prev_decision'] or '-'}<br>
              <strong>Previous Feedback:</strong><div style="white-space:pre-wrap">{(last_my['prev_feedback'] or '').strip() or '-'}</div></div>
            """
        last_block = f"""
        <div class="card">
          <strong>Your Last Submission</strong><br>
          Decision: {last_my['decision']} &nbsp; Rating: {last_my['rating'] or '-'}<br>
          Notes:<div style="white-space:pre-wrap">{(last_my['feedback'] or '').strip() or '-'}</div>
          {prev_info}
        </div>
        """
        edit_form = f"""
        <div class="card">
          <h4>Edit Last Feedback</h4>
          <form method="post">
            <input type="hidden" name="mode" value="edit">
            <div class="row">
              <div class="col"><label>Rating (1-5)</label><input name="rating" placeholder="e.g. 4" value="{last_my['rating'] or ''}"></div>
            </div>
            <label>Decision</label>
            <select name="decision">
              <option value="selected" {'selected' if (last_my['decision']=='selected') else ''}>Selected</option>
              <option value="rejected" {'selected' if (last_my['decision']=='rejected') else ''}>Rejected</option>
              <option value="reinterview" {'selected' if (last_my['decision']=='reinterview') else ''}>Ask Re-Interview</option>
            </select>
            <label>Remarks</label>
            <textarea name="feedback" rows="5" placeholder="Corrected notes for manager">{(last_my['feedback'] or '')}</textarea>
            <div style="margin-top:10px"><button class="btn">Update</button></div>
          </form>
        </div>
        """

    new_form = f"""
    <div class="card">
      <h3>Interviewer Feedback</h3>
      <p><strong>{c['full_name']}</strong> — <span class="tag">{c['post_applied']}</span></p>
      <form method="post">
        <input type="hidden" name="mode" value="new">
        <div class="row">
          <div class="col"><label>Rating (1-5)</label><input name="rating" placeholder="e.g. 4"></div>
        </div>
        <label>Decision</label>
        <select name="decision">
          <option value="selected">Selected</option>
          <option value="rejected">Rejected</option>
          <option value="reinterview">Ask Re-Interview</option>
        </select>
        <label>Remarks</label>
        <textarea name="feedback" rows="5" placeholder="Notes for manager"></textarea>
        <div style="margin-top:10px"><button class="btn">Submit</button> <a class="btn light" href="{url_for('candidates_all')}">Back</a></div>
      </form>
    </div>
    """

    return render_page("Interviewer Feedback", (last_block + edit_form + new_form))

# ---------- Bulk Upload ----------

@app.route("/bulk/sample")
@login_required
@role_required(ROLE_HR,ROLE_ADMIN)
def bulk_sample():
    headers = ["Candidate Id","Salutation","Name","Email","Qualification","Experience (years)","Current designation",
               "Mobile No.","Current Salary","Expected Salary","Current Location","Preferred location","Post applied",
               "Interview Date","Current/Previous company","Region","Status","remarks"]
    wb = Workbook(); ws = wb.active; ws.title = "Candidates"
    for i,h in enumerate(headers, start=1): ws.cell(row=1, column=i).value = h
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name="bulk_sample.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/bulk", methods=["GET","POST"])
@login_required
@role_required(ROLE_HR,ROLE_ADMIN)
def bulk_upload():
    if request.method=="POST":
        file = request.files.get("xlsx")
        if not file or not file.filename.lower().endswith(".xlsx"):
            flash("Please upload an .xlsx file.","error"); return redirect(url_for("bulk_upload"))

        safe = f"bulk_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}.xlsx"
        xpath = os.path.join(UPLOAD_DIR, safe); file.save(xpath)

        try:
            wb = load_workbook(xpath); ws = wb.active
            headers = [ (ws.cell(row=1, column=i).value or "").strip().lower() for i in range(1, ws.max_column+1) ]
            def idx(label):
                l=label.strip().lower()
                return headers.index(l) if l in headers else None

            m = { k:idx(k) for k in [
                "candidate id","salutation","name","email","qualification","experience (years)",
                "current designation","mobile no.","current salary","expected salary","current location",
                "preferred location","post applied","interview date","current/previous company","region","status","remarks"
            ]}

            inserted=0; bad_phone=0; bad_post_or_name=0
            db=get_db(); cur=db.cursor()
            now = datetime.datetime.utcnow().isoformat()
            u=current_user()

            for r in range(2, ws.max_row+1):
                def v(key):
                    ci = m.get(key); return (ws.cell(row=r, column=(ci+1)).value if ci is not None else "") or ""
                post=str(v("post applied")).strip(); full_name=str(v("name")).strip()
                if post not in POSTS or not full_name: bad_post_or_name+=1; continue

                digits = "".join(ch for ch in str(v("mobile no.")).strip() if ch.isdigit())
                if len(digits) != 10: bad_phone+=1; continue

                try: ey=float(v("experience (years)")) if str(v("experience (years)"))!="" else None
                except: ey=None

                cand_code = (str(v("candidate id")).strip() or None)

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

                cid = cur.lastrowid
                if cand_code is None:
                    cand_code = f"DCDC_C{cid}"
                    cur.execute("UPDATE candidates SET candidate_code=? WHERE id=?", (cand_code, cid))

                inserted+=1

                # Notify manager for each inserted candidate
                if manager_id:
                    notify(manager_id, "Candidate Assigned to Your Role",
                           f"{full_name} (ID {cand_code}) assigned to your role.")

            db.commit(); db.close()
            flash(f"Bulk upload complete. Inserted {inserted}. Skipped {bad_post_or_name} (bad name/post), {bad_phone} (invalid phone).","message")
            return redirect(url_for("candidates_all"))
        except Exception as e:
            flash(f"Upload failed: {e}. Supported formats: .xlsx, .xlsm, .xltx, .xltm","error"); return redirect(url_for("bulk_upload"))

    sample_cols = ", ".join([
        "Candidate Id","Salutation","Name","Email","Qualification","Experience (years)","Current designation",
        "Mobile No.","Current Salary","Expected Salary","Current Location","Preferred location","Post applied",
        "Interview Date","Current/Previous company","Region","Status","remarks"
    ])
    body=f"""
    <div class="card" style="max-width:700px;margin:0 auto">
      <h3>Bulk Upload (Excel .xlsx)</h3>
      <p>Expected columns: <span class="tag">{sample_cols}</span></p>
      <p><a class="btn light" href="{url_for('bulk_sample')}">Download Sample Excel</a></p>
      <form method="post" enctype="multipart/form-data">
        <label>Choose .xlsx file</label><input type="file" name="xlsx" accept=".xlsx" required>
        <div style="margin-top:10px"><button class="btn">Upload</button> <a class="btn light" href="{url_for('candidates_all')}">Back</a></div>
      </form>
    </div>
    """
    return render_page("Bulk Upload", body)

# ---------- Finalize / HR join ----------

@app.route("/finalize/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_MANAGER,ROLE_ADMIN)
def finalize_candidate(candidate_id):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c: db.close(); flash("Not found.","error"); return redirect(url_for("dashboard"))
    if u["role"]!=ROLE_ADMIN and c["manager_owner"]!=u["id"]:
        db.close(); flash("You do not own this candidate.","error"); return redirect(url_for("dashboard"))
    cur.execute("""SELECT i.*,u.name interviewer_name FROM interviews i JOIN users u ON u.id=i.interviewer_id
                   WHERE i.candidate_id=? ORDER BY i.id DESC LIMIT 1""",(candidate_id,))
    last=cur.fetchone()

    if request.method=="POST":
        action=request.form.get("action"); remark=request.form.get("remark","").strip()
        now=datetime.datetime.utcnow().isoformat()
        if action == "select":
            cur.execute("""
            UPDATE candidates
            SET status='finalized',
                final_decision='selected',
                final_remark=?,
                finalized_by=?,
                finalized_at=?,
                decision_by=?,
                interviewer_id=NULL
            WHERE id=?""",
            (remark, u["id"], now, u["id"], candidate_id)
            )
        elif action == "reject":
            cur.execute("""
            UPDATE candidates
            SET status='finalized',
                final_decision='rejected',
                final_remark=?,
                finalized_by=?,
                finalized_at=?,
                decision_by=?,
                interviewer_id=NULL
            WHERE id=?""",
            (remark, u["id"], now, u["id"], candidate_id)
            )
        elif action=="reinterview":
            cur.execute("""UPDATE candidates SET status='reinterview', final_decision=NULL, final_remark=?, interviewer_id=NULL WHERE id=?""",
                        (remark,candidate_id))
        else:
            db.close(); flash("Invalid action.","error"); return redirect(url_for("finalize_candidate",candidate_id=candidate_id))
        db.commit(); db.close()

        # Existing notifications: HR creator + interviewer (if any) + manager
        for uid in filter(None, [c["created_by"], c["interviewer_id"], c["manager_owner"]]):
            notify(uid, "Candidate Finalized",
                   f"{c['full_name']} -> {action.upper()}. Remark: {(remark or '-')}")

        # Notify ALL HR users for selected/rejected with name + ID
        if action in ("select","reject"):
            title = "Candidate Selected" if action=="select" else "Candidate Rejected"
            msg = f"{c['full_name']} (ID {c['candidate_code'] or '-'}) was {('SELECTED' if action=='select' else 'REJECTED')} by manager."
            db2 = get_db(); cur2 = db2.cursor()
            cur2.execute("SELECT id FROM users WHERE role='hr'")
            hr_ids = [row["id"] for row in cur2.fetchall()]
            db2.close()
            for hid in hr_ids:
                notify(hid, title, msg)

        flash("Final decision updated.","message"); return redirect(url_for("dashboard"))

    # Show previous vs new remark if interviewer edited
    if not last:
        last_block = "<p>No interview yet.</p>"
    else:
        prev_block = ""
        if last["is_edited"]:
            prev_block = f"""
            <div style="margin-top:8px;padding:8px;border:1px dashed #d1d5db;border-radius:8px">
              <strong>Previous Decision:</strong> {last['prev_decision'] or '-'}<br>
              <strong>Previous Feedback:</strong>
              <div style="white-space:pre-wrap">{(last['prev_feedback'] or '').strip() or '-'}</div>
            </div>
            """
        last_block = f"""
        <div class="card"><strong>Latest Interview</strong><br>
        By: {last['interviewer_name']}<br>Rating: {last['rating'] or '-'} / 5<br>Decision: {last['decision']}<br>
        Notes:<div style="white-space:pre-wrap">{(last['feedback'] or '').strip() or '-'}</div>
        {prev_block}
        </div>"""

    body=f"""
    <div class="card" style="max-width:720px;margin:0 auto">
      <h3>Finalize Candidate</h3>
      <p><strong>{c['full_name']}</strong> — <span class="tag">{c['post_applied']}</span></p>
      {last_block}
      <form method="post">
        <label>Final Remark</label><textarea name="remark" rows="4"></textarea>
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
          <button name="action" value="select" class="btn">Select</button>
          <button name="action" value="reject" class="btn danger">Reject</button>
          <button name="action" value="reinterview" class="btn warn">Re-Interview</button>
          <a class="btn light" href="{url_for('dashboard')}">Cancel</a>
        </div>
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
    WHERE c.status='finalized'
      AND lower(c.final_decision)='selected'
      AND c.hr_join_status IS NULL
    """

    if is_hr_head(u) or u["role"]==ROLE_ADMIN:
        cur.execute(base_sql + " ORDER BY c.finalized_at DESC")
    else:
        cur.execute(base_sql + " AND c.created_by=? ORDER BY c.finalized_at DESC", (u["id"],))

    rows = cur.fetchall(); db.close()

    if not rows:
        body = "<div class='card'><h3>Awaiting Join Status</h3><ul><li>None</li></ul></div>"
        return render_page("HR Actions", body)

    trs = "".join([
        f"""
        <tr>
          <td>{r['full_name']}</td>
          <td>{r['post_applied']}</td>
          <td>{r['finalized_by_name'] or '-'}</td>
          <td style="white-space:pre-wrap">{r['final_remark']}</td>
          <td>{r['finalized_at'] or '-'}</td>
          <td><a class="btn" href="{url_for('hr_join_update', candidate_id=r['id'])}">Mark Join</a></td>
        </tr>
        """ for r in rows
    ])

    body = f"""
    <div class="card">
      <h3>Awaiting Join Status</h3>
      <table>
        <thead>
          <tr>
            <th>Name</th><th>Post</th><th>Finalized By</th>
            <th>Final Remark</th><th>Finalized At</th><th>Action</th>
          </tr>
        </thead>
        <tbody>{trs}</tbody>
      </table>
    </div>
    """
    return render_page("HR Actions", body)

@app.route("/hr/join/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_HR,ROLE_ADMIN)
def hr_join_update(candidate_id):
    u=current_user(); db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)); c=cur.fetchone()
    if not c: db.close(); flash("Not found.","error"); return redirect(url_for("hr_join_queue"))
    if u["role"]==ROLE_HR and not is_hr_head(u) and c["created_by"]!=u["id"]:
        db.close(); flash("You cannot edit another HR's candidate.","error"); return redirect(url_for("hr_join_queue"))
    if c["final_decision"]!="selected" or c["status"]!="finalized":
        db.close(); flash("Only finalized 'Selected' candidates are updatable.","error"); return redirect(url_for("hr_join_queue"))

    cur.execute("SELECT name FROM users WHERE id=?", (c["manager_owner"],)); row_mgr = cur.fetchone()
    manager_name = row_mgr["name"] if row_mgr else "-"
    cur.execute("SELECT name FROM users WHERE id=?", (c["finalized_by"],)); row_fin = cur.fetchone()
    finalized_by_name = row_fin["name"] if row_fin else "-"

    if request.method == "POST":
        st = request.form.get("status")
        reason = request.form.get("reason", "").strip() if st == "not_joined" else None

        if st not in ("joined", "not_joined"):
            db.close(); flash("Invalid status.","error"); return redirect(url_for("hr_join_update", candidate_id=candidate_id))
        if st == "not_joined" and not reason:
            db.close(); flash("Please provide reason for Not Joined.","error"); return redirect(url_for("hr_join_update", candidate_id=candidate_id))

        now = datetime.datetime.utcnow().isoformat()
        cur.execute(
            "UPDATE candidates SET hr_join_status=?, hr_joined_at=?, status='closed', final_remark=? WHERE id=?",
            (st, now, reason if reason else c["final_remark"], candidate_id)
        )
        db.commit(); db.close()

        msg = f"{c['full_name']} join status: {st.upper()}" + (f" (Reason: {reason})" if reason else "")
        for uid in filter(None, [c["manager_owner"], c["finalized_by"], c["created_by"]]):
            notify(uid, "Join Status Updated", msg)

        flash("Join status updated.","message")
        return redirect(url_for("dashboard"))

    body = f"""
    <div class="card" style="max-width:700px;margin:0 auto">
      <h3>HR: Mark Join Status</h3>
      <div class="card section blue">
        <h4>Candidate</h4>
        <p><strong>{c['full_name']}</strong> — <span class="tag">{c['post_applied']}</span></p>
        <p>Manager: <strong>{manager_name}</strong></p>
        <p>Finalized By: <strong>{finalized_by_name}</strong></p>
        <p>Final Remark: <span style="white-space:pre-wrap">{(c['final_remark'] or '-')}</span></p>
      </div>

      <form method="post">
        <label>Status</label>
        <select name="status" id="status" onchange="toggleReason()">
          <option value="joined">Joined</option>
          <option value="not_joined">Not Joined</option>
        </select>

        <div id="reasonBox" style="margin-top:10px; display:none;">
          <label>Reason (if Not Joined)</label>
          <textarea name="reason" rows="3" placeholder="Explain why the candidate did not join"></textarea>
        </div>

        <div style="margin-top:10px">
          <button class="btn">Save</button>
          <a class="btn light" href="{url_for('hr_join_queue')}">Back</a>
        </div>
      </form>
    </div>
    """ + """
    <script>
    function toggleReason(){
      var st = document.getElementById('status').value;
      document.getElementById('reasonBox').style.display = (st === 'not_joined') ? 'block' : 'none';
    }
    </script>
    """
    return render_page("HR Join Update", body)

# ---------- Admin ----------

@app.route("/admin/users", methods=["GET","POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_users():
    db=get_db(); cur=db.cursor()
    if request.method=="POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        role=request.form.get("role","").strip()
        manager_id=request.form.get("manager_id","").strip()
        passcode=request.form.get("passcode","").strip()
        if not name or not email or role not in (ROLE_ADMIN,ROLE_VP,ROLE_HR,ROLE_MANAGER,ROLE_INTERVIEWER) or not passcode or len(passcode)<8:
            flash("Provide name, email, role, and a passcode (min 8 chars).","error")
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
    opts_role="".join([f"<option value='{r}'>{r}</option>" for r in [ROLE_ADMIN,ROLE_VP,ROLE_HR,ROLE_MANAGER,ROLE_INTERVIEWER]])
    cur.execute("SELECT id,name FROM users WHERE role IN ('manager') ORDER BY name"); mgrs=cur.fetchall()
    opts_mgr="<option value=''>—</option>" + "".join([f"<option value='{m['id']}'>{m['name']}</option>" for m in mgrs])
    rows="".join([f"<tr><td>{u['id']}</td><td>{u['name']}</td><td>{u['email']}</td><td>{u['role']}</td><td>{u['manager_id'] or '-'}</td></tr>" for u in users])
    db.close()
    body=f"""
    <div class="card">
      <h3>Add User</h3>
      <form method="post">
        <div class="row">
          <div class="col"><label>Name</label><input name="name" required></div>
          <div class="col"><label>Email</label><input name="email" required></div>
          <div class="col"><label>Role</label><select name="role">{opts_role}</select></div>
          <div class="col"><label>Manager (if interviewer)</label><select name="manager_id">{opts_mgr}</select></div>
          <div class="col"><label>Passcode</label><input name="passcode" required></div>
        </div>
        <div style="margin-top:10px"><button class="btn">Create</button></div>
      </form>
    </div>
    <div class="card">
      <h3>Users</h3>
      <table><thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Role</th><th>Manager</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=5>No users</td></tr>'}</tbody></table>
    </div>
    <div class="card">
      <h3>Password Reset Requests</h3>
      <p><a class="btn" href="{url_for('admin_resets')}">Manage Resets</a></p>
    </div>
    """
    return render_page("Admin: Users", body)

@app.route("/admin/resets", methods=["GET","POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_resets():
    db=get_db(); cur=db.cursor()
    if request.method=="POST":
        rid=request.form.get("rid",""); newp=request.form.get("new","")
        if rid.isdigit() and len(newp)>=8:
            cur.execute("SELECT * FROM password_resets WHERE id=? AND state='open'", (int(rid),))
            row=cur.fetchone()
            if row:
                cur.execute("UPDATE users SET passcode=? WHERE email=?", (generate_password_hash(newp),row["user_email"]))
                cur.execute("""UPDATE password_resets SET state='resolved', resolved_at=?, resolver_id=?, new_passcode=NULL WHERE id=?""",
                            (datetime.datetime.utcnow().isoformat(), current_user()["id"], int(rid)))
                db.commit();
                try:
                    send_email(row["user_email"], "HMS New Passcode", f"<p>Your new passcode is: <b>{newp}</b></p>")
                except Exception:
                    pass
                flash("Reset resolved and passcode updated.","message")
            else:
                flash("Reset not found or already resolved.","error")
        else:
            flash("Provide valid request ID and a new passcode (>=8 chars).","error")

    cur.execute("SELECT * FROM password_resets ORDER BY created_at DESC")
    rows=cur.fetchall(); db.close()
    def tr(r):
        return f"<tr><td>{r['id']}</td><td>{r['user_email']}</td><td>{r['state']}</td><td>{r['created_at'][:19].replace('T',' ')}</td><td>{r['resolved_at'][:19].replace('T',' ') if r['resolved_at'] else '-'}</td></tr>"
    table="".join([tr(r) for r in rows]) or "<tr><td colspan=5>No requests</td></tr>"
    body=f"""
    <div class="card">
      <h3>Password Reset Requests</h3>
      <table><thead><tr><th>ID</th><th>Email</th><th>State</th><th>Created</th><th>Resolved</th></tr></thead>
      <tbody>{table}</tbody></table>
    </div>
    <div class="card" style="max-width:560px">
      <h3>Resolve a Request</h3>
      <form method="post">
        <label>Request ID</label><input name="rid" required>
        <label>New Passcode</label><input name="new" required>
        <div style="margin-top:10px"><button class="btn">Set New Passcode</button> <a class="btn light" href="{url_for('admin_users')}">Back</a></div>
      </form>
    </div>
    """
    return render_page("Admin: Reset Requests", body)

# ---------- Run ----------

if __name__=="__main__":
    init_db()
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port, debug=True)
