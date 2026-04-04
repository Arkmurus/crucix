"""
ARIA — Zoom Meeting SDK Service  (uses Zoom's built-in transcription)
═══════════════════════════════════════════════════════════════════════════
Your Zoom Business account already transcribes every meeting natively.
The Meeting SDK exposes those caption events directly.
No Deepgram. No Whisper. No audio pipeline. No extra cost.

WHAT ARIA DOES:
  • Receives Zoom's live captions via onClosedCaptionMsgReceived callback
  • Posts compliance alerts in Zoom chat immediately on keyword detection
  • Logs procurement signals to CRUCIX brain in real time
  • Sends full debrief to Telegram the moment the call ends
  • Stores transcript in Redis so ARIA learns from every meeting

ONE-TIME ZOOM SETTING:
  Admin portal → Settings → In Meeting (Advanced)
    ☑ Automated captions   ON
    ☑ Full transcript       ON
    ☑ Save captions         ON

REQUIREMENTS:
  pip install zoom-meeting-sdk httpx redis flask python-dotenv

ENV VARS (only ZOOM credentials are new — everything else already set):
  ZOOM_APP_CLIENT_ID       from https://marketplace.zoom.us
  ZOOM_APP_CLIENT_SECRET   from https://marketplace.zoom.us
  BRAIN_SERVICE_URL        already set (points back to main Crucix app)
  ARIA_INTERNAL_TOKEN      already set
  TELEGRAM_BOT_TOKEN       already set
  TELEGRAM_CHAT_ID         already set
  REDIS_URL                already set
═══════════════════════════════════════════════════════════════════════════
"""

import os, json, re, asyncio, logging, threading, time
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from functools import wraps

import httpx
import redis as redis_lib
from flask import Flask, request as freq, jsonify

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("aria.zoom")

ZOOM_CLIENT_ID     = os.getenv("ZOOM_APP_CLIENT_ID",    "")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_APP_CLIENT_SECRET","")
BRAIN_URL          = os.getenv("BRAIN_SERVICE_URL",     "http://localhost:3117")
INT_TOKEN          = os.getenv("ARIA_INTERNAL_TOKEN",   "aria-internal")
TG_TOKEN           = os.getenv("TELEGRAM_BOT_TOKEN",    "")
TG_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID",      "")
REDIS_URL          = os.getenv("REDIS_URL",             "redis://localhost:6379")
BOT_NAME           = "ARIA — Arkmurus Intelligence"

try:
    rc = redis_lib.from_url(REDIS_URL, decode_responses=True)
    rc.ping()
    log.info("✓ Redis")
except Exception:
    rc = None

COMPLIANCE = [
    "sanction","embargo","ofac","ofsi","itar","dual-use","dual use",
    "export control","export licence","debarment","prohibited party",
    "due diligence","kyc","aml","politically exposed",
    "end user certificate","sitcl","brokering licence","arms embargo",
]
INTEL = [
    "procurement","tender","rfq","rfp","contract","budget",
    "armed forces","ministry of defence","military",
    "angola","mozambique","guinea-bissau","nigeria","kenya",
    "cplp","simportex","fadm","faa","fasb",
    "oem","paramount","elbit","baykar","norinco",
    "counter-ied","uav","drone","surveillance","armoured",
]

MARKETS = {
    "Angola":["angola","luanda","faa","simportex"],
    "Mozambique":["mozambique","maputo","fadm","cabo delgado"],
    "Guinea-Bissau":["guinea-bissau","fasb","bissau"],
    "Nigeria":["nigeria","abuja"],
    "Kenya":["kenya","nairobi"],
    "Middle East":["saudi","uae","qatar","gulf"],
}

def detect_market(text):
    t = text.lower()
    for m,kws in MARKETS.items():
        if any(k in t for k in kws): return m
    return "unknown"


