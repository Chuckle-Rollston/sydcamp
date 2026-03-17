import streamlit as st
from supabase import create_client, Client
import hashlib
import random
from datetime import date, timedelta

# ─── Supabase Connection ──────────────────────────────────────────────────────

@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def sb() -> Client:
    return init_supabase()

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

# ─── User Functions ───────────────────────────────────────────────────────────

def register_user(username: str, pin: str) -> tuple[bool, str]:
    if len(pin) != 3 or not pin.isdigit():
        return False, "PIN must be exactly 3 digits."
    if not username.strip():
        return False, "Username can't be empty bruzz."
    try:
        result = sb().table("users").insert({
            "username": username.strip(),
            "pin_hash": hash_pin(pin),
            "is_admin": False
        }).execute()
        if result.data:
            return True, "Account created! You're in 🔥"
        return False, "Something went wrong."
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower() or "23505" in str(e):
            return False, f"Username '{username}' is already taken. Pick something else 💀"
        return False, f"Error: {e}"

def login_user(username: str, pin: str) -> tuple[bool, dict | str]:
    result = sb().table("users").select("*").eq(
        "username", username.strip()
    ).eq(
        "pin_hash", hash_pin(pin)
    ).execute()
    if result.data:
        return True, result.data[0]
    return False, "Wrong username or PIN 🥷"

# ─── Challenge Functions ──────────────────────────────────────────────────────

def add_challenge(title: str, description: str, created_by: str) -> tuple[bool, str]:
    if not title.strip():
        return False, "Challenge needs a title."
    try:
        sb().table("challenges").insert({
            "title": title.strip(),
            "description": description.strip(),
            "created_by": created_by,
            "active": True
        }).execute()
        return True, "Challenge added 🔥"
    except Exception as e:
        return False, f"Error: {e}"

def get_all_challenges():
    result = sb().table("challenges").select("*").eq("active", True).execute()
    return result.data or []

def delete_challenge(challenge_id: int):
    sb().table("challenges").update({"active": False}).eq("id", challenge_id).execute()

def get_daily_assignments(user_id: int, today: str):
    result = sb().table("daily_assignments").select(
        "*, challenges(title, description)"
    ).eq("user_id", user_id).eq("assigned_date", today).order("id").execute()
    # Flatten the joined data
    rows = []
    for r in (result.data or []):
        flat = {**r}
        if "challenges" in flat and flat["challenges"]:
            flat["title"] = flat["challenges"]["title"]
            flat["description"] = flat["challenges"]["description"]
        else:
            flat["title"] = "Unknown"
            flat["description"] = ""
        del flat["challenges"]
        rows.append(flat)
    return rows

def assign_daily_challenges(user_id: int, today: str):
    """Assign 6 random challenges for the day if not already assigned."""
    existing = get_daily_assignments(user_id, today)
    if existing:
        return existing

    all_challenges = get_all_challenges()
    if len(all_challenges) < 6:
        return []

    chosen = random.sample(all_challenges, 6)
    rows = []
    for ch in chosen:
        rows.append({
            "user_id": user_id,
            "challenge_id": ch["id"],
            "assigned_date": today,
            "status": "pending"
        })

    try:
        sb().table("daily_assignments").upsert(rows, on_conflict="user_id,challenge_id,assigned_date").execute()
    except Exception:
        pass

    return get_daily_assignments(user_id, today)

def complete_challenge(assignment_id: int):
    from datetime import datetime
    sb().table("daily_assignments").update({
        "status": "completed",
        "completed_at": datetime.now().isoformat()
    }).eq("id", assignment_id).eq("status", "pending").execute()

def reject_challenge(assignment_id: int):
    sb().table("daily_assignments").update({
        "status": "rejected"
    }).eq("id", assignment_id).eq("status", "pending").execute()

def unreject_challenge(assignment_id: int):
    sb().table("daily_assignments").update({
        "status": "pending"
    }).eq("id", assignment_id).eq("status", "rejected").execute()

def uncomplete_challenge(assignment_id: int):
    sb().table("daily_assignments").update({
        "status": "pending",
        "completed_at": None
    }).eq("id", assignment_id).eq("status", "completed").execute()

# ─── Forfeit Check ────────────────────────────────────────────────────────────

