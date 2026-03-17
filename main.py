import streamlit as st
from supabase import create_client, Client
import hashlib
import random
from datetime import date, datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

# ─── Common Timezones (for the dropdown) ──────────────────────────────────────
COMMON_TIMEZONES = [
    "Australia/Sydney",
    "Australia/Melbourne",
    "Australia/Brisbane",
    "Australia/Perth",
    "Australia/Adelaide",
    "Australia/Hobart",
    "Pacific/Auckland",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Asia/Kolkata",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "US/Eastern",
    "US/Central",
    "US/Mountain",
    "US/Pacific",
    "UTC",
]

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

# ─── Game Settings ────────────────────────────────────────────────────────────

def get_game_settings() -> dict:
    result = sb().table("game_settings").select("*").eq("id", 1).execute()
    if result.data:
        return result.data[0]
    return {
        "game_start_date": None, "daily_deal_time": "08:00:00",
        "timezone": "Australia/Sydney", "num_days": 5, "game_active": False
    }

def save_game_settings(start_date: str, deal_time: str, timezone: str, num_days: int):
    sb().table("game_settings").upsert({
        "id": 1,
        "game_start_date": start_date,
        "daily_deal_time": deal_time,
        "timezone": timezone,
        "num_days": num_days,
        "game_active": True,
        "updated_at": datetime.now().isoformat()
    }).execute()

def deactivate_game():
    sb().table("game_settings").update({
        "game_active": False,
        "updated_at": datetime.now().isoformat()
    }).eq("id", 1).execute()

def now_in_tz(tz_name: str) -> datetime:
    """Get current datetime in the given timezone."""
    return datetime.now(ZoneInfo(tz_name))

def get_game_day_info(settings: dict) -> dict:
    """
    Calculate the current game state based on settings and current time.
    Returns: {
        'game_active': bool,
        'game_started': bool,      # has the game start date arrived?
        'current_day': int,         # which day of camp (1-indexed), 0 if not started
        'today_deal_time': datetime or None,  # when today's challenges should deal
        'today_dealt': bool,        # have today's challenges been dealt already?
        'today_date': str,          # today's date string in the game timezone
        'game_over': bool,
        'now': datetime,            # current time in game timezone
        'days_until_start': int,    # days until game starts (0 if started)
        'time_until_deal': timedelta or None,  # time until today's deal
    }
    """
    info = {
        "game_active": False, "game_started": False, "current_day": 0,
        "today_deal_time": None, "today_dealt": False, "today_date": None,
        "game_over": False, "now": None, "days_until_start": 0,
        "time_until_deal": None
    }

    if not settings.get("game_active") or not settings.get("game_start_date"):
        return info

    tz_name = settings.get("timezone", "Australia/Sydney")
    now = now_in_tz(tz_name)
    info["now"] = now
    info["game_active"] = True

    start_date = date.fromisoformat(settings["game_start_date"])
    today_local = now.date()
    info["today_date"] = today_local.isoformat()

    num_days = settings.get("num_days", 5)

    # Parse deal time
    deal_time_parts = settings["daily_deal_time"].split(":")
    deal_hour = int(deal_time_parts[0])
    deal_min = int(deal_time_parts[1]) if len(deal_time_parts) > 1 else 0
    deal_time = dtime(deal_hour, deal_min)

    # Which day of camp is it?
    day_offset = (today_local - start_date).days
    current_day = day_offset + 1  # Day 1 is the start date

    if current_day < 1:
        # Game hasn't started yet
        info["days_until_start"] = -day_offset
        # Time until first deal
        first_deal_dt = datetime.combine(start_date, deal_time, tzinfo=ZoneInfo(tz_name))
        info["time_until_deal"] = first_deal_dt - now
        return info

    if current_day > num_days:
        info["game_over"] = True
        info["game_started"] = True
        info["current_day"] = num_days
        return info

    info["game_started"] = True
    info["current_day"] = current_day

    # Today's deal time
    today_deal_dt = datetime.combine(today_local, deal_time, tzinfo=ZoneInfo(tz_name))
    info["today_deal_time"] = today_deal_dt

    if now < today_deal_dt:
        info["time_until_deal"] = today_deal_dt - now

    # Check if today has been dealt
    info["today_dealt"] = is_day_started(info["today_date"])

    return info

# ─── Auto-Deal Logic ──────────────────────────────────────────────────────────