# ── API authentication ────────────────────────────────────────────────────────
def require_internal_token(f):
    """Validates ARIA_INTERNAL_TOKEN via Bearer header only."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = freq.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
        if not token or token != INT_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


class MeetingSession:
    def __init__(self, meeting_id, password, title="Meeting"):
        self.meeting_id   = meeting_id
        self.password     = password
        self.title        = title
        self.started_at   = datetime.now(timezone.utc).isoformat()
        self.running      = False
        self.transcript:  List[Tuple[str,str,str]] = []
        self.participants: Dict[str,str] = {}
        self.zoom_svc     = None
        self.cap_ctrl     = None
        self._lock        = threading.Lock()
        self.last_activity = time.time()

    # ── Join ──────────────────────────────────────────────────────────────────
    def join(self) -> bool:
        try:
            import zoom_meeting_sdk as zoom
        except ImportError:
            log.error("zoom-meeting-sdk not installed. Run: pip install zoom-meeting-sdk")
            return False

        p = zoom.InitAuthSDKParams(
            domain=       "zoom.us",
            client_id=    ZOOM_CLIENT_ID,
            client_secret=ZOOM_CLIENT_SECRET,
        )
        if zoom.InitSDK(p) != zoom.SDKERR_SUCCESS:
            log.error("SDK init failed — check ZOOM_APP_CLIENT_ID / ZOOM_APP_CLIENT_SECRET")
            return False

        self.zoom_svc = zoom.GetMeetingService()

        jp                       = zoom.JoinParam()
        jp.userType              = zoom.SDKUserType.SDK_UT_WITHOUT_LOGIN
        jp.param.isVideoOff      = True    # listen only — no camera
        jp.param.isAudioOff      = False   # ARIA listens, never speaks
        jp.param.userName        = BOT_NAME
        jp.param.meetingNumber   = int(self.meeting_id.replace(" ",""))
        jp.param.psw             = self.password

        if self.zoom_svc.Join(jp) != zoom.SDKERR_SUCCESS:
            log.error("Meeting join failed")
            return False

        # ── Hook into Zoom's built-in live transcription ──────────────────────
        self.cap_ctrl = zoom.GetMeetingClosedCaptionController()
        if self.cap_ctrl:
            session_ref = self

            class CaptionHandler(zoom.IMeetingClosedCaptionEvent):
                def onClosedCaptionMsgReceived(self, text:str, user_id:int, ts:int):
                    """Zoom calls this for every transcribed sentence."""
                    if not text.strip(): return
                    with session_ref._lock:
                        speaker   = session_ref.participants.get(str(user_id),"Unknown")
                        timestamp = datetime.now(timezone.utc).isoformat()
                        session_ref.transcript.append((speaker, text.strip(), timestamp))
                        session_ref.last_activity = time.time()
                    log.info(f"  [{speaker}]: {text.strip()[:100]}")
                    threading.Thread(
                        target=session_ref._on_utterance,
                        args=(speaker, text.strip()),
                        daemon=True
                    ).start()

                def onAssignTranslationMessageReceived(self): pass

            self.cap_ctrl.SetEvent(CaptionHandler())
            try: self.cap_ctrl.StartReceiveClosedCaption()
            except Exception: pass

        self.running = True
        log.info(f"✓ ARIA joined: {self.title} ({self.meeting_id})")
        return True

    # ── Participant tracking ───────────────────────────────────────────────────
    def on_join(self, uid:int, name:str):
        with self._lock:
            self.participants[str(uid)] = name
        log.info(f"  + {name}")
        if name != BOT_NAME:
            self._chat("ARIA is present. I will flag compliance keywords and log intelligence signals in real time.")

    def on_leave(self, uid:int):
        with self._lock:
            left = self.participants.pop(str(uid),'?')
        log.info(f"  - {left} left")

    def on_end(self):
        self.running = False
        log.info("Meeting ended")
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._debrief())
        finally:
            loop.close()

    # ── Real-time utterance processing ────────────────────────────────────────
    def _on_utterance(self, speaker:str, text:str):
        t = text.lower()

        for kw in COMPLIANCE:
            if kw in t:
                msg = f"⚠️ ARIA COMPLIANCE ALERT\nKeyword: '{kw}'\nSpeaker: {speaker}\nVerify before proceeding."
                self._chat(msg)
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._tg(
                        f"⚠️ *COMPLIANCE ALERT*\n*{self.title}*\n"
                        f"Keyword: `{kw}` | Speaker: {speaker}\n_{text[:200]}_"
                    ))
                finally:
                    loop.close()
                break

        for kw in INTEL:
            if kw in t:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._signal(speaker, text, kw))
                finally:
                    loop.close()
                break

    def _chat(self, msg:str):
        try:
            import zoom_meeting_sdk as zoom
            ctrl = zoom.GetMeetingChatController()
            if ctrl:
                p          = zoom.SendChatParams()
                p.receiver = zoom.SDKChatMessageType.SDKChatMessageType_To_All
                p.content  = msg[:1000]
                ctrl.SendChatTo(p)
        except Exception as e:
            log.debug(f"Chat: {e}")

    async def _signal(self, speaker:str, text:str, trigger:str):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(f"{BRAIN_URL}/api/brain/signal",
                    headers={"Authorization": f"Bearer {INT_TOKEN}"},
                    json={
                        "content":     text,
                        "source":      f"zoom:{self.title}:{speaker}",
                        "signal_type": "meeting_intelligence",
                        "trigger":     trigger,
                        "market":      detect_market(text),
                    })
        except Exception as e:
            log.warning(f"Signal failed: {e}")

    # ── Debrief ───────────────────────────────────────────────────────────────
    async def _debrief(self):
        with self._lock:
            transcript_copy = list(self.transcript)
            participants_copy = dict(self.participants)

        if not transcript_copy:
            await self._tg(
                f"📋 *{self.title}*\n_No transcript captured._\n"
                "_Check: Zoom admin → Settings → In Meeting (Advanced) → Automated captions ON_"
            )
            return

        participants = list(dict.fromkeys(s for s,_,_ in transcript_copy))
        formatted    = "\n".join(
            f"[{ts[11:16]}] {s}: {t}"
            for s,t,ts in transcript_copy
        )[:12000]

        prompt = f"""You are ARIA. Analyse this Zoom meeting transcript.
