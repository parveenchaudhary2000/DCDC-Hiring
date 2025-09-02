# app.py — Hiring Management System (Flask + SQLite + openpyxl)
import os, sqlite3, datetime, secrets, io, json
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash, send_from_directory, send_file
from openpyxl import load_workbook, Workbook

APP_TITLE = "Hiring Management System (HMS)"
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "hms.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

SECRET_KEY = os.environ.get("HMS_SECRET", "change-this-in-prod")
LOGO_FILENAME = "logo.png"
POSTS = ["Trainee","Junior Technician","Senior Technician","Staff Nurse","Doctor","DMO","Others"]

ROLE_ADMIN="admin"; ROLE_VP="vp"; ROLE_HR="hr"; ROLE_MANAGER="manager"; ROLE_INTERVIEWER="interviewer"
ALLOWED_CV_EXTS = {".pdf",".doc",".docx"}

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

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

    c.execute("""
    CREATE TABLE IF NOT EXISTS interviews(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      candidate_id INTEGER NOT NULL,
      interviewer_id INTEGER NOT NULL,
      feedback TEXT,
      rating INTEGER,
      decision TEXT,
      is_reinterview INTEGER DEFAULT 0,
      created_at TEXT NOT NULL
    );""")

    c.execute("SELECT COUNT(*) AS ct FROM users")
    if c.fetchone()["ct"] == 0:
        now = datetime.datetime.utcnow().isoformat()
        seed = [
          ("Mr. Parveen Chaudhary","clinicalanalyst@dcdc.co.in",ROLE_ADMIN,None,"admin123"),
          ("Mr. Deepak Agarwal","drdeepak@dcdc.co.in",ROLE_VP,None,"vp123"),

          ("Ms. Barkha","jobs@dcdc.co.in",ROLE_HR,None,"hr123"),
          ("Deepika","hiring@dcdc.co.in",ROLE_HR,None,"hrdp123"),
          ("Karishma","hr_hiring@dcdc.co.in",ROLE_HR,None,"hrka123"),
          ("Kajal","hiring_1@dcdc.co.in",ROLE_HR,None,"hrkj123"),
          ("Sneha","hiring_2@dcdc.co.in",ROLE_HR,None,"hrsn123"),
          ("Ravi","hiring_3@dcdc.co.in",ROLE_HR,None,"hrrv123"),
          ("Shivani","recruitments@dcdc.co.in",ROLE_HR,None,"hrsv123"),
          ("Udita","careers@dcdc.co.in",ROLE_HR,None,"hrud123"),

          ("Dr. Yasir Anis","clinical_manager@dcdc.co.in",ROLE_MANAGER,None,"yasir123"),
          ("Ms. Prachi","infectioncontroller@dcdc.co.in",ROLE_INTERVIEWER,None,"prachi123"),
          ("Mr. Shaikh Saadi","dialysis.coord@dcdc.co.in",ROLE_MANAGER,None,"saadi123"),
          ("Ms. Pankaja","rmclinical_4@dcdc.co.in",ROLE_INTERVIEWER,None,"pankaja123"),
          ("Mr. Yekula Bhanu Prakash","rmclinical_6@dcdc.co.in",ROLE_INTERVIEWER,None,"bhanu123"),
          ("Mr. Rohit","clinical_therapist@dcdc.co.in",ROLE_INTERVIEWER,None,"rohit123"),
        ]
        for n,e,r,m,p in seed:
            c.execute("INSERT INTO users(name,email,role,manager_id,passcode,created_at) VALUES(?,?,?,?,?,?)",(n,e,r,m,p,now))
        conn.commit()
        # link interviewers to managers
        def uid(em):
            c.execute("SELECT id FROM users WHERE email=?", (em,)); rr=c.fetchone(); return rr["id"] if rr else None
        yasir = uid("clinical_manager@dcdc.co.in")
        saadi = uid("dialysis.coord@dcdc.co.in")
        c.execute("UPDATE users SET manager_id=? WHERE email='infectioncontroller@dcdc.co.in'", (yasir,))
        for em in ("rmclinical_4@dcdc.co.in","rmclinical_6@dcdc.co.in","clinical_therapist@dcdc.co.in"):
            c.execute("UPDATE users SET manager_id=? WHERE email=?", (saadi, em))
        conn.commit()
    conn.close()

def user_id_by_email(email:str):
    db=get_db(); cur=db.cursor()
    cur.execute("SELECT id FROM users WHERE email=?", (email,))
    r=cur.fetchone(); db.close()
    return r["id"] if r else None

