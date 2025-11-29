# app.py
# KrishiGPT - Flask Web Application with WhatsApp Integration

import os
import uuid
import logging
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, abort
from ai_engine import KrishiGPT
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("krishigpt")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24)

print("\n" + "=" * 60)
print("🌾 Starting KrishiGPT Web Server...")
print("=" * 60 + "\n")

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
        "ai_ready": bool(krishigpt and getattr(krishigpt, "ai_ready", False)),
        "store_ready": bool(krishigpt and getattr(krishigpt, "kv_ready", False)),
        "whatsapp_ready": twilio_client is not None
    })

@app.route("/healthz")
def healthz():
    return health()

# ---------- Chat API ----------

@app.route("/api/chat", methods=["POST"])
def chat():
    if not krishigpt or not getattr(krishigpt, "ai_ready", False):
        return jsonify({"success": False, "error": "AI Engine not initialized"}), 503

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id") or str(uuid.uuid4())
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": "Message is required"}), 400

    try:
        logger.info(f"Web chat from {user_id}: {message[:80]}...")
        response = krishigpt.get_response(user_id, message)
        return jsonify({"success": True, "response": response, "user_id": user_id})
    except Exception as e:
        logger.exception("Error in /api/chat")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/clear-history", methods=["POST"])
def clear_history():
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "")
    if krishigpt and user_id:
        krishigpt.clear_history(user_id)
    return jsonify({"success": True, "message": "History cleared"})

@app.route("/api/quick-info/<topic>")
def quick_info(topic):
    if not krishigpt or not getattr(krishigpt, "ai_ready", False):
        return jsonify({"success": False, "error": "AI not ready"}), 503
    try:
        info = krishigpt.get_quick_info(topic)
        if info:
            return jsonify({"success": True, "info": info})
        return jsonify({"success": False, "error": "Topic not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ---------- WhatsApp (Twilio) ----------

@app.route("/whatsapp/webhook", methods=["GET", "POST"])
def whatsapp_webhook():
    if request.method == "GET":
        return jsonify({"status": "WhatsApp webhook is active", "service": "KrishiGPT"})

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

        if not krishigpt or not getattr(krishigpt, "ai_ready", False):
            msg.body("❌ सर्वर में तकनीकी समस्या है। कृपया 5 मिनट बाद प्रयास करें।\n\n📞 किसान हेल्पलाइन: 1551")
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
            return str(resp), 200, {"Content-Type": "application/xml"}

        if lower in ["clear","reset","नया","नवीन","रीसेट","new"]:
            krishigpt.clear_history(sender)
            msg.body("✅ बातचीत का इतिहास साफ हो गया।\n\n🔄 अब नया सवाल पूछें!")
            return str(resp), 200, {"Content-Type": "application/xml"}

        if lower in ["helpline","हेल्पलाइन","फोन","contact","संपर्क"]:
            helpline = """📞 महत्वपूर्ण हेल्पलाइन:

🌾 किसान कॉल सेंटर: 1551 (टोल फ्री)
📱 PM-KISAN हेल्पलाइन: 155261
🔬 नजदीकी KVK: kvk.icar.gov.in

किसी भी समस्या के लिए 1551 पर कॉल करें।"""
            msg.body(helpline)
            return str(resp), 200, {"Content-Type": "application/xml"}

        if lower in ["योजना","scheme","schemes","योजनाएं","yojana"]:
            scheme_msg = """📋 प्रमुख सरकारी योजनाएं:

1) PM-KISAN — ₹6,000/वर्ष
2) PMFBY — फसल बीमा
3) KCC — सस्ती ऋण सुविधा

किसी योजना का नाम लिखें विस्तृत जानकारी के लिए."""
            msg.body(scheme_msg)
            return str(resp), 200, {"Content-Type": "application/xml"}

        if not incoming_msg:
            msg.body("🤔 कृपया अपना सवाल लिखें।\nउदाहरण: टमाटर में पत्ते पीले हो रहे हैं")
            return str(resp), 200, {"Content-Type": "application/xml"}

        # AI response
        logger.info("🤖 Generating AI response…")
        ai_response = krishigpt.get_response(sender, incoming_msg)
        if len(ai_response) > 1500:
            ai_response = ai_response[:1450] + "\n\n... (अधिक जानकारी के लिए वेबसाइट देखें)"
        ai_response += "\n\n---\n📞 किसान हेल्पलाइन: 1551"

        msg.body(ai_response)
        logger.info(f"✅ Response sent to {sender_short}")
        return str(resp), 200, {"Content-Type": "application/xml"}

    except Exception as e:
        logger.exception("❌ WhatsApp webhook error")
        resp = MessagingResponse()
        resp.message("❌ माफ करें, तकनीकी समस्या है। कृपया थोड़ी देर बाद प्रयास करें।\n\n📞 किसान हेल्पलाइन: 1551")
        return str(resp), 200, {"Content-Type": "application/xml"}

# ---------- Docs ----------
@app.route("/api/docs")
def api_docs():
    return jsonify({
        "service": "KrishiGPT API",
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "endpoints": {
            "GET /": "Web chat interface",
            "GET /health": "Health check",
            "GET /healthz": "Health check alias",
            "POST /api/chat": "Web chat API { message, user_id? }",
            "POST /api/clear-history": "Clear chat history",
            "GET /api/quick-info/<topic>": "Quick info",
            "POST /whatsapp/webhook": "Twilio WhatsApp webhook"
        }
    })

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"success": False, "error": "Server error"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print(f"🚀 KrishiGPT Server running on http://localhost:{port}")
    print(f"📱 Web Interface: http://127.0.0.1:{port}")
    print(f"📚 API Docs: http://127.0.0.1:{port}/api/docs")
    print(f"💬 WhatsApp Webhook: http://127.0.0.1:{port}/whatsapp/webhook")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=True)