MEETING: {self.title} | DATE: {self.started_at[:10]}
PARTICIPANTS: {', '.join(participants)}

{formatted}

Return JSON only:
{{"summary":"...","key_decisions":[],"action_items":[{{"action":"","owner":"","deadline":""}}],"compliance_flags":[],"procurement_signals":[{{"market":"","opportunity":"","urgency":""}}],"intelligence_notes":[],"aria_priority_alert":null}}"""

        analysis = {}
        try:
            # Use /api/aria/chat (JSON, not streaming) via the main Crucix app
            async with httpx.AsyncClient(timeout=90) as c:
                r = await c.post(f"{BRAIN_URL}/api/aria/chat",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {INT_TOKEN}"},
                    json={"message": prompt, "session_id": f"zoom_{self.meeting_id}"})

            if r.status_code == 200:
                data = r.json()
                full = data.get("response", "") or data.get("answer", "")
                m = re.search(r"\{[\s\S]*\}", full)
                if m:
                    analysis = json.loads(m.group())
            else:
                log.error(f"ARIA chat returned {r.status_code}")
                analysis = {"summary": f"Debrief analysis unavailable (HTTP {r.status_code})", "action_items": []}
        except Exception as e:
            log.error(f"Debrief failed: {e}")
            analysis = {"summary": f"Debrief failed: {e}", "action_items": []}

        # Auto-create deals
        auto = []
        for sig in analysis.get("procurement_signals", []):
            if sig.get("market") and sig.get("opportunity"):
                try:
                    async with httpx.AsyncClient(timeout=8) as c:
                        r = await c.post(f"{BRAIN_URL}/api/brain/pipeline/create",
                            headers={"Authorization": f"Bearer {INT_TOKEN}"},
                            json={"market": sig["market"], "opportunity": sig["opportunity"],
                                  "note": f"From meeting: {self.title}"})
                        if r.status_code == 200:
                            auto.append(f"Lead: {sig['market']} — {r.json().get('id', '?')}")
                except Exception as e:
                    log.warning(f"Auto-create deal failed: {e}")

        await self._tg(_fmt(analysis, self.title, participants, auto, self.started_at))

        if rc:
            key = f"crucix:meetings:{self.started_at[:10].replace('-','')}:{self.meeting_id}"
            rc.setex(key, 30*86400, json.dumps({
                "title": self.title, "date": self.started_at[:10],
                "participants": participants, "transcript": transcript_copy,
                "analysis": analysis, "lines": len(transcript_copy)
            }))
            log.info(f"Saved: {key} ({len(transcript_copy)} lines)")

    async def _tg(self, text:str):
        if not TG_TOKEN or not TG_CHAT_ID: return
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": TG_CHAT_ID, "text": text[:4096],
                          "parse_mode": "Markdown", "disable_web_page_preview": True})
        except Exception as e:
            log.warning(f"Telegram send failed: {e}")


def _fmt(a:dict, title:str, parts:list, auto:list, started:str) -> str:
    msg = f"📋 *ARIA MEETING DEBRIEF*\n*{title}* — {started[:10]}\n_{', '.join(parts[:5])}_\n\n"
    if a.get("summary"):     msg += f"*Summary:*\n{a['summary']}\n\n"
    if a.get("key_decisions"):
        msg += "*Decisions:*\n"
        for d in a["key_decisions"][:4]: msg += f"  ✓ {d}\n"
        msg += "\n"
    if a.get("action_items"):
        msg += "*Action Items:*\n"
        for ai in a["action_items"][:6]:
            msg += f"  ☐ {ai.get('action','')}\n"
            parts_ = []
            if ai.get("owner"):    parts_.append(f"Owner: {ai['owner']}")
            if ai.get("deadline"): parts_.append(f"Due: {ai['deadline']}")
            if parts_: msg += f"     _{' | '.join(parts_)}_\n"
        msg += "\n"
    if a.get("compliance_flags"):
        msg += f"⚠️ *Compliance mentions:*\n"
        for f_ in a["compliance_flags"]: msg += f"  • {f_}\n"
        msg += "_Legal review required._\n\n"
    if a.get("procurement_signals"):
        msg += "*Procurement signals:*\n"
        for s in a["procurement_signals"]:
            e = "🔴" if s.get("urgency")=="HIGH" else ("🟠" if s.get("urgency")=="MEDIUM" else "🟡")
            msg += f"  {e} *{s.get('market','?')}* — {s.get('opportunity','')[:70]}\n"
        msg += "\n"
    if auto:
        msg += "*ARIA auto-created:*\n"
        for x in auto: msg += f"  ✅ {x}\n"
        msg += "\n"
    if a.get("aria_priority_alert"):
        msg += f"🚨 *Priority:* {a['aria_priority_alert']}\n\n"
    msg += "_Transcript in CRUCIX. Use /brief for updated pipeline._"
    return msg[:4096]


# ── Flask API ──────────────────────────────────────────────────────────────────
app_api  = Flask("aria_zoom")
sessions: Dict[str, MeetingSession] = {}
_cleanup_lock = threading.Lock()

def _cleanup_stale_sessions():
    """Remove sessions that have been inactive for 12+ hours."""
    cutoff = time.time() - 12 * 3600
    with _cleanup_lock:
        stale = [mid for mid, s in sessions.items()
                 if not s.running and s.last_activity < cutoff]
        for mid in stale:
            del sessions[mid]
            log.info(f"Cleaned up stale session: {mid}")

@app_api.route("/join", methods=["POST"])
@require_internal_token
def api_join():
    _cleanup_stale_sessions()
    d     = freq.json or {}
    url   = d.get("meeting_url","")
    title = d.get("title","Arkmurus Meeting")
    if not url: return jsonify({"error":"meeting_url required"}),400
    m = re.search(r"/j/(\d+)", url)
    if not m: return jsonify({"error":"Cannot parse meeting ID from URL"}),400
    mid   = m.group(1)
    if mid in sessions and sessions[mid].running:
        return jsonify({"error":"Already in this meeting","meeting_id":mid}),409
    pm    = re.search(r"[?&]pwd=([^&]+)", url)
    pwd   = d.get("password","") or (pm.group(1) if pm else "")
    sess  = MeetingSession(mid, pwd, title)
    def _join_and_store():
        if sess.join():
            with _cleanup_lock:
                sessions[mid] = sess
    threading.Thread(target=_join_and_store, daemon=True).start()
    return jsonify({"status":"joining","meeting_id":mid,"bot_name":BOT_NAME})

@app_api.route("/leave", methods=["POST"])
@require_internal_token
def api_leave():
    d   = freq.json or {}
    mid = d.get("meeting_id","")
    with _cleanup_lock:
        s = sessions.pop(mid, None)
    if s:
        s.running = False
        return jsonify({"status":"left","meeting_id":mid})
    return jsonify({"error":"Not found"}),404

@app_api.route("/active", methods=["GET"])
@require_internal_token
def api_active():
    return jsonify({"count":len(sessions),"meetings":[
        {"meeting_id":k,"title":v.title,"started":v.started_at,"lines":len(v.transcript),"running":v.running}
        for k,v in sessions.items()
    ]})

@app_api.route("/transcript/<mid>", methods=["GET"])
@require_internal_token
def api_transcript(mid):
    s = sessions.get(mid)
    if not s:
        if rc:
            keys = rc.keys(f"crucix:meetings:*:{mid}")
            if keys: return jsonify(json.loads(rc.get(sorted(keys)[-1])))
        return jsonify({"error":"Not found"}),404
    with s._lock:
        transcript = list(s.transcript[-50:])
        participants = list(s.participants.values())
    return jsonify({"meeting_id":mid,"title":s.title,
                    "participants":participants,
                    "lines":len(s.transcript),"transcript":transcript})

@app_api.route("/health", methods=["GET"])
def api_health():
    return jsonify({
        "status":"operational","bot":BOT_NAME,
        "active":sum(1 for s in sessions.values() if s.running),
        "total_sessions":len(sessions),
        "transcription":"Zoom built-in — no external service",
        "redis":"ok" if rc else "offline",
        "credentials_set":bool(ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET),
    })

if __name__=="__main__":
    port = int(os.getenv("ZOOM_BOT_PORT",5060))
    log.info(f"ARIA Zoom Service — port {port}")
    log.info("Transcription: Zoom built-in (free, included in Business account)")
    log.info("ENSURE: Zoom admin → Settings → Automated captions → ON")
    app_api.run(host="0.0.0.0", port=port)


# ──────────────────────────────────────────────────────────────────────────────
# Dockerfile.zoom
# ──────────────────────────────────────────────────────────────────────────────
# FROM ubuntu:22.04
# ENV DEBIAN_FRONTEND=noninteractive
# RUN apt-get update && apt-get install -y \
#     python3 python3-pip libpulse0 libpulse-dev \
#     libasound2 libasound2-dev libssl3 libssl-dev \
#     libx11-6 libxext6 libxrender1 wget curl && \
#     rm -rf /var/lib/apt/lists/*
# WORKDIR /app
# COPY requirements_zoom.txt .
# RUN pip3 install --no-cache-dir -r requirements_zoom.txt
# COPY aria_zoom_service.py .
# ENV PYTHONUNBUFFERED=1
# EXPOSE 5060
# CMD ["python3", "aria_zoom_service.py"]
#
# ──────────────────────────────────────────────────────────────────────────────
# requirements_zoom.txt  (4 packages — no audio/ML)
# ──────────────────────────────────────────────────────────────────────────────
# zoom-meeting-sdk>=0.0.27
# httpx>=0.27.0
# redis>=5.0.0
# flask>=3.0.0
# python-dotenv>=1.0.0
