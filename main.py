import streamlit as st
import sqlite3
import random
import hashlib
from datetime import datetime, date, time as dtime
import time
import os

# ─── Database Setup ───────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camp.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pin_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            active INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            challenge_id INTEGER NOT NULL,
            assigned_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (challenge_id) REFERENCES challenges(id),
            UNIQUE(user_id, challenge_id, assigned_date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS forfeits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            forfeit_date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, forfeit_date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Create default admin "Charles" with pin 000 if not exists
    admin_hash = hashlib.sha256("000".encode()).hexdigest()
    try:
        c.execute("INSERT INTO users (username, pin_hash, is_admin) VALUES (?, ?, 1)",
                  ("Charles", admin_hash))
    except sqlite3.IntegrityError:
        pass

    conn.commit()
    conn.close()

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

# ─── User Functions ───────────────────────────────────────────────────────────

def register_user(username: str, pin: str) -> tuple[bool, str]:
    if len(pin) != 3 or not pin.isdigit():
        return False, "PIN must be exactly 3 digits."
    if not username.strip():
        return False, "Username can't be empty bruzz."
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, pin_hash) VALUES (?, ?)",
                     (username.strip(), hash_pin(pin)))
        conn.commit()
        return True, "Account created! You're in 🔥"
    except sqlite3.IntegrityError:
        return False, f"Username '{username}' is already taken. Pick something else 💀"
    finally:
        conn.close()

def login_user(username: str, pin: str) -> tuple[bool, dict | str]:
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ? AND pin_hash = ?",
                        (username.strip(), hash_pin(pin))).fetchone()
    conn.close()
    if user:
        return True, dict(user)
    return False, "Wrong username or PIN 🥷"

# ─── Challenge Functions ──────────────────────────────────────────────────────

def add_challenge(title: str, description: str, created_by: str) -> tuple[bool, str]:
    if not title.strip():
        return False, "Challenge needs a title."
    conn = get_db()
    conn.execute("INSERT INTO challenges (title, description, created_by) VALUES (?, ?, ?)",
                 (title.strip(), description.strip(), created_by))
    conn.commit()
    conn.close()
    return True, "Challenge added 🔥"

def get_all_challenges():
    conn = get_db()
    rows = conn.execute("SELECT * FROM challenges WHERE active = 1").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_challenge(challenge_id: int):
    conn = get_db()
    conn.execute("UPDATE challenges SET active = 0 WHERE id = ?", (challenge_id,))
    conn.commit()
    conn.close()

