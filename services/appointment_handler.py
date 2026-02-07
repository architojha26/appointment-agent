"""
Appointment Handler
====================
All operations keyed by 4-digit user_id.
Stores in data/appointments.json.

Tools:
  1. identify_user        – look up user by 4-digit ID
  2. fetch_slots           – available slots for a date
  3. book_appointment      – book (with conflict check)
  4. retrieve_appointments – all appointments for a user
  5. cancel_appointment    – mark cancelled
  6. modify_appointment    – change date/time
  7. end_conversation      – signal call is over
"""

import json
import uuid
import random
from datetime import datetime, timedelta
from pathlib import Path
from utils.logger import get_custom_logger

logger = get_custom_logger("appointments")

APPOINTMENTS_FILE = Path("data/appointments.json")
SUMMARIES_FILE = Path("data/call_summaries.json")

BUSINESS_START_HOUR = 9
BUSINESS_END_HOUR = 18
SLOT_DURATION_MINUTES = 30


# ── DB helpers ───────────────────────────────────────────────────────────

def _load_db() -> dict:
    """
    DB schema:
    {
      "users": {
        "1234": {"name": "Archit", "user_id": "1234"},
        ...
      },
      "appointments": [
        {
          "appointment_id": "abc123",
          "user_id": "1234",
          "name": "Archit",
          "date": "2026-02-12",
          "time": "10:00 AM",
          "purpose": "General checkup",
          "status": "booked",
          "created_at": "...",
          "modified_at": null
        }
      ]
    }
    """
    if APPOINTMENTS_FILE.exists():
        try:
            data = json.loads(APPOINTMENTS_FILE.read_text())
            data.setdefault("users", {})
            data.setdefault("appointments", [])
            return data
        except Exception:
            pass
    return {"users": {}, "appointments": []}


def _save_db(data: dict):
    APPOINTMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPOINTMENTS_FILE.write_text(json.dumps(data, indent=2, default=str))


def _normalize_id(user_id: str) -> str:
    """Extract 4 digits from whatever the user says."""
    digits = "".join(c for c in str(user_id) if c.isdigit())
    return digits[:4] if len(digits) >= 4 else digits


def _generate_new_id(data: dict) -> str:
    """Generate a unique 4-digit ID."""
    existing = set(data["users"].keys())
    for _ in range(100):
        new_id = f"{random.randint(1000, 9999)}"
        if new_id not in existing:
            return new_id
    return f"{random.randint(1000, 9999)}"


def _generate_all_slots(date: str) -> list[str]:
    slots = []
    start = datetime.strptime(f"{date} {BUSINESS_START_HOUR:02d}:00", "%Y-%m-%d %H:%M")
    end = datetime.strptime(f"{date} {BUSINESS_END_HOUR:02d}:00", "%Y-%m-%d %H:%M")
    current = start
    while current < end:
        slots.append(current.strftime("%I:%M %p"))
        current += timedelta(minutes=SLOT_DURATION_MINUTES)
    return slots


def _get_booked_slots(data: dict, date: str) -> set[str]:
    return {
        r["time"] for r in data["appointments"]
        if r["date"] == date and r["status"] == "booked"
    }


# ── Call Summary helpers ─────────────────────────────────────────────────

