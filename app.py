# app.py
# KrishiGPT - Flask Web Application with WhatsApp Integration + Metrics + Secure API + Dosage Calculator

import os
import uuid
import time
import math
import logging
import json   # NEW
import redis
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, abort
from ai_engine import KrishiGPT
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator
from voice_handler import process_voice_message


# Rate limiting
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("krishigpt")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24)

# Limiter: use Redis if available so limits work across workers
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=os.getenv("REDIS_URL", "memory://")
)

print("\n" + "=" * 60)
print("🌾 Starting KrishiGPT Web Server...")
print("=" * 60 + "\n")

# ---------- Metrics (Redis-backed counters) ----------
uptime_start = time.time()
redis_metrics = None
metrics_local = {}


def _metrics_inc(key, by=1):
    try:
        if redis_metrics:
            redis_metrics.incrby(f"metrics:{key}", by)
        else:
            metrics_local[key] = metrics_local.get(key, 0) + by
    except Exception:
        metrics_local[key] = metrics_local.get(key, 0) + by


def _metrics_get(key):
    try:
        if redis_metrics:
            v = redis_metrics.get(f"metrics:{key}")
            return int(v or 0)
    except Exception:
        pass
    return int(metrics_local.get(key, 0))


def _metrics_snapshot():
    keys = [
        "chat_requests", "chat_success", "chat_errors",
        "wa_inbound", "wa_success", "wa_errors",
        "calc_requests", "calc_success", "calc_errors"
    ]
    return {k: _metrics_get(k) for k in keys}


# Connect metrics Redis (reuse REDIS_URL)
if os.getenv("REDIS_URL"):
    try:
        redis_metrics = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
        redis_metrics.ping()
        print("✅ Metrics Redis connected")
    except Exception as e:
        print(f"⚠️ Metrics Redis not available: {e}")

# Initialize AI
krishigpt = None
try:
    krishigpt = KrishiGPT()
    print("✅ KrishiGPT AI Engine initialized successfully!\n")
except Exception as e:
    print(f"❌ Failed to initialize KrishiGPT: {e}")
    krishigpt = None

# Twilio (optional)
twilio_client = None
twilio_validator = None
try:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if account_sid and auth_token:
        twilio_client = TwilioClient(account_sid, auth_token)
        twilio_validator = RequestValidator(auth_token)
        print("✅ Twilio client initialized\n")
except Exception as e:
    print(f"⚠️ Twilio client not initialized: {e}\n")

# ---------- Helpers ----------