def get_daily_assignments(user_id: int, today: str):
    conn = get_db()
    rows = conn.execute("""
        SELECT da.*, c.title, c.description
        FROM daily_assignments da
        JOIN challenges c ON da.challenge_id = c.id
        WHERE da.user_id = ? AND da.assigned_date = ?
        ORDER BY da.id
    """, (user_id, today)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def assign_daily_challenges(user_id: int, today: str):
    """Assign 6 random challenges for the day if not already assigned."""
    existing = get_daily_assignments(user_id, today)
    if existing:
        return existing

    all_challenges = get_all_challenges()
    if len(all_challenges) < 6:
        return []  # Not enough challenges in the pool

    chosen = random.sample(all_challenges, min(6, len(all_challenges)))
    conn = get_db()
    for ch in chosen:
        try:
            conn.execute("""
                INSERT INTO daily_assignments (user_id, challenge_id, assigned_date, status)
                VALUES (?, ?, ?, 'pending')
            """, (user_id, ch['id'], today))
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return get_daily_assignments(user_id, today)

def complete_challenge(assignment_id: int):
    conn = get_db()
    conn.execute("""
        UPDATE daily_assignments
        SET status = 'completed', completed_at = datetime('now')
        WHERE id = ? AND status = 'pending'
    """, (assignment_id,))
    conn.commit()
    conn.close()

def reject_challenge(assignment_id: int):
    conn = get_db()
    conn.execute("""
        UPDATE daily_assignments
        SET status = 'rejected'
        WHERE id = ? AND status = 'pending'
    """, (assignment_id,))
    conn.commit()
    conn.close()

def unreject_challenge(assignment_id: int):
    conn = get_db()
    conn.execute("""
        UPDATE daily_assignments
        SET status = 'pending'
        WHERE id = ? AND status = 'rejected'
    """, (assignment_id,))
    conn.commit()
    conn.close()

def uncomplete_challenge(assignment_id: int):
    conn = get_db()
    conn.execute("""
        UPDATE daily_assignments
        SET status = 'pending', completed_at = NULL
        WHERE id = ? AND status = 'completed'
    """, (assignment_id,))
    conn.commit()
    conn.close()

# ─── Forfeit Check ────────────────────────────────────────────────────────────

def check_forfeits_for_date(check_date: str):
    """Check all users for forfeits on a given date."""
    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    for user in users:
        assignments = conn.execute("""
            SELECT status FROM daily_assignments
            WHERE user_id = ? AND assigned_date = ?
        """, (user['id'], check_date)).fetchall()

        if not assignments:
            continue

        completed_count = sum(1 for a in assignments if a['status'] == 'completed')
        already_forfeited = conn.execute("""
            SELECT id FROM forfeits WHERE user_id = ? AND forfeit_date = ?
        """, (user['id'], check_date)).fetchone()

        if completed_count < 3 and not already_forfeited:
            # Check if the day is over (past midnight)
            today = date.today().isoformat()
            if check_date < today:
                conn.execute("INSERT INTO forfeits (user_id, forfeit_date) VALUES (?, ?)",
                             (user['id'], check_date))
                conn.execute("INSERT INTO notifications (message) VALUES (?)",
                             (f"⚠️ {user['username']} has forfeited on {check_date}!",))
    conn.commit()
    conn.close()

def get_notifications(limit=20):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─── Leaderboard ──────────────────────────────────────────────────────────────

def get_leaderboard():
    conn = get_db()
    rows = conn.execute("""
        SELECT
            u.username,
            COALESCE(completed.cnt, 0) as challenges_completed,
            COALESCE(forfeits.cnt, 0) as forfeit_count
        FROM users u
        LEFT JOIN (
            SELECT user_id, COUNT(*) as cnt
            FROM daily_assignments WHERE status = 'completed'
            GROUP BY user_id
        ) completed ON u.id = completed.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as cnt
            FROM forfeits GROUP BY user_id
        ) forfeits ON u.id = forfeits.user_id
        ORDER BY challenges_completed DESC, forfeit_count ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─── Admin: Get all users ────────────────────────────────────────────────────

def get_all_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY username").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_user(user_id: int):
    conn = get_db()
    conn.execute("DELETE FROM daily_assignments WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM forfeits WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    conn.commit()
    conn.close()

# ─── Streamlit App ────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Camp Challenge Tracker 🏕️", page_icon="🏕️", layout="wide")
    init_db()

    # Run forfeit checks for yesterday
    from datetime import timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    check_forfeits_for_date(yesterday)

    # Custom CSS
    st.markdown("""
    <style>
        .stApp { }
        div[data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%); }
        div[data-testid="stSidebar"] * { color: #e0e0e0 !important; }
        .challenge-card {
            background: linear-gradient(135deg, #667eea22 0%, #764ba222 100%);
            border: 1px solid #667eea55;
            border-radius: 12px;
            padding: 16px;
            margin: 8px 0;
        }
        .completed-card {
            background: linear-gradient(135deg, #2ecc7122 0%, #27ae6022 100%);
            border: 1px solid #2ecc7155;
            border-radius: 12px;
            padding: 16px;
            margin: 8px 0;
        }
        .rejected-card {
            background: linear-gradient(135deg, #e74c3c22 0%, #c0392b22 100%);
            border: 1px solid #e74c3c55;
            border-radius: 12px;
            padding: 16px;
            margin: 8px 0;
        }
        .forfeit-card {
            background: linear-gradient(135deg, #e74c3c33 0%, #c0392b33 100%);
            border: 1px solid #e74c3c;
            border-radius: 12px;
            padding: 16px;
            margin: 8px 0;
        }
        .big-number { font-size: 2.5rem; font-weight: 800; }
        .notification-box {
            background: #ff990022;
            border-left: 4px solid #ff9900;
            padding: 10px 16px;
            margin: 4px 0;
            border-radius: 0 8px 8px 0;
        }
    </style>
    """, unsafe_allow_html=True)

    # Session state
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.user = None

    if not st.session_state.logged_in:
        show_login_page()
    else:
        show_main_app()

def show_login_page():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("# 🏕️ Camp Challenge Tracker")
        st.markdown("##### Log in or create an account to get started")

        tab_login, tab_register = st.tabs(["🔑 Login", "📝 Register"])

        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username", placeholder="Enter your camp name")
                pin = st.text_input("3-Digit PIN", type="password", max_chars=3, placeholder="e.g. 123")
                submitted = st.form_submit_button("Log In", use_container_width=True)
                if submitted:
                    success, result = login_user(username, pin)
                    if success:
                        st.session_state.logged_in = True
                        st.session_state.user = result
                        st.rerun()
                    else:
                        st.error(result)

        with tab_register:
            with st.form("register_form"):
                new_username = st.text_input("Choose a Username", placeholder="Pick something unique")
                new_pin = st.text_input("Choose a 3-Digit PIN", type="password", max_chars=3, placeholder="e.g. 456")
                confirm_pin = st.text_input("Confirm PIN", type="password", max_chars=3, placeholder="Same PIN again")
                reg_submitted = st.form_submit_button("Create Account", use_container_width=True)
                if reg_submitted:
                    if new_pin != confirm_pin:
                        st.error("PINs don't match 💀")
                    else:
                        success, msg = register_user(new_username, new_pin)
                        if success:
                            st.success(msg)
                        else:
                            st.error(msg)

def show_main_app():
    user = st.session_state.user
    is_admin = user.get('is_admin', 0) == 1

    # Sidebar
    with st.sidebar:
        st.markdown(f"### 👋 Hey, **{user['username']}**!")
        if is_admin:
            st.markdown("🛡️ **Admin**")
        st.divider()

        # Notifications
        notifications = get_notifications(10)
        if notifications:
            st.markdown("### 🔔 Notifications")
            for n in notifications:
                st.markdown(f"""<div class="notification-box">{n['message']}<br>
                    <small style="opacity:0.6">{n['created_at']}</small></div>""",
                    unsafe_allow_html=True)
        st.divider()
        if st.button("🚪 Log Out", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.user = None
            st.rerun()

    # Main tabs
    if is_admin:
        tabs = st.tabs(["⚔️ My Challenges", "🏆 Leaderboard", "🛠️ Manage Challenges", "👥 Manage Users"])
    else:
        tabs = st.tabs(["⚔️ My Challenges", "🏆 Leaderboard"])

    # ── Tab 1: My Challenges ──────────────────────────────────────────────────
    with tabs[0]:
        show_challenges_tab(user)

    # ── Tab 2: Leaderboard ────────────────────────────────────────────────────
    with tabs[1]:
        show_leaderboard_tab()

    # ── Admin Tabs ────────────────────────────────────────────────────────────
    if is_admin:
        with tabs[2]:
            show_manage_challenges_tab(user)
        with tabs[3]:
            show_manage_users_tab()


def show_challenges_tab(user):
    today = date.today().isoformat()
    st.markdown(f"## ⚔️ Today's Challenges — {date.today().strftime('%A, %B %d')}")

    assignments = assign_daily_challenges(user['id'], today)

    if not assignments:
        all_ch = get_all_challenges()
        if len(all_ch) < 6:
            st.warning(f"Not enough challenges in the pool yet! Need at least 6, currently have {len(all_ch)}. Ask Charles to add more 🙏")
            return

    # Count statuses
    completed = [a for a in assignments if a['status'] == 'completed']
    rejected = [a for a in assignments if a['status'] == 'rejected']
    pending = [a for a in assignments if a['status'] == 'pending']

    # Progress bar
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("✅ Completed", f"{len(completed)}/3")
    with col2:
        st.metric("❌ Rejected", f"{len(rejected)}/3")
    with col3:
        remaining = max(0, 3 - len(completed))
        st.metric("⏳ Still Need", str(remaining))

    progress = min(len(completed) / 3, 1.0)
    st.progress(progress, text=f"{'🎉 All done for today!' if len(completed) >= 3 else f'{len(completed)}/3 completed'}")

    if len(completed) >= 3:
        st.balloons()

    st.divider()

    # Show each assignment
    for a in assignments:
        status = a['status']

        if status == 'completed':
            card_class = "completed-card"
            icon = "✅"
        elif status == 'rejected':
            card_class = "rejected-card"
            icon = "❌"
        else:
            card_class = "challenge-card"
            icon = "⚔️"

        st.markdown(f"""<div class="{card_class}">
            <strong>{icon} {a['title']}</strong><br>
            <span style="opacity:0.8">{a['description'] or 'No description'}</span>
        </div>""", unsafe_allow_html=True)

        bcol1, bcol2, bcol3 = st.columns([1, 1, 3])

        if status == 'pending':
            with bcol1:
                if st.button("✅ Complete", key=f"complete_{a['id']}"):
                    complete_challenge(a['id'])
                    st.rerun()
            with bcol2:
                if len(rejected) < 3:
                    if st.button("❌ Reject", key=f"reject_{a['id']}"):
                        reject_challenge(a['id'])
                        st.rerun()
                else:
                    st.caption("Max rejects used")

        elif status == 'completed':
            with bcol1:
                if st.button("↩️ Undo", key=f"uncomplete_{a['id']}"):
                    uncomplete_challenge(a['id'])
                    st.rerun()

        elif status == 'rejected':
            with bcol1:
                if st.button("↩️ Undo Reject", key=f"unreject_{a['id']}"):
                    unreject_challenge(a['id'])
                    st.rerun()


def show_leaderboard_tab():
    st.markdown("## 🏆 Leaderboard")

    leaderboard = get_leaderboard()
    if not leaderboard:
        st.info("No data yet — challenges haven't started!")
        return

    # Top 3 podium
    if len(leaderboard) >= 1:
        st.markdown("### 🥇🥈🥉 Top Performers")
        podium_cols = st.columns(min(3, len(leaderboard)))
        medals = ["🥇", "🥈", "🥉"]
        for i, col in enumerate(podium_cols):
            if i < len(leaderboard):
                p = leaderboard[i]
                with col:
                    st.markdown(f"""
                    <div style="text-align:center; padding:20px; background:linear-gradient(135deg, #667eea22, #764ba222); 
                    border-radius:16px; border:1px solid #667eea55;">
                        <div style="font-size:2.5rem">{medals[i]}</div>
                        <div style="font-size:1.3rem; font-weight:700">{p['username']}</div>
                        <div class="big-number" style="color:#2ecc71">{p['challenges_completed']}</div>
                        <div style="opacity:0.7">challenges done</div>
                        <div style="color:#e74c3c; margin-top:4px">💀 {p['forfeit_count']} forfeits</div>
                    </div>
                    """, unsafe_allow_html=True)

    st.divider()

    # Full table
    st.markdown("### 📊 Full Standings")
    import pandas as pd
    df = pd.DataFrame(leaderboard)
    df.index = range(1, len(df) + 1)
    df.columns = ["Username", "Challenges Completed", "Forfeits"]
    st.dataframe(df, use_container_width=True, height=400)

    # Shame section
    st.divider()
    if leaderboard:
        worst = max(leaderboard, key=lambda x: x['forfeit_count'])
        if worst['forfeit_count'] > 0:
            st.markdown(f"""<div class="forfeit-card">
                <strong>💀 Most Forfeits:</strong> {worst['username']} with {worst['forfeit_count']} forfeit(s). 
                Absolute scenes.
            </div>""", unsafe_allow_html=True)

        least_done = min(leaderboard, key=lambda x: x['challenges_completed'])
        st.markdown(f"""<div class="forfeit-card">
            <strong>😴 Least Challenges Done:</strong> {least_done['username']} with {least_done['challenges_completed']} challenge(s) completed.
        </div>""", unsafe_allow_html=True)


def show_manage_challenges_tab(user):
    st.markdown("## 🛠️ Manage Challenges")
    st.markdown("Add challenges that will be randomly assigned to users each day.")

    with st.form("add_challenge_form"):
        title = st.text_input("Challenge Title", placeholder="e.g. Do 20 pushups")
        description = st.text_area("Description (optional)", placeholder="e.g. Must be witnessed by another camper")
        submitted = st.form_submit_button("➕ Add Challenge", use_container_width=True)
        if submitted:
            success, msg = add_challenge(title, description, user['username'])
            if success:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    st.divider()
    st.markdown("### 📋 Current Challenge Pool")

    challenges = get_all_challenges()
    if not challenges:
        st.info("No challenges yet. Add some above!")
    else:
        st.caption(f"{len(challenges)} active challenges in the pool (need minimum 6)")
        for ch in challenges:
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"**{ch['title']}** — {ch['description'] or 'No description'}")
                st.caption(f"Added by {ch['created_by']}")
            with col2:
                if st.button("🗑️", key=f"del_ch_{ch['id']}"):
                    delete_challenge(ch['id'])
                    st.rerun()


def show_manage_users_tab():
    st.markdown("## 👥 Manage Users")
    users = get_all_users()
    for u in users:
        col1, col2 = st.columns([5, 1])
        with col1:
            admin_badge = " 🛡️ Admin" if u['is_admin'] else ""
            st.markdown(f"**{u['username']}**{admin_badge}")
            st.caption(f"Joined: {u['created_at']}")
        with col2:
            if not u['is_admin']:
                if st.button("🗑️", key=f"del_user_{u['id']}"):
                    delete_user(u['id'])
                    st.rerun()


# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()