def next_candidate_code():
    db = get_db(); cur = db.cursor()
    cur.execute("SELECT MAX(id) FROM candidates")
    row = cur.fetchone(); db.close()
    return f"DCDC_C{(row[0] or 0)+1}"

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

# ---------- UI ----------
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
 .btn.light{background:#fff;color:var(--vein-blue)}
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
      {% endif %}
      {% if user['role'] in ['hr','admin'] %}
        <a href="{{ url_for('hr_join_queue') }}">HR Actions</a>
      {% endif %}
      {% if user['role'] in ['admin'] %}
        <a href="{{ url_for('admin_users') }}">Admin</a>
      {% endif %}
      <a href="{{ url_for('profile') }}">Profile</a>
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

# serve logo
@app.route("/brand-logo")
def brand_logo():
    path = os.path.join(BASE_DIR, LOGO_FILENAME)
    if os.path.exists(path):
        return send_from_directory(BASE_DIR, LOGO_FILENAME)
    from flask import Response
    return Response(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\n\x00\x01\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;", mimetype="image/gif")

# ---------- Auth ----------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email = request.form.get("email","").strip().lower()
        passcode = request.form.get("passcode","").strip()
        db=get_db(); cur=db.cursor()
        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        u = cur.fetchone(); db.close()
        if u and u["passcode"]==passcode:
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

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

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
        if not new or len(new)<4: flash("New passcode must be at least 4 chars.","error")
        else:
            db=get_db(); cur=db.cursor()
            cur.execute("SELECT passcode FROM users WHERE id=?", (u["id"],))
            if cur.fetchone()["passcode"]!=old:
                db.close(); flash("Old passcode incorrect.","error"); return redirect(url_for("profile"))
            cur.execute("UPDATE users SET passcode=? WHERE id=?", (new,u["id"]))
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
    q_post   = (request.args.get("post") or "").strip()
    q_region = (request.args.get("region") or "").strip()
    q_from   = (request.args.get("from") or "").strip()
    q_to     = (request.args.get("to") or "").strip()

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
    if q_post:   where.append("post_applied=?"); args.append(q_post)
    if q_region: where.append("assigned_region=?"); args.append(q_region)
    if q_from:   where.append("date(substr(created_at,1,10))>=date(?)"); args.append(q_from)
    if q_to:     where.append("date(substr(created_at,1,10))<=date(?)"); args.append(q_to)

    WHERE = " AND ".join(where)

    def scalar(sql, a=()):
        cur.execute(sql, a); r = cur.fetchone(); return r[0] if r else 0

    total    = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE}", args)
    selected = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND lower(final_decision)='selected'", args)
    rejected = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND lower(final_decision)='rejected'", args)
    assigned = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND status='Assigned'", args)
    joined   = scalar(f"SELECT COUNT(*) FROM candidates WHERE {WHERE} AND hr_join_status='joined'", args)
    status_labels = ["Selected", "Rejected", "Assigned"]
    status_counts = [selected, rejected, assigned]

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
    line_sel    = [ sel_map.get(m,0) for m in months ]
    line_join   = [ join_map.get(m,0) for m in months ]

    db.close()

    opts_status = "".join([f"<option value='{s}' {'selected' if q_status==s else ''}>{s or 'All'}</option>"
                           for s in ["","Pending","Assigned","reinterview","finalized","Selected","Rejected","Joined"]])
    opts_post   = "<option value=''>All</option>" + "".join([f"<option value='{p}' {'selected' if q_post==p else ''}>{p}</option>" for p in posts])
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
      const lineLabels   = {json.dumps(line_labels)};
      const lineSel      = {json.dumps(line_sel)};
      const lineJoin     = {json.dumps(line_join)};

      // Pie chart with percentages
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

      // Region Bar
      new Chart(document.getElementById('regionBar'), {{
        type: 'bar',
        data: {{ labels: regionLabels, datasets: [{{ label: 'Candidates', data: regionCounts }}] }},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
      }});

      // Line: Selected vs Joined
      new Chart(document.getElementById('selJoinLine'), {{
        type: 'line',
        data: {{
          labels: lineLabels,
          datasets: [
            {{ label: 'Selected', data: lineSel, tension: 0.35 }},
            {{ label: 'Joined',   data: lineJoin, tension: 0.35 }}
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

# ---------- Candidates ----------
@app.route("/candidates")
@login_required
def candidates_all():
    u=current_user(); db=get_db(); cur=db.cursor()
    if u["role"] in (ROLE_ADMIN,ROLE_VP) or (u["role"]==ROLE_HR and is_hr_head(u)):
        cur.execute("SELECT * FROM candidates ORDER BY created_at DESC")
    elif u["role"]==ROLE_HR:
        cur.execute("SELECT * FROM candidates WHERE created_by=? ORDER BY created_at DESC",(u["id"],))
    elif u["role"]==ROLE_MANAGER:
        cur.execute("SELECT * FROM candidates WHERE manager_owner=? ORDER BY created_at DESC",(u["id"],))
    else:
        cur.execute("SELECT * FROM candidates WHERE interviewer_id=? ORDER BY created_at DESC",(u["id"],))
    rows = cur.fetchall(); db.close()

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

    rows_html = "".join([
        (f"<tr>"
         f"<td>{(r['candidate_code'] or '-')}</td>"
         f"<td>{r['full_name']}</td>"
         f"<td>{r['post_applied']}</td>"
         f"<td><span class='tag'>{r['status']}</span></td>"
         f"<td>{(r['final_decision'] or '-')}</td>"
         f"<td>{(r['hr_join_status'] or '-')}</td>"
         f"<td>{r['created_at'][:19].replace('T',' ')}</td>"
         f"<td>{('<a href=\"' + url_for('download_cv', path=r['cv_path']) + '\">CV</a>') if r['cv_path'] else '-'}</td>"
         f"<td>{actions(r)}</td>"
         f"</tr>")
        for r in rows
    ]) or "<tr><td colspan=9>No data</td></tr>"


    body = f"""
        <div class="card"><h3>All Candidates</h3>
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

        candidate_code = (f.get("candidate_code") or "").strip() or next_candidate_code()

        fields = dict(
            candidate_code=candidate_code,
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
            fields["candidate_code"],fields["salutation"],fields["full_name"],fields["email"],fields["qualification"],ey,fields["current_designation"],fields["phone"],cv_path,fields["current_salary"],fields["expected_salary"],fields["current_location"],fields["preferred_location"],fields["post_applied"],fields["interview_date"],fields["current_previous_company"],fields["assigned_region"],status,None,fields["remarks"],u["id"],now,None,manager_id,None,None,None,None,None,None
        ))
        db.commit(); db.close()
        flash(f"Candidate added (ID: {fields['candidate_code']}).","message")
        return redirect(url_for("dashboard"))

    default_code = next_candidate_code()
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
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    c=cur.fetchone()
    if not c: db.close(); flash("Not found.","error"); return redirect(url_for("candidates_all"))
    if u["role"]!=ROLE_ADMIN and c["manager_owner"]!=u["id"]:
        db.close(); flash("You do not own this candidate.","error"); return redirect(url_for("candidates_all"))

    if request.method=="POST":
        iid = request.form.get("interviewer_id","").strip()
        if not iid.isdigit():
            db.close(); flash("Choose an interviewer.","error"); return redirect(url_for("assign_candidate",candidate_id=candidate_id))
        cur.execute("UPDATE candidates SET interviewer_id=?, status='Assigned' WHERE id=?", (int(iid), candidate_id))
        db.commit(); db.close(); flash("Assigned to interviewer.","message"); return redirect(url_for("candidates_all"))



    ivs = interviewers_for_manager(u["id"])
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
    db.close(); return render_page("Assign Interviewer", body)

@app.route("/assign/bulk", methods=["GET", "POST"])
@login_required
@role_required(ROLE_MANAGER, ROLE_ADMIN)
def bulk_assign():
        u = current_user()
        db = get_db();
        cur = db.cursor()

        # managers can only assign their own candidates (admins see all)
        base_where = "1=1"
        args = []
        if u["role"] != ROLE_ADMIN:
            base_where = "manager_owner=?"
            args = [u["id"]]

        # GET → show selectable candidates + interviewer list
        if request.method == "GET":
            # Show candidates that still need (re)assignment
            cur.execute(f"""
                SELECT id, candidate_code, full_name, post_applied, status,
                       COALESCE((SELECT name FROM users uu WHERE uu.id=c.interviewer_id), '-') as current_iv
                FROM candidates c
                WHERE {base_where}
                  AND (c.interviewer_id IS NULL OR c.status IN ('Assigned','reinterview'))
                ORDER BY datetime(c.created_at) DESC
            """, args)
            rows = cur.fetchall()

            # Which interviewers can this manager assign to?
            ivs = interviewers_for_manager(u["id"]) if u["role"] != ROLE_ADMIN else []
            # If admin, allow choosing the owning manager first (optional: keep simple by listing all interviewers grouped by manager)
            iv_opts = "".join([f"<option value='{i['id']}'>{i['name']}</option>" for i in ivs]) if ivs else ""

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
                    <tr><th></th><th>ID</th><th>Name</th><th>Post</th><th>Status</th><th>Current Interviewer</th></tr>
                  </thead>
                  <tbody>{trs}</tbody>
                </table>

                <div class="sticky-actions">
                  <button class="btn">Assign Selected</button>
                  <a class="btn light" href="{url_for('candidates_all')}">Cancel</a>
                </div>
              </form>
            </div>
            """
            db.close()
            return render_page("Bulk Assign", body)

        # POST → update selected ids
        iid = request.form.get("interviewer_id", "").strip()
        ids = request.form.getlist("ids")

        if not iid.isdigit() or not ids:
            db.close()
            flash("Pick an interviewer and at least one candidate.", "error")
            return redirect(url_for("bulk_assign"))

        # Secure: only update rows this manager owns (unless admin)
        placeholders = ",".join("?" for _ in ids)
        params = [int(iid)] + ids
        owner_guard = ""
        if u["role"] != ROLE_ADMIN:
            owner_guard = " AND manager_owner=?"
            params.append(u["id"])

        cur.execute(f"""
            UPDATE candidates
            SET interviewer_id=?, status='Assigned'
            WHERE id IN ({placeholders}){owner_guard}
        """, params)
        db.commit();
        db.close()
        flash("Candidates assigned.", "message")
        return redirect(url_for("candidates_all"))

# ---------- Interviewer: feedback / decision (to manager) ----------
@app.route("/interview/<int:candidate_id>", methods=["GET","POST"])
@login_required
@role_required(ROLE_INTERVIEWER)
def interview_feedback(candidate_id):
    u=current_user()
    db=get_db(); cur=db.cursor()
    cur.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    c=cur.fetchone()
    if not c or c["interviewer_id"]!=u["id"]:
        db.close(); flash("Not allowed.","error"); return redirect(url_for("candidates_all"))

    if request.method=="POST":
        decision = request.form.get("decision","").strip().lower()
        rating   = request.form.get("rating","").strip()
        feedback = request.form.get("feedback","").strip()
        try: r = int(rating);
        except: r = None
        now = datetime.datetime.utcnow().isoformat()
        if decision not in ("selected","rejected","reinterview"):
            db.close(); flash("Choose a decision.","error"); return redirect(url_for('interview_feedback',candidate_id=candidate_id))
        cur.execute("INSERT INTO interviews(candidate_id,interviewer_id,feedback,rating,decision,is_reinterview,created_at) VALUES(?,?,?,?,?,?,?)",
                    (candidate_id,u["id"],feedback,r,decision,1 if decision=="reinterview" else 0,now))
        if decision=="reinterview":
            cur.execute("UPDATE candidates SET status='reinterview' WHERE id=?", (candidate_id,))
        else:
            cur.execute("UPDATE candidates SET status='Assigned' WHERE id=?", (candidate_id,))
        db.commit(); db.close()
        flash("Feedback submitted to manager.","message"); return redirect(url_for("candidates_all"))

    body=f"""
    <div class="card" style="max-width:680px;margin:0 auto">
      <h3>Interviewer Feedback</h3>
      <p><strong>{c['full_name']}</strong> — <span class="tag">{c['post_applied']}</span></p>
      <form method="post">
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
    db.close(); return render_page("Interviewer Feedback", body)

# ---------- Bulk Upload ----------
@app.route("/bulk/sample")
@login_required
@role_required(ROLE_HR,ROLE_ADMIN)
def bulk_sample():
    headers = ["Candidate Id","Salutation","Name","Email","Qualification","Experience (years)","Current  designation",
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
            headers = [ (ws.cell(row=1,col=i).value or "").strip().lower() for i in range(1, ws.max_column+1) ]
            def idx(label):
                l=label.strip().lower()
                return headers.index(l) if l in headers else None

            m = { k:idx(k) for k in [
                "candidate id","salutation","name","email","qualification","experience (years)",
                "current  designation","mobile no.","current salary","expected salary","current location",
                "preferred location","post applied","interview date","current/previous company","region","status","remarks"
            ]}

            inserted=0; bad_phone=0; bad_post_or_name=0
            db=get_db(); cur=db.cursor()
            now = datetime.datetime.utcnow().isoformat()
            u=current_user()

            cur.execute("SELECT MAX(id) FROM candidates"); next_base = (cur.fetchone()[0] or 0)

            for r in range(2, ws.max_row+1):
                def v(key):
                    ci = m.get(key);
                    return (ws.cell(row=r, col=ci+1).value if ci is not None else "") or ""
                post=str(v("post applied")).strip(); full_name=str(v("name")).strip()
                if post not in POSTS or not full_name: bad_post_or_name+=1; continue

                digits = "".join(ch for ch in str(v("mobile no.")).strip() if ch.isdigit())
                if len(digits) != 10: bad_phone+=1; continue

                try: ey=float(v("experience (years)")) if str(v("experience (years)"))!="" else None
                except: ey=None

                cand_code = str(v("candidate id")).strip()
                if not cand_code: next_base += 1; cand_code = f"DCDC_C{next_base}"

                manager_id = manager_for_post(post); status = "Assigned"
                cur.execute("""
                  INSERT INTO candidates(candidate_code,salutation,full_name,email,qualification,experience_years,current_designation,phone,cv_path,current_salary,expected_salary,current_location,preferred_location,post_applied,interview_date,current_previous_company,assigned_region,status,decision_by,remarks,created_by,created_at,interviewer_id,manager_owner,final_decision,final_remark,finalized_by,finalized_at,hr_join_status,hr_joined_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,(
                    cand_code, str(v("salutation")).strip(), full_name, str(v("email")).strip(),
                    str(v("qualification")).strip(), ey, str(v("current  designation")).strip(), digits,
                    None, str(v("current salary")).strip(), str(v("expected salary")).strip(), str(v("current location")).strip(),
                    str(v("preferred location")).strip(), post, str(v("interview date")).strip(), str(v("current/previous company")).strip(),
                    str(v("region")).strip(), status, None, str(v("remarks")).strip(),
                    u["id"], now, None, manager_id, None, None, None, None, None, None
                ))
                inserted+=1

            db.commit(); db.close()
            flash(f"Bulk upload complete. Inserted {inserted}. Skipped {bad_post_or_name} (bad name/post), {bad_phone} (invalid phone).","message")
            return redirect(url_for("candidates_all"))
        except Exception as e:
            flash(f"Upload failed: {e}. Supported formats: .xlsx, .xlsm, .xltx, .xltm","error"); return redirect(url_for("bulk_upload"))

    sample_cols = ", ".join([
        "Candidate Id","Salutation","Name","Email","Qualification","Experience (years)","Current  designation",
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
        db.commit(); db.close(); flash("Final decision updated.","message"); return redirect(url_for("dashboard"))

    last_block = "<p>No interview yet.</p>" if not last else f"""
    <div class="card"><strong>Latest Interview</strong><br>
      By: {last['interviewer_name']}<br>Rating: {last['rating'] or '-'} / 5<br>Decision: {last['decision']}<br>
      Notes:<div style="white-space:pre-wrap">{(last['feedback'] or '').strip() or '-'}</div>
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
    db.close(); return render_page("Finalize", body)

@app.route("/hr/queue")
@login_required
@role_required(ROLE_HR, ROLE_ADMIN)
def hr_join_queue():
    u = current_user()
    db = get_db(); cur = db.cursor()

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

    # fetch manager and finalizer names for context
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
    db.close(); return render_page("HR Join Update", body)

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
        if not name or not email or role not in (ROLE_ADMIN,ROLE_VP,ROLE_HR,ROLE_MANAGER,ROLE_INTERVIEWER) or not passcode:
            flash("Provide name, email, role, passcode.","error")
        else:
            mid=int(manager_id) if manager_id.isdigit() else None
            try:
                cur.execute("INSERT INTO users(name,email,role,manager_id,passcode,created_at) VALUES(?,?,?,?,?,?)",
                            (name,email,role,mid,passcode,datetime.datetime.utcnow().isoformat()))
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
        if rid.isdigit() and len(newp)>=4:
            cur.execute("SELECT * FROM password_resets WHERE id=? AND state='open'", (int(rid),))
            row=cur.fetchone()
            if row:
                cur.execute("UPDATE users SET passcode=? WHERE email=?", (newp,row["user_email"]))
                cur.execute("""UPDATE password_resets SET state='resolved', resolved_at=?, resolver_id=?, new_passcode=? WHERE id=?""",
                            (datetime.datetime.utcnow().isoformat(), current_user()["id"], newp, int(rid)))
                db.commit(); flash("Reset resolved and passcode updated.","message")
            else:
                flash("Reset not found or already resolved.","error")
        else:
            flash("Provide valid request ID and a new passcode (>=4 chars).","error")

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