def require_api_key(f):
    @wraps(f)
    def _wrap(*args, **kwargs):
        expected = os.getenv("API_SECRET", "").strip()
        if not expected:
            return f(*args, **kwargs)
        provided = request.headers.get("X-API-Key", "").strip()
        if provided != expected:
            return jsonify({"success": False, "error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return _wrap


def _calc_dose(payload: dict):
    """
    Inputs (JSON):
      - unit: one of ["ml_per_l", "g_per_l", "ml_per_acre", "g_per_acre"]  REQUIRED
      - rate: float (the numeric dose)                                        REQUIRED
      - tank_size_l: float (e.g., 15 or 200)                                  OPTIONAL (needed for per_tank)
      - spray_volume_l_per_acre: float (e.g., 200)                            OPTIONAL (needed for acre/area conversions)
      - area_acre: float (default 1.0)                                        OPTIONAL
      - product: str (optional, just echoed back)
      - farmer: str (optional, for logging)
      - crop_note: str (optional, for logging)
    Output units:
      - ml for ml_* units, g for g_* units
    """
    unit = (payload.get("unit") or "").strip().lower()
    rate = payload.get("rate", None)
    tank_size = float(payload.get("tank_size_l", 0) or 0)
    spray_vol = float(payload.get("spray_volume_l_per_acre", 0) or 0)
    area = float(payload.get("area_acre", 1) or 1)
    product = payload.get("product")

    if unit not in ["ml_per_l", "g_per_l", "ml_per_acre", "g_per_acre"]:
        return None, "invalid unit. Use one of: ml_per_l, g_per_l, ml_per_acre, g_per_acre."
    if rate is None:
        return None, "rate is required."

    # Determine unit symbol for product amount
    amt_unit = "ml" if unit.startswith("ml_") else "g"

    per_liter = None
    per_tank = None
    per_acre = None
    total_area_amt = None
    total_water = None
    tanks_needed = None

    if unit in ["ml_per_l", "g_per_l"]:
        # Given per liter -> derive others if spray volume known
        per_liter = float(rate)
        if tank_size > 0:
            per_tank = per_liter * tank_size
        if spray_vol > 0:
            per_acre = per_liter * spray_vol
            total_water = spray_vol * area
            total_area_amt = per_liter * total_water
            if tank_size > 0:
                tanks_needed = total_water / tank_size
    else:
        # Given per acre -> derive others if spray volume known
        per_acre = float(rate)
        if spray_vol <= 0:
            # Can't derive per liter or per tank without spray volume
            total_area_amt = per_acre * area
        else:
            per_liter = per_acre / spray_vol
            total_water = spray_vol * area
            total_area_amt = per_acre * area
            if tank_size > 0:
                per_tank = per_acre * (tank_size / spray_vol)
                tanks_needed = total_water / tank_size

    # Round nicely
    def r(x):
        if x is None:
            return None
        return round(float(x), 3 if float(x) < 1 else 2)

    result = {
        "input": {
            "product": product,
            "unit": unit,
            "rate": float(rate),
            "tank_size_l": tank_size or None,
            "spray_volume_l_per_acre": spray_vol or None,
            "area_acre": area,
            # echo farmer info
            "farmer": payload.get("farmer"),
            "crop_note": payload.get("crop_note")
        },
        "results": {
            "per_liter": {"amount": r(per_liter), "unit": amt_unit} if per_liter is not None else None,
            "per_tank": {"amount": r(per_tank), "unit": amt_unit} if per_tank is not None else None,
            "per_acre": {"amount": r(per_acre), "unit": amt_unit} if per_acre is not None else None,
            "area_total": {"amount": r(total_area_amt), "unit": amt_unit, "area_acre": area} if total_area_amt is not None else None,
            "total_water_l": r(total_water),
            "tanks_needed": r(tanks_needed)
        },
        "notes": [
            "Always follow the product label and local regulations.",
            "PHI/REI and PPE must be followed. Values here are calculator estimates."
        ]
    }
    return result, None


def _log_notebook_event(payload: dict, result: dict):
    """
    Store a simple agronomy dosage event in Redis (if available).
    Uses redis_metrics (same Redis as metrics).
    Keyed by farmer name or crop_note.
    """
    if not redis_metrics:
        return
    try:
        farmer = (payload.get("farmer") or "").strip()
        crop_note = (payload.get("crop_note") or "").strip()
        key_id = farmer or crop_note or "unknown"
        key = f"notebook:{key_id}"

        event = {
            "ts": int(time.time()),
            "farmer": farmer or None,
            "crop_note": crop_note or None,
            "product": payload.get("product"),
            "unit": payload.get("unit"),
            "rate": payload.get("rate"),
            "tank_size_l": payload.get("tank_size_l"),
            "spray_volume_l_per_acre": payload.get("spray_volume_l_per_acre"),
            "area_acre": payload.get("area_acre"),
            "calc": result.get("results")
        }

        redis_metrics.rpush(key, json.dumps(event))
        ttl = int(os.getenv("NOTEBOOK_TTL", "15552000"))  # 180 days
        redis_metrics.expire(key, ttl)
    except Exception as e:
        logger.warning(f"Notebook logging failed: {e}")

# ---------- Web ----------

@app.route("/")
def home():
    try:
        return render_template("index.html")
    except Exception:
        return jsonify({"service": "KrishiGPT", "message": "Web UI template missing"}), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "KrishiGPT",
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "ai_ready": bool(krishigpt is not None and getattr(krishigpt, "ai_ready", True)),
        "store_ready": bool(krishigpt and getattr(krishigpt, "kv_ready", False)),
        "whatsapp_ready": twilio_client is not None
    })


