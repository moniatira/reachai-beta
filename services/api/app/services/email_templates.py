"""Email templates for transactional emails.

Keep these simple — plain HTML, no frameworks. Render well in Gmail,
Outlook, Apple Mail. Tested by sending to all three.
"""


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
