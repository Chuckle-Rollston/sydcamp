import streamlit as st
from supabase import create_client, Client
import hashlib
import random
from datetime import date, datetime, timedelta

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

def get_all_past_challenge_ids(user_id: int) -> set:
    """Get every challenge_id this user has EVER been assigned (across all days)."""
    result = sb().table("daily_assignments").select(
        "challenge_id"
    ).eq("user_id", user_id).execute()
    return {r["challenge_id"] for r in (result.data or [])}

def is_day_started(today: str) -> bool:
    """Check if any assignments exist for today (i.e. admin has started the day)."""
    result = sb().table("daily_assignments").select(
        "id"
    ).eq("assigned_date", today).limit(1).execute()
    return bool(result.data)

def start_day_for_all_users(today: str) -> tuple[bool, str]:
    """Admin action: assign 6 unique-ever challenges to every user for today."""
    if is_day_started(today):
        return False, "Today's challenges have already been dealt out!"

    all_challenges = get_all_challenges()
    users = sb().table("users").select("id, username").execute().data or []

    if not users:
        return False, "No users registered yet."
    if len(all_challenges) < 6:
        return False, f"Need at least 6 challenges in the pool. Currently have {len(all_challenges)}."

    all_challenge_ids = {ch["id"] for ch in all_challenges}
    errors = []
    rows_to_insert = []

    for user in users:
        uid = user["id"]
        past_ids = get_all_past_challenge_ids(uid)
        available_ids = all_challenge_ids - past_ids

        if len(available_ids) < 6:
            errors.append(
                f"⚠️ {user['username']} only has {len(available_ids)} fresh challenges left (need 6). "
                f"They've done {len(past_ids)} already. Add more to the pool!"
            )
            continue

        chosen_ids = random.sample(list(available_ids), 6)
        for cid in chosen_ids:
            rows_to_insert.append({
                "user_id": uid,
                "challenge_id": cid,
                "assigned_date": today,
                "status": "pending"
            })

    if not rows_to_insert and errors:
        return False, "\n".join(errors)

    try:
        for i in range(0, len(rows_to_insert), 100):
            batch = rows_to_insert[i:i+100]
            sb().table("daily_assignments").insert(batch).execute()
    except Exception as e:
        return False, f"Error assigning challenges: {e}"

    sb().table("notifications").insert({
        "message": f"🚀 Day started! Challenges have been dealt for {date.today().strftime('%A, %B %d')}!"
    }).execute()

    if errors:
        return True, "Day started, but some users were skipped:\n" + "\n".join(errors)

    return True, f"Challenges dealt to {len(users)} campers! Let's go 🔥"

def get_pool_status_for_users():
    """For admin: show how many fresh challenges each user has left."""
    all_challenges = get_all_challenges()
    total = len(all_challenges)
    users = sb().table("users").select("id, username").execute().data or []

    status = []
    for user in users:
        past_count = len(get_all_past_challenge_ids(user["id"]))
        remaining = total - past_count
        status.append({
            "username": user["username"],
            "used": past_count,
            "remaining": remaining,
            "can_play": remaining >= 6
        })
    return status

def complete_challenge(assignment_id: int):
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
    today = date.today().isoformat()
    if check_date >= today:
        return

    users = sb().table("users").select("id, username").execute().data or []
    for user in users:
        uid = user["id"]
        assignments = sb().table("daily_assignments").select("status").eq(
            "user_id", uid
        ).eq("assigned_date", check_date).execute().data or []

        if not assignments:
            continue

        completed_count = sum(1 for a in assignments if a["status"] == "completed")
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
                pass

def get_notifications(limit=20):
    result = sb().table("notifications").select("*").order(
        "created_at", desc=True
    ).limit(limit).execute()
    return result.data or []

# ─── Leaderboard ──────────────────────────────────────────────────────────────

def get_leaderboard():
    users = sb().table("users").select("id, username").execute().data or []
    completed_result = sb().table("daily_assignments").select(
        "user_id"
    ).eq("status", "completed").execute().data or []
    forfeit_result = sb().table("forfeits").select("user_id").execute().data or []

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

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    check_forfeits_for_date(yesterday)

    st.markdown("""
    <style>
        div[data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%); }
        div[data-testid="stSidebar"] * { color: #e0e0e0 !important; }
        .challenge-card {
            background: linear-gradient(135deg, #667eea22 0%, #764ba222 100%);
            border: 1px solid #667eea55; border-radius: 12px; padding: 16px; margin: 8px 0;
        }
        .completed-card {
            background: linear-gradient(135deg, #2ecc7122 0%, #27ae6022 100%);
            border: 1px solid #2ecc7155; border-radius: 12px; padding: 16px; margin: 8px 0;
        }
        .rejected-card {
            background: linear-gradient(135deg, #e74c3c22 0%, #c0392b22 100%);
            border: 1px solid #e74c3c55; border-radius: 12px; padding: 16px; margin: 8px 0;
        }
        .forfeit-card {
            background: linear-gradient(135deg, #e74c3c33 0%, #c0392b33 100%);
            border: 1px solid #e74c3c; border-radius: 12px; padding: 16px; margin: 8px 0;
        }
        .start-card {
            background: linear-gradient(135deg, #f39c1222 0%, #e74c3c22 100%);
            border: 2px solid #f39c12; border-radius: 16px; padding: 24px; margin: 16px 0;
            text-align: center;
        }
        .waiting-card {
            background: linear-gradient(135deg, #3498db22 0%, #2980b922 100%);
            border: 2px dashed #3498db; border-radius: 16px; padding: 32px; margin: 16px 0;
            text-align: center;
        }
        .pool-good { color: #2ecc71; font-weight: 700; }
        .pool-warn { color: #f39c12; font-weight: 700; }
        .pool-bad { color: #e74c3c; font-weight: 700; }
        .big-number { font-size: 2.5rem; font-weight: 800; }
        .notification-box {
            background: #ff990022; border-left: 4px solid #ff9900;
            padding: 10px 16px; margin: 4px 0; border-radius: 0 8px 8px 0;
        }
    </style>
    """, unsafe_allow_html=True)

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

    with st.sidebar:
        st.markdown(f"### 👋 Hey, **{user['username']}**!")
        if is_admin:
            st.markdown("🛡️ **Admin**")
        st.divider()

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

    if is_admin:
        tabs = st.tabs(["⚔️ My Challenges", "🏆 Leaderboard", "🚀 Start Day", "🛠️ Manage Challenges", "👥 Manage Users"])
    else:
        tabs = st.tabs(["⚔️ My Challenges", "🏆 Leaderboard"])

    with tabs[0]:
        show_challenges_tab(user)
    with tabs[1]:
        show_leaderboard_tab()
    if is_admin:
        with tabs[2]:
            show_start_day_tab()
        with tabs[3]:
            show_manage_challenges_tab(user)
        with tabs[4]:
            show_manage_users_tab()


