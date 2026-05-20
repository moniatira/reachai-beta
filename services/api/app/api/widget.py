"""Widget endpoint - serves the configured chat widget JS per workspace.

When an SMB embeds <script src=".../v1/widget/acme-salon.js"></script>,
this endpoint returns a JS file with the workspace's branding baked in.
"""
import json
import pathlib

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.models import Workspace


router = APIRouter(prefix="/v1/widget", tags=["widget"])
settings = get_settings()

_WIDGET_TEMPLATE_PATH = pathlib.Path(__file__).parent.parent / "static" / "widget.js"


@router.get("/{slug}.js")
async def widget_js(slug: str, db: AsyncSession = Depends(get_db)):
    """Return the chat widget JS with this workspace's config baked in."""
    result = await db.execute(
        select(Workspace).where(
            Workspace.slug == slug,
            Workspace.active == True,
            Workspace.whitelisted == True,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    api_base = settings.calendly_redirect_uri.rsplit("/v1/", 1)[0]

    config = {
        "workspaceSlug": workspace.slug,
        "workspaceName": workspace.name,
        "assistantName": workspace.assistant_name,
        "greeting": workspace.greeting,
        "primaryColor": workspace.brand_primary,
        "apiBase": api_base,
        "logoUrl": workspace.logo_url,
    }

    if _WIDGET_TEMPLATE_PATH.exists():
        template = _WIDGET_TEMPLATE_PATH.read_text()
    else:
        template = _INLINE_WIDGET_JS

    js = f"window.__REACHAI_CONFIG__ = {json.dumps(config)};\n{template}"

    return Response(
        content=js,
        media_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
        },
    )


_INLINE_WIDGET_JS = r"""
(function(){
  var cfg = window.__REACHAI_CONFIG__;
  if (!cfg) { console.error('ReachAI: missing config'); return; }
  if (window.__REACHAI_LOADED__) return;
  window.__REACHAI_LOADED__ = true;

  var primary = cfg.primaryColor || '#534AB7';
  var sessionId = null;

  var css = ''
    + '.rai-bubble{position:fixed;bottom:24px;right:24px;width:60px;height:60px;border-radius:50%;background:' + primary + ';display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.18);z-index:999998;transition:transform .15s}'
    + '.rai-bubble:hover{transform:scale(1.06)}'
    + '.rai-bubble svg{width:28px;height:28px;fill:#fff}'
    + '.rai-panel{position:fixed;bottom:96px;right:24px;width:380px;max-width:calc(100vw - 32px);height:560px;max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.22);display:none;flex-direction:column;overflow:hidden;z-index:999999;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;color:#1A1F3D}'
    + '.rai-panel.open{display:flex}'
    + '.rai-header{background:' + primary + ';color:#fff;padding:16px 20px;display:flex;align-items:center;gap:10px}'
    + '.rai-avatar{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;font-weight:600}'
    + '.rai-header-text{flex:1}'
    + '.rai-header-name{font-weight:600;font-size:15px;line-height:1.2}'
    + '.rai-header-sub{font-size:12px;opacity:.85;margin-top:2px}'
    + '.rai-close{background:transparent;border:none;color:#fff;cursor:pointer;padding:4px;font-size:18px;line-height:1;opacity:.75}'
    + '.rai-close:hover{opacity:1}'
    + '.rai-body{flex:1;padding:18px;overflow-y:auto;background:#FAFAFC}'
    + '.rai-msg{margin-bottom:10px;display:flex}'
    + '.rai-msg.user{justify-content:flex-end}'
    + '.rai-bubble-msg{padding:9px 14px;border-radius:14px;max-width:80%;line-height:1.5;word-wrap:break-word}'
    + '.rai-msg.bot .rai-bubble-msg{background:#fff;border:1px solid #E5E5EE;border-bottom-left-radius:4px}'
    + '.rai-msg.user .rai-bubble-msg{background:' + primary + ';color:#fff;border-bottom-right-radius:4px}'
    + '.rai-msg.bot .rai-bubble-msg a{color:' + primary + ';font-weight:600}'
    + '.rai-typing{display:inline-flex;gap:3px;padding:11px 14px}'
    + '.rai-typing span{width:6px;height:6px;border-radius:50%;background:#888;opacity:.5;animation:rai-blink 1.4s infinite}'
    + '.rai-typing span:nth-child(2){animation-delay:.2s}'
    + '.rai-typing span:nth-child(3){animation-delay:.4s}'
    + '@keyframes rai-blink{0%,100%{opacity:.3}50%{opacity:1}}'
    + '.rai-input-row{display:flex;gap:8px;padding:12px;border-top:1px solid #E5E5EE;background:#fff}'
    + '.rai-input{flex:1;padding:10px 14px;border-radius:20px;border:1px solid #D4D4DE;font-size:14px;outline:none;font-family:inherit}'
    + '.rai-input:focus{border-color:' + primary + '}'
    + '.rai-send{background:' + primary + ';color:#fff;border:none;border-radius:50%;width:38px;height:38px;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:16px}'
    + '.rai-send:disabled{opacity:.4;cursor:not-allowed}'
    + '.rai-footer{text-align:center;padding:8px;font-size:11px;color:#888;background:#fff;border-top:1px solid #F4F4F8}'
    + '.rai-footer a{color:' + primary + ';font-weight:600;text-decoration:none}'
    + '@media (max-width:480px){.rai-panel{bottom:0;right:0;left:0;top:0;width:100%;height:100%;max-width:none;max-height:none;border-radius:0}}';

  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  var bubble = document.createElement('div');
  bubble.className = 'rai-bubble';
  bubble.setAttribute('role', 'button');
  bubble.setAttribute('aria-label', 'Open chat to book an appointment');
  bubble.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>';

  var panel = document.createElement('div');
  panel.className = 'rai-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', cfg.workspaceName + ' chat');

  var initials = cfg.assistantName.split(' ').map(function(p){return p[0]}).join('').slice(0, 2).toUpperCase();

  panel.innerHTML = ''
    + '<div class="rai-header">'
    +   '<div class="rai-avatar">' + initials + '</div>'
    +   '<div class="rai-header-text">'
    +     '<div class="rai-header-name">' + cfg.assistantName + ' · ' + cfg.workspaceName + '</div>'
    +     '<div class="rai-header-sub">Usually replies in seconds</div>'
    +   '</div>'
    +   '<button class="rai-close" aria-label="Close chat">×</button>'
    + '</div>'
    + '<div class="rai-body"></div>'
    + '<form class="rai-input-row">'
    +   '<input class="rai-input" placeholder="Type your message…" autocomplete="off" maxlength="2000">'
    +   '<button class="rai-send" type="submit" aria-label="Send">→</button>'
    + '</form>'
    + '<div class="rai-footer">Powered by <a href="https://moniatira.github.io/bookring/" target="_blank">ReachAI</a></div>';

  document.body.appendChild(bubble);
  document.body.appendChild(panel);

  var body = panel.querySelector('.rai-body');
  var input = panel.querySelector('.rai-input');
  var form = panel.querySelector('.rai-input-row');
  var sendBtn = panel.querySelector('.rai-send');
  var closeBtn = panel.querySelector('.rai-close');

  function escapeHtml(str){
    return str.replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function linkify(str){
    return str.replace(/(https?:\/\/[^\s<]+)/g, function(url){
      return '<a href="' + url + '" target="_blank" rel="noopener">' + url + '</a>';
    });
  }

  function addMessage(role, text){
    var msg = document.createElement('div');
    msg.className = 'rai-msg ' + role;
    var bubbleEl = document.createElement('div');
    bubbleEl.className = 'rai-bubble-msg';
    bubbleEl.innerHTML = linkify(escapeHtml(text));
    msg.appendChild(bubbleEl);
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
    return msg;
  }

  function addTyping(){
    var msg = document.createElement('div');
    msg.className = 'rai-msg bot';
    msg.innerHTML = '<div class="rai-bubble-msg"><div class="rai-typing"><span></span><span></span><span></span></div></div>';
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
    return msg;
  }

  function open(){
    panel.classList.add('open');
    setTimeout(function(){ input.focus(); }, 50);
    if (body.children.length === 0) {
      addMessage('bot', cfg.greeting || 'Hi! How can I help you today?');
    }
  }

  function close(){ panel.classList.remove('open'); }

  bubble.addEventListener('click', open);
  closeBtn.addEventListener('click', close);

  form.addEventListener('submit', function(e){
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;

    addMessage('user', text);
    input.value = '';
    sendBtn.disabled = true;
    var typing = addTyping();

    fetch(cfg.apiBase + '/v1/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        workspace_slug: cfg.workspaceSlug,
        session_id: sessionId,
        message: text
      })
    })
    .then(function(r){
      if (!r.ok) return r.json().then(function(e){ throw new Error(e.detail || 'Chat error'); });
      return r.json();
    })
    .then(function(data){
      sessionId = data.session_id;
      typing.remove();
      addMessage('bot', data.reply);
    })
    .catch(function(err){
      typing.remove();
      addMessage('bot', 'Sorry, something went wrong. Please try again or call us directly.');
      console.error('ReachAI chat error:', err);
    })
    .finally(function(){
      sendBtn.disabled = false;
      input.focus();
    });
  });
})();
"""