def auto_deal_if_ready():
    """Called on every page load. Deals challenges if the scheduled time has passed."""
    settings = get_game_settings()
    info = get_game_day_info(settings)

    if not info["game_active"] or not info["game_started"]:
        return
    if info["game_over"]:
        return
    if info["today_dealt"]:
        return
    if info["time_until_deal"] is not None:
        # Deal time hasn't arrived yet
        return

    # Time to deal! Also check forfeits for yesterday first
    yesterday = (date.fromisoformat(info["today_date"]) - timedelta(days=1)).isoformat()
    check_forfeits_for_date(yesterday)

    # Deal challenges
    start_day_for_all_users(info["today_date"], info["current_day"])

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
    result = sb().table("daily_assignments").select(
        "challenge_id"
    ).eq("user_id", user_id).execute()
    return {r["challenge_id"] for r in (result.data or [])}

def is_day_started(today: str) -> bool:
    result = sb().table("daily_assignments").select(
        "id"
    ).eq("assigned_date", today).limit(1).execute()
    return bool(result.data)

def start_day_for_all_users(today: str, day_number: int = 1) -> tuple[bool, str]:
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
                f"⚠️ {user['username']} only has {len(available_ids)} fresh challenges left (need 6)."
            )
            continue

        chosen_ids = random.sample(list(available_ids), 6)
        for cid in chosen_ids:
            rows_to_insert.append({
                "user_id": uid,
                "challenge_id": cid,
                "assigned_date": today,
                "day_number": day_number,
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
        "message": f"🚀 Day {day_number} started! Challenges have been dealt!"
    }).execute()

    if errors:
        return True, "Day started, but some users were skipped:\n" + "\n".join(errors)

    return True, f"Day {day_number}: Challenges dealt to {len(users)} campers! 🔥"

def get_pool_status_for_users():
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

def reset_game_data():
    """Wipe all game data but keep users and challenge pool."""
    sb().table("daily_assignments").delete().neq("id", 0).execute()
    sb().table("forfeits").delete().neq("id", 0).execute()
    sb().table("notifications").delete().neq("id", 0).execute()
    deactivate_game()