@app.route("/healthz")
def healthz():
    return health()

# ---------- Metrics route ----------
@app.get("/metrics")
def metrics():
    # Protect if METRICS_TOKEN is set
    token_cfg = os.getenv("METRICS_TOKEN")
    if token_cfg:
        token = request.headers.get("X-Metrics-Token") or request.args.get("token")
        if token != token_cfg:
            return jsonify({"error": "unauthorized"}), 401

    data = _metrics_snapshot()
    data.update({
        "uptime_seconds": round(time.time() - uptime_start, 2),
        "ai_ready": bool(krishigpt and getattr(krishigpt, "ai_ready", True)),
        "store_ready": bool(krishigpt and getattr(krishigpt, "kv_ready", False))
    })
    return jsonify(data)

# ---------- Chat API (open; used by your web UI) ----------
@limiter.limit(os.getenv("CHAT_RATE_PER_MIN", "10 per minute") + "; " +
               os.getenv("CHAT_RATE_PER_DAY", "200 per day"))
@app.route("/api/chat", methods=["POST"])
def chat():
    _metrics_inc("chat_requests")

    if not krishigpt or not getattr(krishigpt, "ai_ready", True):
        _metrics_inc("chat_errors")
        return jsonify({"success": False, "error": "AI Engine not initialized"}), 503

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id") or str(uuid.uuid4())
    message = (data.get("message") or "").strip()
    if not message:
        _metrics_inc("chat_errors")
        return jsonify({"success": False, "error": "Message is required"}), 400

    crop = data.get("crop")
    sowing_date = data.get("sowing_date")
    meta = None
    if crop or sowing_date:
        meta = {"crop": crop, "sowing_date": sowing_date}

    try:
        logger.info(f"Web chat from {user_id}: {message[:80]}...")
        answer = krishigpt.get_response(user_id, message, meta=meta)
        answer += "\n\n---\n⚠️ यह सामान्य सलाह है; स्थानीय लेबल/नियम देखें। संदेह में KVK/कृषि अधिकारी से संपर्क करें।"
        _metrics_inc("chat_success")
        return jsonify({"success": True, "response": answer, "user_id": user_id})
    except Exception as e:
        logger.exception("Error in /api/chat")
        _metrics_inc("chat_errors")
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Chat API (secure; requires X-API-Key) ----------
@require_api_key
@limiter.limit(os.getenv("CHAT_RATE_PER_MIN", "10 per minute") + "; " +
               os.getenv("CHAT_RATE_PER_DAY", "200 per day"))
