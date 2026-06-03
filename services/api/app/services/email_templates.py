"""Email templates for transactional emails.

Keep these simple — plain HTML, no frameworks. Render well in Gmail,
Outlook, Apple Mail. Tested by sending to all three.
"""
import base64
import uuid as _uuid
from datetime import datetime, timedelta, timezone


def magic_link_email(magic_url: str, is_new_user: bool = False) -> tuple[str, str, str]:
    """Build the magic link email.

    Returns (subject, html, text) tuple.
    """
    subject = "Sign in to ReachAI" if not is_new_user else "Welcome to ReachAI — confirm your email"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #FAFAFC; color: #1A1F3D; }}
  .wrap {{ max-width: 520px; margin: 0 auto; padding: 48px 24px; }}
  .card {{ background: #FFFFFF; border: 1px solid #E5E5EE; border-radius: 14px; padding: 36px; }}
  .logo {{ font-size: 18px; font-weight: 600; color: #1A1F3D; margin-bottom: 32px; letter-spacing: -0.02em; }}
  .logo .mark {{ display: inline-block; width: 26px; height: 26px; line-height: 26px; text-align: center; background: #534AB7; color: #FFFFFF; border-radius: 7px; font-weight: 700; margin-right: 6px; vertical-align: middle; font-size: 12px; }}
  h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 14px; color: #0A0E27; letter-spacing: -0.01em; }}
  p {{ font-size: 15px; line-height: 1.6; color: #5F5E5A; margin: 0 0 18px; }}
  .btn {{ display: inline-block; background: #534AB7; color: #FFFFFF !important; text-decoration: none; padding: 13px 28px; border-radius: 8px; font-size: 14px; font-weight: 500; }}
  .small {{ font-size: 13px; color: #888780; }}
  .footer {{ text-align: center; font-size: 12px; color: #888780; margin-top: 32px; }}
  .fallback {{ font-family: "SF Mono", Consolas, monospace; font-size: 12px; background: #F4F4F8; border-radius: 6px; padding: 10px 12px; word-break: break-all; color: #534AB7; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="logo"><span class="mark">R</span>ReachAI</div>
    <h1>{"Welcome — confirm your email" if is_new_user else "Sign in to ReachAI"}</h1>
    <p>{"Click the button below to confirm your email and start setting up your AI booking assistant." if is_new_user else "Click the button below to sign in to your ReachAI account."}</p>
    <p><a class="btn" href="{magic_url}">Sign in to ReachAI →</a></p>
    <p class="small">This link expires in 15 minutes and can only be used once. If you didn't request it, ignore this email — no action needed.</p>
    <p class="small">Button not working? Copy and paste this URL into your browser:</p>
    <div class="fallback">{magic_url}</div>
  </div>
  <div class="footer">© ReachAI · The self-serve AI front desk for SMBs</div>
</div>
</body>
</html>"""

    text = f"""Sign in to ReachAI

{"Welcome! Confirm your email and start setting up your AI booking assistant." if is_new_user else "Click the link below to sign in to your ReachAI account."}

{magic_url}

This link expires in 15 minutes and can only be used once.
If you didn't request it, ignore this email — no action needed.

— ReachAI
"""

    return subject, html, text


def _ics_dt(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y%m%dT%H%M%SZ")


_TZ_ABBR: dict[str, str] = {
    "America/New_York": "ET", "America/Detroit": "ET", "America/Toronto": "ET",
    "America/Chicago": "CT", "America/Winnipeg": "CT",
    "America/Denver": "MT", "America/Boise": "MT",
    "America/Phoenix": "MT",
    "America/Los_Angeles": "PT", "America/Vancouver": "PT",
    "America/Anchorage": "AKT",
    "Pacific/Honolulu": "HT",
    "UTC": "UTC", "Etc/UTC": "UTC",
    "Europe/London": "GMT", "Europe/Dublin": "GMT",
    "Europe/Paris": "CET", "Europe/Berlin": "CET", "Europe/Amsterdam": "CET",
}


def _friendly_dt_local(dt: datetime, tz_name: str | None) -> tuple[str, str]:
    """Return (local_str, utc_str) for display in emails.

    local_str: "Thursday, June 19, 2026 at 8:00 AM CT"
    utc_str:   "1:00 PM UTC"
    """
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        zi = ZoneInfo(tz_name) if tz_name else None
    except Exception:
        zi = None

    utc_dt = dt.astimezone(timezone.utc)
    utc_str = utc_dt.strftime("%-I:%M %p UTC").lstrip("0") if hasattr(utc_dt, "strftime") else utc_dt.strftime("%I:%M %p UTC")
    # Windows-safe strftime (no %-I)
    utc_str = utc_dt.strftime("%I:%M %p UTC").lstrip("0") or "12" + utc_dt.strftime(":%M %p UTC")

    if zi:
        local_dt = dt.astimezone(zi)
        abbr = _TZ_ABBR.get(tz_name or "", tz_name or "UTC")
        local_time = local_dt.strftime("%I:%M %p").lstrip("0") or "12" + local_dt.strftime(":%M %p")
        local_str = local_dt.strftime(f"%A, %B %d, %Y at {local_time} {abbr}")
    else:
        local_str = utc_dt.strftime("%A, %B %d, %Y at %I:%M %p UTC").replace(" 0", " ")

    return local_str, utc_str


def _ics_conference_lines(location_info: dict) -> str:
    """Return provider-specific ICS conference extension lines."""
    raw_type = location_info.get("raw_type", "")
    join_url = location_info.get("join_url", "")
    if not join_url:
        return ""
    if raw_type == "google_conference":
        return f"X-GOOGLE-CONFERENCE:{join_url}\r\n"
    if raw_type == "zoom_conference":
        return f"X-ZOOM-MEETING-URL:{join_url}\r\n"
    if raw_type == "microsoft_teams_conference":
        return f"X-MICROSOFT-SKYPETEAMSMEETINGURL:{join_url}\r\n"
    if raw_type == "webex_conference":
        return f"X-WEBEX-MEETING:{join_url}\r\n"
    return ""


def booking_confirmation_email(
    customer_name: str,
    service_name: str,
    business_name: str,
    business_email: str,
    scheduled_for: datetime,
    duration_minutes: int,
    reschedule_url: str | None = None,
    chat_url: str | None = None,
    location_info: dict | None = None,
    invitee_tz: str | None = None,
) -> tuple[str, str, str, bytes]:
    """Build a booking confirmation email with an .ics calendar attachment.

    Returns (subject, html, text, ics_bytes).
    """
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    end_time = scheduled_for + timedelta(minutes=duration_minutes)
    event_uid = f"{_uuid.uuid4()}@reachai.co"
    now_stamp = _ics_dt(datetime.now(timezone.utc))

    local_str, utc_str = _friendly_dt_local(scheduled_for, invitee_tz)
    time_display = local_str
    if invitee_tz and invitee_tz not in ("UTC", "Etc/UTC"):
        time_display = f"{local_str} ({utc_str})"

    loc = location_info or {}
    join_url = loc.get("join_url")
    loc_label = loc.get("label", "")
    loc_icon = loc.get("icon", "📍")
    ics_location = loc.get("ics_location", "")
    is_video = loc.get("is_video", False)
    is_phone = loc.get("is_phone", False)

    # Build join button label based on location type
    if join_url:
        raw_type = loc.get("raw_type", "")
        btn_labels = {
            "google_conference": "Join Google Meet →",
            "zoom_conference": "Join Zoom →",
            "microsoft_teams_conference": "Join Teams →",
            "webex_conference": "Join Webex →",
            "gotomeeting_conference": "Join GoToMeeting →",
        }
        join_btn_label = btn_labels.get(raw_type, "Join meeting →")
        join_btn_html = f'<p><a class="btn" href="{join_url}" style="background:#0F6E56">{join_btn_label}</a></p>'
    else:
        join_btn_html = ""

    # What-to-expect line
    if is_video and join_url:
        expect_text = "A link to join the video call is above. The .ics calendar invite below also contains the meeting link."
    elif is_phone:
        expect_text = f"You'll receive a call at the number you provided: {loc_label}."
    elif loc.get("is_physical"):
        expect_text = f"Your appointment is in person at: {loc_label}."
    else:
        expect_text = "A calendar invite (.ics) is attached — open it to add this appointment to your calendar."

    # Build reschedule buttons HTML
    reschedule_btns = []
    if chat_url:
        reschedule_btns.append(f'<a class="reschedule-btn" href="{chat_url}">💬 Chat to reschedule</a>')
    if reschedule_url:
        reschedule_btns.append(f'<a class="reschedule-btn" href="{reschedule_url}">📅 Pick a new time directly</a>')
    if reschedule_btns:
        reschedule_section = (
            '<div class="reschedule"><h3>Need to reschedule?</h3>'
            '<div class="reschedule-btns">' + "".join(reschedule_btns) + "</div></div>"
        )
    else:
        reschedule_section = '<p class="small">To reschedule or cancel, reply to this email or return to the website chat.</p>'

    # ICS
    ics_location_line = f"LOCATION:{ics_location}\r\n" if ics_location else ""
    ics_url_line = f"URL:{join_url}\r\n" if join_url else ""
    ics_conf_lines = _ics_conference_lines(loc)
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//ReachAI//ReachAI Booking//EN\r\n"
        "METHOD:REQUEST\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{event_uid}\r\n"
        f"DTSTAMP:{now_stamp}\r\n"
        f"DTSTART:{_ics_dt(scheduled_for)}\r\n"
        f"DTEND:{_ics_dt(end_time)}\r\n"
        f"SUMMARY:{service_name} with {business_name}\r\n"
        f"DESCRIPTION:Your {service_name} appointment with {business_name} is confirmed.\r\n"
        f"ORGANIZER;CN={business_name}:mailto:{business_email}\r\n"
        + ics_location_line
        + ics_url_line
        + ics_conf_lines
        + "STATUS:CONFIRMED\r\n"
        "SEQUENCE:0\r\n"
        "BEGIN:VALARM\r\n"
        "TRIGGER:-PT24H\r\n"
        f"DESCRIPTION:Reminder: {service_name} with {business_name}\r\n"
        "ACTION:DISPLAY\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

    subject = f"Confirmed: {service_name} with {business_name}"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body{{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#FAFAFC;color:#1A1F3D}}
  .wrap{{max-width:520px;margin:0 auto;padding:48px 24px}}
  .card{{background:#fff;border:1px solid #E5E5EE;border-radius:14px;padding:36px}}
  .logo{{font-size:18px;font-weight:600;color:#1A1F3D;margin-bottom:32px;letter-spacing:-.02em}}
  .mark{{display:inline-block;width:26px;height:26px;line-height:26px;text-align:center;background:#534AB7;color:#fff;border-radius:7px;font-weight:700;margin-right:6px;vertical-align:middle;font-size:12px}}
  h1{{font-size:22px;font-weight:600;margin:0 0 14px;color:#0A0E27;letter-spacing:-.01em}}
  p{{font-size:15px;line-height:1.6;color:#5F5E5A;margin:0 0 18px}}
  .btn{{display:inline-block;padding:13px 28px;border-radius:8px;font-size:14px;font-weight:500;text-decoration:none;color:#fff!important;background:#534AB7}}
  .box{{background:#F4F4FF;border:1px solid #D4D0F5;border-radius:10px;padding:20px 24px;margin:20px 0}}
  .label{{font-size:12px;font-weight:600;color:#534AB7;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}}
  .val{{font-size:16px;font-weight:600;color:#1A1F3D;margin-bottom:2px}}
  .sub{{font-size:13px;color:#5F5E5A}}
  .small{{font-size:13px;color:#888780}}
  .footer{{text-align:center;font-size:12px;color:#888780;margin-top:32px}}
  .reschedule{{background:#F8F8FC;border:1px solid #E5E5EE;border-radius:10px;padding:18px 22px;margin-top:20px}}
  .reschedule h3{{font-size:14px;font-weight:600;color:#1A1F3D;margin:0 0 12px}}
  .reschedule-btns{{display:flex;gap:10px;flex-wrap:wrap}}
  .reschedule-btn{{display:inline-block;padding:9px 18px;border-radius:8px;font-size:13px;font-weight:500;text-decoration:none;border:1px solid #D4D0F5;color:#534AB7;background:#fff}}
</style>
</head>
<body>
<div class="wrap"><div class="card">
  <div class="logo"><span class="mark">R</span>ReachAI</div>
  <h1>&#10003; Your appointment is confirmed</h1>
  <p>Hi {customer_name}, you're all set! Here are your details:</p>
  <div class="box">
    <div class="label">Service</div>
    <div class="val">{service_name}</div>
    <div class="label" style="margin-top:14px">Date &amp; Time</div>
    <div class="val">{time_display}</div>
    <div class="sub">Duration: {duration_minutes} minutes</div>
    {f'<div class="label" style="margin-top:14px">Location</div><div class="val">{loc_icon} {loc_label}</div>' if loc_label else ""}
  </div>
  {join_btn_html}
  <p>{expect_text}</p>
  {reschedule_section}
  <p class="small">Questions? Reply to this email or contact {business_name} directly.</p>
</div>
<div class="footer">&#169; ReachAI &middot; The AI front desk for SMBs</div>
</div>
</body>
</html>"""

    # Plaintext
    reschedule_text_lines = ["Need to reschedule?"]
    if chat_url:
        reschedule_text_lines.append(f"  Chat: {chat_url}")
    if reschedule_url:
        reschedule_text_lines.append(f"  Pick a new time: {reschedule_url}")
    if not chat_url and not reschedule_url:
        reschedule_text_lines.append("  Reply to this email or return to the website chat.")
    reschedule_text = "\n".join(reschedule_text_lines)

    loc_text_line = f"Location: {loc_icon} {loc_label}\n" if loc_label else ""
    join_text_line = f"Join: {join_url}\n" if join_url else ""

    text = (
        f"Your appointment is confirmed!\n\n"
        f"Hi {customer_name},\n\n"
        f"{service_name} with {business_name}\n"
        f"{time_display}\n"
        f"Duration: {duration_minutes} minutes\n"
        f"{loc_text_line}"
        f"{join_text_line}"
        f"\n{expect_text}\n\n"
        f"{reschedule_text}\n\n"
        f"— {business_name}"
    )

    return subject, html, text, ics.encode("utf-8")
