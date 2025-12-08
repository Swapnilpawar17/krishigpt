# app.py
# KrishiGPT - Flask Web Application with WhatsApp Integration + Metrics + Secure API + Dosage Calculator

import os
import uuid
import time
import math
import logging
import json  # NEW
import redis
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, abort
from ai_engine import KrishiGPT
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator

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
      - farmer: str (optional, for notebook/logging)
      - crop_note: str (optional, for notebook/logging)
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
            # NEW: echo farmer info back
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

    # optional crop & sowing_date for stage-aware answers
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

        # log notebook event
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

        # log notebook event
        _log_notebook_event(payload, result)

        _metrics_inc("calc_success")
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.exception("Error in /api/calc/dose-secure")
        _metrics_inc("calc_errors")
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- Quick info & WhatsApp ----------

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

        logger.info(f"📱 WhatsApp from {sender_short}: {incoming_msg[:80]}...")

        resp = MessagingResponse()
        msg = resp.message()

        if not krishigpt or not getattr(krishigpt, "ai_ready", True):
            msg.body("❌ सर्वर में तकनीकी समस्या है। कृपया 5 मिनट बाद प्रयास करें।\n\n📞 किसान हेल्पलाइन: 1551")
            _metrics_inc("wa_errors")
            return str(resp), 200, {"Content-Type": "application/xml"}

        lower = incoming_msg.lower()

        if lower in ["hi","hello","start","शुरू","नमस्कार","हेलो","हाय","menu","help","मदद"]:
            welcome = f"""🌾 KrishiGPT में आपका स्वागत है, {sender_name}! 🙏

मैं आपका AI कृषि सलाहकार हूं। मुझसे पूछें:
• फसल की बीमारी और इलाज
• खाद-उर्वरक की जानकारी
• सरकारी योजनाएं
• कीट नियंत्रण

कैसे पूछें: बस अपना सवाल हिंदी या मराठी में लिखें।
उदाहरण: "कपास में गुलाबी सुंडी का इलाज" या "टमाटर में पत्ते पीले हैं"

🔄 रीसेट: "नया" लिखें
💬 अब अपना सवाल पूछें! 👇"""
            msg.body(welcome)
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        if lower in ["clear","reset","नया","नवीन","รีसेट","new"]:
            krishigpt.clear_history(sender)
            msg.body("✅ बातचीत का इतिहास साफ हो गया।\n\n🔄 अब नया सवाल पूछें!")
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        if lower in ["helpline","हेल्पलाइन","फोन","contact","संपर्क"]:
            helpline = """📞 महत्वपूर्ण हेल्पलाइन:

🌾 किसान कॉल सेंटर: 1551 (टोल फ्री)
📱 PM-KISAN हेल्पलाइन: 155261
🔬 नजदीकी KVK: kvk.icar.gov.in

किसी भी समस्या के लिए 1551 पर कॉल करें।"""
            msg.body(helpline)
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        if lower in ["योजना","scheme","schemes","योजनाएं","yojana"]:
            scheme_msg = """📋 प्रमुख सरकारी योजनाएं:

1) PM-KISAN — ₹6,000/वर्ष
2) PMFBY — फसल बीमा
3) KCC — सस्ती ऋण सुविधा

किसी योजना का नाम लिखें विस्तृत जानकारी के लिए."""
            msg.body(scheme_msg)
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        if not incoming_msg:
            msg.body("🤔 कृपया अपना सवाल लिखें।\nउदाहरण: टमाटर में पत्ते पीले हो रहे हैं")
            _metrics_inc("wa_success")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # AI response
        logger.info("🤖 Generating AI response…")
        ai_response = krishigpt.get_response(sender, incoming_msg)
        if len(ai_response) > 1500:
            ai_response = ai_response[:1450] + "\n\n... (अधिक जानकारी के लिए वेबसाइट देखें)"
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

# ---------- Docs ----------
@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"success": False, "error": "Server error"}), 500


@app.route("/api/docs")
def api_docs():
    return jsonify({
        "service": "KrishiGPT API",
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "endpoints": {
            "GET /": "Web chat interface",
            "GET /health": "Health check",
            "GET /healthz": "Health check alias",
            "GET /metrics": "Usage counters (protected by METRICS_TOKEN if set)",
            "POST /api/chat": "Web chat API { message, user_id?, crop?, sowing_date? }",
            "POST /api/chat-secure": "Secure chat API (X-API-Key required if API_SECRET is set)",
            "POST /api/calc/dose": "Dosage calculator (open)",
            "POST /api/calc/dose-secure": "Dosage calculator (X-API-Key required if API_SECRET is set)",
            "POST /api/clear-history": "Clear chat history",
            "GET /api/quick-info/<topic>": "Quick info",
            "POST /whatsapp/webhook": "Twilio WhatsApp webhook"
        }
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print(f"🚀 KrishiGPT Server running on http://localhost:{port}")
    print(f"📱 Web Interface: http://127.0.0.1:{port}")
    print(f"📚 API Docs: http://127.0.0.1:{port}/api/docs")
    print(f"💬 WhatsApp Webhook: http://127.0.0.1:{port}/whatsapp/webhook")
    print("=" * 60)
    app.run(host="0.0.0.1", port=port, debug=True)