def _load_summaries() -> dict:
    """
    Schema:
    {
      "1001": [
        {"call_id": "abc123", "summary": "...", "timestamp": "..."},
        ...
      ]
    }
    """
    if SUMMARIES_FILE.exists():
        try:
            return json.loads(SUMMARIES_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_summaries(data: dict):
    SUMMARIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARIES_FILE.write_text(json.dumps(data, indent=2, default=str))


def save_call_summary(user_id: str, call_id: str, summary: str) -> dict:
    """Save a call summary for a user. Called after conversation ends."""
    if not user_id:
        return {"status": "skipped", "message": "No user identified in this call."}
    uid = _normalize_id(user_id)
    data = _load_summaries()
    if uid not in data:
        data[uid] = []
    data[uid].append({
        "call_id": call_id,
        "summary": summary,
        "timestamp": datetime.now().isoformat(),
    })
    _save_summaries(data)
    logger.info("Saved call summary for user %s (call %s)", uid, call_id)
    return {"status": "saved", "user_id": uid, "call_id": call_id}


def get_last_summary(user_id: str) -> dict | None:
    """Get the most recent call summary for a user."""
    uid = _normalize_id(user_id)
    data = _load_summaries()
    if uid in data and data[uid]:
        return data[uid][-1]  # most recent
    return None


# ── Tool implementations ────────────────────────────────────────────────

def identify_user(user_id: str) -> dict:
    """Look up user by 4-digit ID. Returns profile + active appointments."""
    uid = _normalize_id(user_id)
    if len(uid) < 4:
        return {
            "status": "invalid",
            "message": f"'{user_id}' is not a valid 4-digit ID. Please ask for a valid ID."
        }

    data = _load_db()

    if uid in data["users"]:
        user = data["users"][uid]
        active = [
            a for a in data["appointments"]
            if a["user_id"] == uid and a["status"] == "booked"
        ]
        last_summary = get_last_summary(uid)
        logger.info("User found: %s (ID: %s), %d appointments", user["name"], uid, len(active))
        return {
            "status": "found",
            "user": user,
            "active_appointments": active,
            "last_call_summary": last_summary,
            "message": f"Welcome back, {user['name']}! You have {len(active)} active appointment(s)."
        }
    else:
        return {
            "status": "not_found",
            "user_id": uid,
            "message": f"No user found with ID {uid}. Ask if they'd like to register as a new user."
        }


def register_user(name: str) -> dict:
    """Register a new user with a name, auto-generate 4-digit ID."""
    data = _load_db()
    new_id = _generate_new_id(data)
    data["users"][new_id] = {"name": name, "user_id": new_id}
    _save_db(data)
    logger.info("Registered new user: %s (ID: %s)", name, new_id)
    return {
        "status": "registered",
        "user_id": new_id,
        "name": name,
        "message": f"Registered {name} with ID {new_id}. Please remember your ID for future visits."
    }


def fetch_slots(date: str) -> dict:
    """Return available and booked slots for a given date."""
    data = _load_db()
    all_slots = _generate_all_slots(date)
    booked = _get_booked_slots(data, date)
    available = [s for s in all_slots if s not in booked]

    logger.info("Slots for %s: %d available, %d booked", date, len(available), len(booked))
    return {
        "status": "ok",
        "date": date,
        "business_hours": f"9:00 AM – 6:00 PM",
        "total_slots": len(all_slots),
        "available_count": len(available),
        "booked_count": len(booked),
        "available_slots": available,
        "booked_slots": list(booked),
    }


def book_appointment(user_id: str, name: str, date: str, time_slot: str, purpose: str = "") -> dict:
    """Book an appointment with conflict checking."""
    uid = _normalize_id(user_id)
    data = _load_db()

    # Validate slot is within business hours
    all_slots = _generate_all_slots(date)
    if time_slot not in all_slots:
        return {
            "status": "invalid_slot",
            "message": f"'{time_slot}' is not a valid slot. Business hours are 9:00 AM – 6:00 PM, 30-min intervals.",
            "valid_slots_sample": all_slots[:5] + ["..."] + all_slots[-3:]
        }

    # Check global conflict (any user at same slot)
    booked = _get_booked_slots(data, date)
    if time_slot in booked:
        available = [s for s in all_slots if s not in booked]
        return {
            "status": "conflict",
            "message": f"The slot {time_slot} on {date} is already booked.",
            "suggested_alternatives": available[:5],
        }

    # Check if THIS user already has a booking at same date+time
    for a in data["appointments"]:
        if a["user_id"] == uid and a["date"] == date and a["time"] == time_slot and a["status"] == "booked":
            return {
                "status": "already_booked",
                "message": f"You already have an appointment at {time_slot} on {date}.",
                "appointment_id": a["appointment_id"],
            }

    # Register user if not exists
    if uid not in data["users"]:
        data["users"][uid] = {"name": name, "user_id": uid}
    elif not data["users"][uid]["name"] and name:
        data["users"][uid]["name"] = name

    appt = {
        "appointment_id": str(uuid.uuid4())[:8],
        "user_id": uid,
        "name": name,
        "date": date,
        "time": time_slot,
        "purpose": purpose,
        "status": "booked",
        "created_at": datetime.now().isoformat(),
        "modified_at": None,
    }
    data["appointments"].append(appt)
    _save_db(data)
    logger.info("Booked: %s", appt)

    return {
        "status": "booked",
        "appointment_id": appt["appointment_id"],
        "user_id": uid,
        "name": name,
        "date": date,
        "time": time_slot,
        "purpose": purpose,
        "message": f"Appointment booked for {name} on {date} at {time_slot}. ID: {appt['appointment_id']}"
    }


def retrieve_appointments(user_id: str) -> dict:
    """Fetch all appointments for a user."""
    uid = _normalize_id(user_id)
    data = _load_db()
    user_appts = [a for a in data["appointments"] if a["user_id"] == uid]
    active = [a for a in user_appts if a["status"] == "booked"]
    cancelled = [a for a in user_appts if a["status"] == "cancelled"]

    logger.info("Retrieved for %s: %d active, %d cancelled", uid, len(active), len(cancelled))
    return {
        "status": "ok",
        "user_id": uid,
        "active_appointments": active,
        "cancelled_appointments": cancelled,
        "total_active": len(active),
        "total_cancelled": len(cancelled),
    }


def cancel_appointment(appointment_id: str) -> dict:
    """Cancel an appointment by ID."""
    data = _load_db()
    for a in data["appointments"]:
        if a["appointment_id"] == appointment_id:
            if a["status"] == "cancelled":
                return {"status": "already_cancelled", "message": "Already cancelled."}
            a["status"] = "cancelled"
            a["modified_at"] = datetime.now().isoformat()
            _save_db(data)
            logger.info("Cancelled: %s", appointment_id)
            return {
                "status": "cancelled",
                "appointment_id": appointment_id,
                "date": a["date"],
                "time": a["time"],
                "message": f"Appointment on {a['date']} at {a['time']} cancelled."
            }
    return {"status": "not_found", "message": f"No appointment with ID {appointment_id}."}


def modify_appointment(appointment_id: str, new_date: str = "", new_time: str = "") -> dict:
    """Change date/time of an existing appointment."""
    if not new_date and not new_time:
        return {"status": "error", "message": "Provide a new date or time."}

    data = _load_db()
    for a in data["appointments"]:
        if a["appointment_id"] == appointment_id and a["status"] == "booked":
            target_date = new_date or a["date"]
            target_time = new_time or a["time"]

            all_slots = _generate_all_slots(target_date)
            if target_time not in all_slots:
                return {"status": "invalid_slot", "message": f"'{target_time}' is not valid."}

            booked = _get_booked_slots(data, target_date)
            if a["date"] == target_date and a["time"] in booked:
                booked.discard(a["time"])
            if target_time in booked:
                available = [s for s in all_slots if s not in booked]
                return {
                    "status": "conflict",
                    "message": f"{target_time} on {target_date} is taken.",
                    "suggested_alternatives": available[:5],
                }

            old_date, old_time = a["date"], a["time"]
            a["date"] = target_date
            a["time"] = target_time
            a["modified_at"] = datetime.now().isoformat()
            _save_db(data)
            logger.info("Modified %s: %s %s → %s %s", appointment_id, old_date, old_time, target_date, target_time)
            return {
                "status": "modified",
                "appointment_id": appointment_id,
                "old_date": old_date, "old_time": old_time,
                "new_date": target_date, "new_time": target_time,
                "message": f"Moved from {old_date} {old_time} to {target_date} {target_time}."
            }

    return {"status": "not_found", "message": f"No active appointment with ID {appointment_id}."}


def end_conversation() -> dict:
    return {"status": "ended", "message": "Conversation ended. Goodbye!"}


# ── OpenAI Tool definitions ─────────────────────────────────────────────

APPOINTMENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "identify_user",
            "description": "Identify a user by their 4-digit ID. Call this FIRST when user provides their ID. Returns profile and active appointments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "4-digit user ID"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_user",
            "description": "Register a brand new user. Generates a 4-digit ID for them. Use when identify_user returns not_found and user wants to register.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full name of the new user"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_slots",
            "description": "Get available appointment slots for a date. ALWAYS call this BEFORE booking to check availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book a new appointment. MUST call fetch_slots first. time_slot MUST be from available slots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "4-digit user ID"},
                    "name": {"type": "string", "description": "Full name"},
                    "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                    "time_slot": {"type": "string", "description": "Time slot from fetch_slots (e.g. '10:00 AM')"},
                    "purpose": {"type": "string", "description": "Reason (optional)"},
                },
                "required": ["user_id", "name", "date", "time_slot"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_appointments",
            "description": "Fetch all appointments for a user by their 4-digit ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "4-digit user ID"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an appointment by its appointment ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string", "description": "8-char appointment ID"},
                },
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_appointment",
            "description": "Change date/time of an existing appointment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string", "description": "8-char appointment ID"},
                    "new_date": {"type": "string", "description": "New date YYYY-MM-DD (optional)"},
                    "new_time": {"type": "string", "description": "New time slot (optional)"},
                },
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_conversation",
            "description": "End the call. Use when user says bye, goodbye, done, that's all, thank you, etc.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_DISPATCH = {
    "identify_user": identify_user,
    "register_user": register_user,
    "fetch_slots": fetch_slots,
    "book_appointment": book_appointment,
    "retrieve_appointments": retrieve_appointments,
    "cancel_appointment": cancel_appointment,
    "modify_appointment": modify_appointment,
    "end_conversation": end_conversation,
}