# ─── Streamlit App ────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Camp Challenge Tracker 🏕️", page_icon="🏕️", layout="wide")

    # Auto-deal check on every page load
    auto_deal_if_ready()

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
        .countdown-card {
            background: linear-gradient(135deg, #9b59b622 0%, #8e44ad22 100%);
            border: 2px solid #9b59b6; border-radius: 16px; padding: 32px; margin: 16px 0;
            text-align: center;
        }
        .gameover-card {
            background: linear-gradient(135deg, #f1c40f22 0%, #f39c1222 100%);
            border: 2px solid #f1c40f; border-radius: 16px; padding: 32px; margin: 16px 0;
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
        .schedule-info {
            background: linear-gradient(135deg, #2ecc7122 0%, #27ae6022 100%);
            border: 1px solid #2ecc7155; border-radius: 12px; padding: 16px; margin: 8px 0;
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


def format_timedelta(td: timedelta) -> str:
    """Format a timedelta into a human-readable string."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "now!"
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "less than a minute"


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

    settings = get_game_settings()
    info = get_game_day_info(settings)

    with st.sidebar:
        st.markdown(f"### 👋 Hey, **{user['username']}**!")
        if is_admin:
            st.markdown("🛡️ **Admin**")

        # Game status
        st.divider()
        if info["game_active"]:
            if info["game_over"]:
                st.markdown("🏁 **Game Over!**")
            elif info["game_started"]:
                st.markdown(f"📅 **Day {info['current_day']}** of {settings['num_days']}")
                if info["today_dealt"]:
                    st.markdown("✅ Today's challenges are live")
                elif info["time_until_deal"]:
                    st.markdown(f"⏳ Deals in {format_timedelta(info['time_until_deal'])}")
            else:
                st.markdown(f"⏳ Starts in **{info['days_until_start']}** day(s)")
        else:
            st.markdown("⚙️ Game not scheduled yet")

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
        tabs = st.tabs(["⚔️ My Challenges", "🏆 Leaderboard", "🚀 Game Settings", "🛠️ Manage Challenges", "👥 Manage Users"])
    else:
        tabs = st.tabs(["⚔️ My Challenges", "🏆 Leaderboard"])

    with tabs[0]:
        show_challenges_tab(user, info, settings)
    with tabs[1]:
        show_leaderboard_tab()
    if is_admin:
        with tabs[2]:
            show_game_settings_tab(info, settings)
        with tabs[3]:
            show_manage_challenges_tab(user)
        with tabs[4]:
            show_manage_users_tab()


def show_challenges_tab(user, info, settings):
    if not info["game_active"]:
        st.markdown("""<div class="waiting-card">
            <div style="font-size:3rem">⚙️</div>
            <h3>Game hasn't been scheduled yet</h3>
            <p style="opacity:0.7">Ask Charles to set up the game schedule!</p>
        </div>""", unsafe_allow_html=True)
        return

    if info["game_over"]:
        st.markdown("""<div class="gameover-card">
            <div style="font-size:3rem">🏁</div>
            <h3>Game Over!</h3>
            <p style="opacity:0.7">Camp challenges are finished. Check the leaderboard to see the final results!</p>
        </div>""", unsafe_allow_html=True)
        return

    if not info["game_started"]:
        days_left = info["days_until_start"]
        time_str = format_timedelta(info["time_until_deal"]) if info["time_until_deal"] else "soon"
        st.markdown(f"""<div class="countdown-card">
            <div style="font-size:3rem">⏳</div>
            <h3>Game starts in {days_left} day{"s" if days_left != 1 else ""}!</h3>
            <p style="opacity:0.7">First challenges drop in <strong>{time_str}</strong></p>
            <p>📅 {settings["game_start_date"]} at {settings["daily_deal_time"][:5]} ({settings["timezone"]})</p>
        </div>""", unsafe_allow_html=True)
        return

    today_str = info["today_date"]

    if not info["today_dealt"]:
        if info["time_until_deal"]:
            time_str = format_timedelta(info["time_until_deal"])
            st.markdown(f"""<div class="countdown-card">
                <div style="font-size:3rem">⏳</div>
                <h3>Day {info['current_day']} challenges drop in {time_str}</h3>
                <p style="opacity:0.7">Scheduled for {settings["daily_deal_time"][:5]} ({settings["timezone"]})</p>
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown("""<div class="waiting-card">
                <div style="font-size:3rem">🔄</div>
                <h3>Dealing challenges...</h3>
                <p style="opacity:0.7">Refresh the page in a moment!</p>
            </div>""", unsafe_allow_html=True)
        return

    st.markdown(f"## ⚔️ Day {info['current_day']} Challenges — {date.fromisoformat(today_str).strftime('%A, %B %d')}")

    assignments = get_daily_assignments(user['id'], today_str)

    if not assignments:
        st.info("No challenges assigned to you today. You may have been skipped if there weren't enough fresh challenges left. Talk to Charles 🙏")
        return

    completed = [a for a in assignments if a['status'] == 'completed']
    rejected = [a for a in assignments if a['status'] == 'rejected']

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


def show_game_settings_tab(info, settings):
    st.markdown("## 🚀 Game Settings")

    # Current status
    if info["game_active"]:
        if info["game_over"]:
            st.markdown("""<div class="gameover-card">
                <div style="font-size:2rem">🏁</div>
                <h4>Game is finished!</h4>
            </div>""", unsafe_allow_html=True)
        elif info["game_started"]:
            st.markdown(f"""<div class="schedule-info">
                <strong>🟢 Game is LIVE — Day {info["current_day"]} of {settings["num_days"]}</strong><br>
                Started: {settings["game_start_date"]} · Daily deal: {settings["daily_deal_time"][:5]} · TZ: {settings["timezone"]}
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div class="countdown-card">
                <strong>⏳ Game scheduled — starts in {info["days_until_start"]} day(s)</strong><br>
                Start: {settings["game_start_date"]} at {settings["daily_deal_time"][:5]} ({settings["timezone"]})
            </div>""", unsafe_allow_html=True)
    else:
        st.info("No game scheduled. Set one up below!")

    st.divider()

    # Schedule form
    st.markdown("### 📅 Schedule the Game")
    st.caption("Set when camp starts. Challenges will auto-deal to everyone at the scheduled time each day.")

    with st.form("game_settings_form"):
        col1, col2 = st.columns(2)
        with col1:
            current_start = None
            if settings.get("game_start_date"):
                try:
                    current_start = date.fromisoformat(settings["game_start_date"])
                except (ValueError, TypeError):
                    pass
            start_date = st.date_input(
                "Game Start Date",
                value=current_start or date.today(),
                min_value=date.today() - timedelta(days=30),
            )

        with col2:
            # Parse current deal time
            current_time = dtime(8, 0)
            if settings.get("daily_deal_time"):
                parts = settings["daily_deal_time"].split(":")
                try:
                    current_time = dtime(int(parts[0]), int(parts[1]))
                except (ValueError, IndexError):
                    pass
            deal_time = st.time_input("Daily Challenge Deal Time", value=current_time)

        col3, col4 = st.columns(2)
        with col3:
            current_tz = settings.get("timezone", "Australia/Sydney")
            tz_index = COMMON_TIMEZONES.index(current_tz) if current_tz in COMMON_TIMEZONES else 0
            timezone = st.selectbox("Timezone", COMMON_TIMEZONES, index=tz_index)

        with col4:
            num_days = st.number_input(
                "Number of Days (camp duration)",
                min_value=1, max_value=30,
                value=settings.get("num_days", 5)
            )

        submitted = st.form_submit_button("💾 Save & Activate Game", use_container_width=True, type="primary")
        if submitted:
            save_game_settings(
                start_date.isoformat(),
                deal_time.strftime("%H:%M:%S"),
                timezone,
                num_days
            )
            st.success(f"Game scheduled! Starts {start_date.isoformat()} at {deal_time.strftime('%H:%M')} ({timezone}) for {num_days} days 🔥")
            st.rerun()

    st.divider()

    # Manual override
    st.markdown("### ⚡ Manual Override")
    st.caption("Force-deal today's challenges immediately, regardless of schedule.")

    mcol1, mcol2 = st.columns(2)
    with mcol1:
        if st.button("🃏 Deal Now", use_container_width=True):
            today_str = info["today_date"] if info.get("today_date") else date.today().isoformat()
            day_num = info["current_day"] if info.get("current_day", 0) > 0 else 1
            success, msg = start_day_for_all_users(today_str, day_num)
            if success:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    with mcol2:
        if st.button("🛑 Deactivate Game", use_container_width=True):
            deactivate_game()
            st.warning("Game deactivated. No more auto-dealing.")
            st.rerun()

    st.divider()

    # Danger zone
    with st.expander("🔴 Danger Zone — Reset Game Data"):
        st.warning("This will delete ALL assignments, forfeits, and notifications. Users and challenges are kept.")
        if st.button("💣 Reset All Game Data", type="primary"):
            reset_game_data()
            st.success("Game data wiped. Fresh start!")
            st.rerun()

    st.divider()

    # Pool health
    st.markdown("### 📊 Challenge Pool Health")
    pool_status = get_pool_status_for_users()
    total_challenges = len(get_all_challenges())
    st.markdown(f"**Total active challenges:** {total_challenges}")
    st.divider()

    for ps in pool_status:
        col1, col2, col3 = st.columns([3, 2, 2])
        with col1:
            st.markdown(f"**{ps['username']}**")
        with col2:
            st.markdown(f"Used: {ps['used']} / {total_challenges}")
        with col3:
            if ps['remaining'] >= 12:
                cls, label = "pool-good", f"✅ {ps['remaining']} left"
            elif ps['remaining'] >= 6:
                cls, label = "pool-warn", f"⚠️ {ps['remaining']} left"
            else:
                cls, label = "pool-bad", f"❌ {ps['remaining']} left (can't play!)"
            st.markdown(f'<span class="{cls}">{label}</span>', unsafe_allow_html=True)

    if total_challenges > 0:
        max_days = total_challenges // 6
        st.divider()
        st.info(f"💡 With {total_challenges} challenges, each camper can play for up to **{max_days} days**. Add more to extend the game!")


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
    st.markdown("### ⚡ Bulk Add Challenges")
    st.caption("One per line. Format: `Title | Description` (description optional)")
    with st.form("bulk_add_form"):
        bulk_text = st.text_area("Paste challenges here", height=150,
            placeholder="Do 20 pushups | Must be witnessed\nSing a song at dinner\nWear your shirt inside out all day")
        bulk_submitted = st.form_submit_button("➕ Add All", use_container_width=True)
        if bulk_submitted and bulk_text.strip():
            lines = [l.strip() for l in bulk_text.strip().split("\n") if l.strip()]
            added = 0
            for line in lines:
                parts = line.split("|", 1)
                t = parts[0].strip()
                d = parts[1].strip() if len(parts) > 1 else ""
                if t:
                    ok, _ = add_challenge(t, d, user['username'])
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
        st.caption(f"{len(challenges)} active challenges")
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