def check_forfeits_for_date(check_date: str):
    """Check all users for forfeits on a given date."""
    today = date.today().isoformat()
    if check_date >= today:
        return  # Only check past days

    users = sb().table("users").select("id, username").execute().data or []

    for user in users:
        uid = user["id"]

        # Get their assignments for that date
        assignments = sb().table("daily_assignments").select("status").eq(
            "user_id", uid
        ).eq("assigned_date", check_date).execute().data or []

        if not assignments:
            continue

        completed_count = sum(1 for a in assignments if a["status"] == "completed")

        # Already forfeited?
        existing = sb().table("forfeits").select("id").eq(
            "user_id", uid
        ).eq("forfeit_date", check_date).execute().data

        if completed_count < 3 and not existing:
            try:
                sb().table("forfeits").insert({
                    "user_id": uid,
                    "forfeit_date": check_date
                }).execute()
                sb().table("notifications").insert({
                    "message": f"⚠️ {user['username']} has forfeited on {check_date}!"
                }).execute()
            except Exception:
                pass  # Already inserted (race condition)

def get_notifications(limit=20):
    result = sb().table("notifications").select("*").order(
        "created_at", desc=True
    ).limit(limit).execute()
    return result.data or []

# ─── Leaderboard ──────────────────────────────────────────────────────────────

def get_leaderboard():
    """Build leaderboard from Supabase data."""
    users = sb().table("users").select("id, username").execute().data or []

    # Get all completed counts
    completed_result = sb().table("daily_assignments").select(
        "user_id"
    ).eq("status", "completed").execute().data or []

    # Get all forfeit counts
    forfeit_result = sb().table("forfeits").select("user_id").execute().data or []

    # Count per user
    completed_counts = {}
    for r in completed_result:
        uid = r["user_id"]
        completed_counts[uid] = completed_counts.get(uid, 0) + 1

    forfeit_counts = {}
    for r in forfeit_result:
        uid = r["user_id"]
        forfeit_counts[uid] = forfeit_counts.get(uid, 0) + 1

    leaderboard = []
    for u in users:
        leaderboard.append({
            "username": u["username"],
            "challenges_completed": completed_counts.get(u["id"], 0),
            "forfeit_count": forfeit_counts.get(u["id"], 0)
        })

    leaderboard.sort(key=lambda x: (-x["challenges_completed"], x["forfeit_count"]))
    return leaderboard

# ─── Admin Functions ──────────────────────────────────────────────────────────

def get_all_users():
    result = sb().table("users").select("id, username, is_admin, created_at").order("username").execute()
    return result.data or []

def delete_user(user_id: int):
    sb().table("daily_assignments").delete().eq("user_id", user_id).execute()
    sb().table("forfeits").delete().eq("user_id", user_id).execute()
    sb().table("users").delete().eq("id", user_id).eq("is_admin", False).execute()

# ─── Streamlit App ────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Camp Challenge Tracker 🏕️", page_icon="🏕️", layout="wide")

    # Run forfeit check for yesterday
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    check_forfeits_for_date(yesterday)

    # Custom CSS
    st.markdown("""
    <style>
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
    is_admin = user.get('is_admin', False)

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
                    <small style="opacity:0.6">{n['created_at'][:19]}</small></div>""",
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

    with tabs[0]:
        show_challenges_tab(user)
    with tabs[1]:
        show_leaderboard_tab()
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

    completed = [a for a in assignments if a['status'] == 'completed']
    rejected = [a for a in assignments if a['status'] == 'rejected']
    pending = [a for a in assignments if a['status'] == 'pending']

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

    for a in assignments:
        status = a['status']
        if status == 'completed':
            card_class, icon = "completed-card", "✅"
        elif status == 'rejected':
            card_class, icon = "rejected-card", "❌"
        else:
            card_class, icon = "challenge-card", "⚔️"

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
    st.markdown("### 📊 Full Standings")
    import pandas as pd
    df = pd.DataFrame(leaderboard)
    df.index = range(1, len(df) + 1)
    df.columns = ["Username", "Challenges Completed", "Forfeits"]
    st.dataframe(df, use_container_width=True, height=400)

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
            st.caption(f"Joined: {u['created_at'][:19]}")
        with col2:
            if not u['is_admin']:
                if st.button("🗑️", key=f"del_user_{u['id']}"):
                    delete_user(u['id'])
                    st.rerun()


if __name__ == "__main__":
    main()