def show_challenges_tab(user):
    today = date.today().isoformat()
    st.markdown(f"## ⚔️ Today's Challenges — {date.today().strftime('%A, %B %d')}")

    day_active = is_day_started(today)

    if not day_active:
        st.markdown("""<div class="waiting-card">
            <div style="font-size:3rem">⏳</div>
            <h3>Waiting for the game master to start today's challenges...</h3>
            <p style="opacity:0.7">Charles hasn't dealt the cards yet. Sit tight!</p>
        </div>""", unsafe_allow_html=True)
        return

    assignments = get_daily_assignments(user['id'], today)

    if not assignments:
        st.info("No challenges assigned to you today. You may have been skipped if there weren't enough fresh challenges left. Talk to Charles 🙏")
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


def show_start_day_tab():
    today = date.today().isoformat()
    st.markdown(f"## 🚀 Start Day — {date.today().strftime('%A, %B %d')}")

    day_active = is_day_started(today)

    if day_active:
        st.success("✅ Today's challenges have already been dealt!")
        st.divider()
    else:
        st.markdown("""<div class="start-card">
            <div style="font-size:3rem">🃏</div>
            <h3>Ready to deal today's challenges?</h3>
            <p style="opacity:0.7">This will assign 6 unique challenges to every registered camper.<br>
            No one will ever receive a challenge they've already seen.</p>
        </div>""", unsafe_allow_html=True)

        if st.button("🚀 START TODAY'S GAME", use_container_width=True, type="primary"):
            with st.spinner("Dealing challenges to all campers..."):
                success, msg = start_day_for_all_users(today)
            if success:
                st.success(msg)
                st.balloons()
                st.rerun()
            else:
                st.error(msg)

        st.divider()

    # Pool health
    st.markdown("### 📊 Challenge Pool Health")
    st.caption("Shows how many fresh (never-seen) challenges each user has remaining.")

    pool_status = get_pool_status_for_users()
    total_challenges = len(get_all_challenges())

    st.markdown(f"**Total active challenges in pool:** {total_challenges}")
    st.divider()

    for ps in pool_status:
        col1, col2, col3 = st.columns([3, 2, 2])
        with col1:
            st.markdown(f"**{ps['username']}**")
        with col2:
            st.markdown(f"Used: {ps['used']} / {total_challenges}")
        with col3:
            if ps['remaining'] >= 12:
                cls = "pool-good"
                label = f"✅ {ps['remaining']} left"
            elif ps['remaining'] >= 6:
                cls = "pool-warn"
                label = f"⚠️ {ps['remaining']} left"
            else:
                cls = "pool-bad"
                label = f"❌ {ps['remaining']} left (can't play!)"
            st.markdown(f'<span class="{cls}">{label}</span>', unsafe_allow_html=True)

    if total_challenges > 0:
        max_days = total_challenges // 6
        st.divider()
        st.info(f"💡 With {total_challenges} challenges, each camper can play for a maximum of **{max_days} days** before running out of fresh ones. Add more challenges to extend the game!")


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
    st.markdown("Add challenges to the pool. Once you start the day, 6 are randomly dealt to each camper.")

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

    # Bulk add
    st.markdown("### ⚡ Bulk Add Challenges")
    st.caption("Paste one challenge per line. Format: `Title | Description` (description is optional)")
    with st.form("bulk_add_form"):
        bulk_text = st.text_area("Paste challenges here", height=150,
            placeholder="Do 20 pushups | Must be witnessed\nSing a song at dinner\nWear your shirt inside out all day")
        bulk_submitted = st.form_submit_button("➕ Add All", use_container_width=True)
        if bulk_submitted and bulk_text.strip():
            lines = [l.strip() for l in bulk_text.strip().split("\n") if l.strip()]
            added = 0
            for line in lines:
                parts = line.split("|", 1)
                title = parts[0].strip()
                desc = parts[1].strip() if len(parts) > 1 else ""
                if title:
                    ok, _ = add_challenge(title, desc, user['username'])
                    if ok:
                        added += 1
            st.success(f"Added {added}/{len(lines)} challenges 🔥")
            st.rerun()

    st.divider()
    st.markdown("### 📋 Current Challenge Pool")
    challenges = get_all_challenges()
    if not challenges:
        st.info("No challenges yet. Add some above!")
    else:
        st.caption(f"{len(challenges)} active challenges — each camper needs 6 fresh ones per day")
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