@app.route("/api/chat-secure", methods=["POST"])
def chat_secure():
    _metrics_inc("chat_requests")

    if not krishigpt or not getattr(krishigpt, "ai_ready", True):
        _metrics_inc("chat_errors")
        return jsonify({"success": False, "error": "AI Engine not initialized"}), 503

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id") or str(uuid.uuid4())
    message = (data.get("message") or "").strip()
    if not message:
        _metrics_inc("chat_errors")
        return jsonify({"success": False, "error": "Message is required"}), 400

    crop = data.get("crop")
    sowing_date = data.get("sowing_date")
    meta = None
    if crop or sowing_date:
        meta = {"crop": crop, "sowing_date": sowing_date}

    try:
        logger.info(f"Secure chat from {user_id}: {message[:80]}...")
        answer = krishigpt.get_response(user_id, message, meta=meta)
        answer += "\n\n---\n⚠️ यह सामान्य सलाह है; स्थानीय लेबल/नियम देखें। संदेह में KVK/कृषि अधिकारी से संपर्क करें।"
        _metrics_inc("chat_success")
        return jsonify({"success": True, "response": answer, "user_id": user_id})
    except Exception as e:
        logger.exception("Error in /api/chat-secure")
        _metrics_inc("chat_errors")
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Dosage calculator (open) ----------
@limiter.limit(os.getenv("CALC_RATE_PER_MIN", "60 per minute"))
@app.route("/api/calc/dose", methods=["POST"])
def calc_dose():
    _metrics_inc("calc_requests")
    try:
        payload = request.get_json(silent=True) or {}
        result, err = _calc_dose(payload)
        if err:
            _metrics_inc("calc_errors")
            return jsonify({"success": False, "error": err}), 400

        _log_notebook_event(payload, result)

        _metrics_inc("calc_success")
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.exception("Error in /api/calc/dose")
        _metrics_inc("calc_errors")
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Dosage calculator (secure) ----------
@require_api_key
@limiter.limit(os.getenv("CALC_RATE_PER_MIN", "60 per minute"))
@app.route("/api/calc/dose-secure", methods=["POST"])
def calc_dose_secure():
    _metrics_inc("calc_requests")
    try:
        payload = request.get_json(silent=True) or {}
        result, err = _calc_dose(payload)
        if err:
            _metrics_inc("calc_errors")
            return jsonify({"success": False, "error": err}), 400

        _log_notebook_event(payload, result)

        _metrics_inc("calc_success")
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.exception("Error in /api/calc/dose-secure")
        _metrics_inc("calc_errors")
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Quick info & Notebook & WhatsApp ----------

@app.route("/api/clear-history", methods=["POST"])
def clear_history():
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "")
    if krishigpt and user_id:
        krishigpt.clear_history(user_id)
    return jsonify({"success": True, "message": "History cleared"})


@app.route("/api/quick-info/<topic>")
def quick_info(topic):
    if not krishigpt or not getattr(krishigpt, "ai_ready", True):
        return jsonify({"success": False, "error": "AI not ready"}), 503
    try:
        info = krishigpt.get_quick_info(topic)
        if info:
            return jsonify({"success": True, "info": info})
        return jsonify({"success": False, "error": "Topic not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/notebook", methods=["GET"])
def notebook_view():
    """
    Notebook viewer (JSON).
    Usage: /notebook?id=<farmer-name-or-crop-note>
    """
    if not redis_metrics:
        return jsonify({
            "success": False,
            "error": "Notebook not available (no Redis configured)."
        }), 503

    key_id = (request.args.get("id") or "").strip()
    if not key_id:
        return jsonify({
            "success": False,
            "error": "Missing 'id' query parameter. Use /notebook?id=<farmer-or-crop-note>."
        }), 400

    key = f"notebook:{key_id}"
    try:
        raw = redis_metrics.lrange(key, 0, -1)
        events = [json.loads(e) for e in raw]
        return jsonify({
            "success": True,
            "id": key_id,
            "count": len(events),
            "events": events
        })
    except Exception as e:
        logger.exception("Error in /notebook")
        return jsonify({"success": False, "error": str(e)}), 500

# Not rate-limited to avoid Twilio retry loops.
@app.route("/whatsapp/webhook", methods=["GET", "POST"])
def whatsapp_webhook():
    if request.method == "GET":
        return jsonify({"status": "WhatsApp webhook is active", "service": "KrishiGPT"})

    _metrics_inc("wa_inbound")

    if twilio_validator:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not twilio_validator.validate(request.url, request.form, signature):
            abort(403)

    try:
        incoming_msg = (request.values.get("Body") or "").strip()
        sender = request.values.get("From", "")  # whatsapp:+919876543210
        sender_name = request.values.get("ProfileName", "किसान")
        sender_short = sender.replace("whatsapp:", "")[-10:] if sender else "Unknown"
        
        # NEW: Check for media (voice notes, images)
        num_media = int(request.values.get("NumMedia", 0))

        logger.info(f"📱 WhatsApp from {sender_short}: msg='{incoming_msg[:50]}...' media={num_media}")

        resp = MessagingResponse()
        msg = resp.message()

        if not krishigpt or not getattr(krishigpt, "ai_ready", True):
            msg.body("❌ सर्वर में तकनीकी समस्या है। कृपया 5 मिनट बाद प्रयास करें।\n\n📞 किसान हेल्पलाइन: 1551")
            _metrics_inc("wa_errors")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # ========== NEW: VOICE MESSAGE HANDLING ==========
        if num_media > 0:
            media_type = request.values.get("MediaContentType0", "")
            media_url = request.values.get("MediaUrl0", "")
            
            logger.info(f"📎 Media received: type={media_type}")
            
            # Handle VOICE NOTES
            if "audio" in media_type.lower() or "ogg" in media_type.lower():
                logger.info("🎤 Processing voice message...")
                
                try:
                    # Get Twilio credentials
                    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
                    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
                    
                    if not account_sid or not auth_token:
                        msg.body("❌ Voice processing not configured. कृपया टेक्स्ट में लिखें।")
                        _metrics_inc("wa_errors")
                        return str(resp), 200, {"Content-Type": "application/xml"}
                    
                    # Process voice message
                    voice_result = process_voice_message(media_url, account_sid, auth_token)
                    
                    if voice_result["success"] and voice_result["text"]:
                        transcribed_text = voice_result["text"]
                        logger.info(f"🎤 Transcribed: {transcribed_text[:100]}...")
                        
                        # Use transcribed text as the query
                        incoming_msg = transcribed_text
                        
                        # Send acknowledgment with transcription
                        # (We'll include this in the final response)
                        
                    else:
                        error_msg = voice_result.get("error", "Unknown error")
                        logger.warning(f"🎤 Transcription failed: {error_msg}")
                        msg.body(f"""❌ आवाज़ समझ नहीं आई। 

कृपया:
• धीरे और साफ़ बोलें
• शोर से दूर रहकर बोलें
• या टेक्स्ट में लिखें

🔄 फिर से कोशिश करें!""")
                        _metrics_inc("wa_errors")
                        return str(resp), 200, {"Content-Type": "application/xml"}
                        
                except Exception as e:
                    logger.exception("Voice processing error")
                    msg.body("❌ आवाज़ प्रोसेस करने में समस्या हुई। कृपया टेक्स्ट में लिखें।\n\n📞 किसान हेल्पलाइन: 1551")
                    _metrics_inc("wa_errors")
                    return str(resp), 200, {"Content-Type": "application/xml"}
            
            # Handle IMAGES (future feature - placeholder)
            elif "image" in media_type.lower():
                msg.body("""📷 फोटो मिला! 

🔜 जल्द ही फोटो से बीमारी पहचान की सुविधा आ रही है।

अभी के लिए:
• बीमारी का विवरण टेक्स्ट में लिखें
• या आवाज़ में बोलकर भेजें 🎤

उदाहरण: "टमाटर के पत्ते पीले हो रहे हैं और उन पर धब्बे हैं" """)
                _metrics_inc("wa_success")
                return str(resp), 200, {"Content-Type": "application/xml"}
            
            # Handle other media types
            else:
                msg.body("🙏 कृपया टेक्स्ट, आवाज़ (🎤) या फोटो भेजें।")
                _metrics_inc("wa_success")
                return str(resp), 200, {"Content-Type": "application/xml"}
        # ========== END VOICE HANDLING ==========

        lower = incoming_msg.lower()

        # Welcome messages
        if lower in ["hi", "hello", "start", "शुरू", "नमस्कार", "हेलो", "हाय", "menu", "help", "मदद"]:
            welcome = f"""🌾 KrishiGPT में आपका स्वागत है, {sender_name}! 🙏

मैं आपका AI कृषि सलाहकार हूं। मुझसे पूछें:
• 🐛 फसल की बीमारी और इलाज
• 💊 खाद-उर्वरक की जानकारी
• 🏛️ सरकारी योजनाएं
• 🦗 कीट नियंत्रण

*कैसे पूछें:*
✍️ टाइप करें - हिंदी या मराठी में
🎤 आवाज़ में बोलें - Voice note भेजें!
📷 फोटो भेजें - (जल्द उपलब्ध)

उदाहरण: "कपास में गुलाबी सुंडी का इलाज"

🔄 रीसेट: "नया" लिखें
💬 अब अपना सवाल पूछें! 👇"""
            msg.body(welcome)
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # Reset/clear history
        if lower in ["clear", "reset", "नया", "नवीन", "new"]:
            krishigpt.clear_history(sender)
            msg.body("✅ बातचीत का इतिहास साफ हो गया।\n\n🔄 अब नया सवाल पूछें!\n\n💡 Tip: आप आवाज़ में भी सवाल पूछ सकते हैं 🎤")
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # Helpline info
        if lower in ["helpline", "हेल्पलाइन", "फोन", "contact", "संपर्क"]:
            helpline = """📞 महत्वपूर्ण हेल्पलाइन:

🌾 किसान कॉल सेंटर: 1551 (टोल फ्री)
📱 PM-KISAN हेल्पलाइन: 155261
🔬 नजदीकी KVK: kvk.icar.gov.in

किसी भी समस्या के लिए 1551 पर कॉल करें।"""
            msg.body(helpline)
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # Schemes info
        if lower in ["योजना", "scheme", "schemes", "योजनाएं", "yojana"]:
            scheme_msg = """📋 प्रमुख सरकारी योजनाएं:

1️⃣ PM-KISAN — ₹6,000/वर्ष
2️⃣ PMFBY — फसल बीमा
3️⃣ KCC — सस्ती ऋण सुविधा

किसी योजना का नाम लिखें विस्तृत जानकारी के लिए।"""
            msg.body(scheme_msg)
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # Empty message
        if not incoming_msg:
            msg.body("""🤔 कोई सवाल नहीं मिला।

अपना सवाल:
✍️ टाइप करें, या
🎤 आवाज़ में बोलकर भेजें!

उदाहरण: "टमाटर में पत्ते पीले हो रहे हैं" """)
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # ========== AI Response ==========
        logger.info("🤖 Generating AI response…")
        
        # Check if this was a voice message (for response formatting)
        was_voice = num_media > 0 and "audio" in request.values.get("MediaContentType0", "").lower()
        
        ai_response = krishigpt.get_response(sender, incoming_msg)
        
        # Truncate if too long for WhatsApp
        if len(ai_response) > 1400:
            ai_response = ai_response[:1350] + "\n\n... (अधिक जानकारी के लिए वेबसाइट देखें)"
        
        # Add voice acknowledgment if it was a voice message
        if was_voice:
            ai_response = f"🎤 *आपने पूछा:* \"{incoming_msg[:100]}{'...' if len(incoming_msg) > 100 else ''}\"\n\n{ai_response}"
        
        # Add footer
        ai_response += "\n\n---\n📞 किसान हेल्पलाइन: 1551"

        msg.body(ai_response)
        logger.info(f"✅ Response sent to {sender_short}")
        _metrics_inc("wa_success")
        return str(resp), 200, {"Content-Type": "application/xml"}

    except Exception as e:
        logger.exception("❌ WhatsApp webhook error")
        resp = MessagingResponse()
        resp.message("❌ माफ करें, तकनीकी समस्या है। कृपया थोड़ी देर बाद प्रयास करें।\n\n📞 किसान हेल्पलाइन: 1551")
        _metrics_inc("wa_errors")
        return str(resp), 200, {"Content-Type": "application/xml"}