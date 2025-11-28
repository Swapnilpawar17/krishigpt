# app.py
# KrishiGPT - Flask Web Application with WhatsApp Integration
# Complete server for Web + WhatsApp

import os
import uuid
from flask import Flask, request, jsonify, render_template, session
from dotenv import load_dotenv
from ai_engine import KrishiGPT
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import logging

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# Initialize KrishiGPT AI Engine
print("\n" + "=" * 60)
print("🌾 Starting KrishiGPT Web Server...")
print("=" * 60 + "\n")

try:
    krishigpt = KrishiGPT()
    print("✅ KrishiGPT AI Engine initialized successfully!\n")
except Exception as e:
    print(f"❌ Failed to initialize KrishiGPT: {e}")
    krishigpt = None

# Initialize Twilio client (optional, for sending messages)
twilio_client = None
try:
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token = os.getenv('TWILIO_AUTH_TOKEN')
    if account_sid and auth_token:
        twilio_client = Client(account_sid, auth_token)
        print("✅ Twilio client initialized\n")
except Exception as e:
    print(f"⚠️ Twilio client not initialized: {e}\n")


# ==================== WEB ROUTES ====================

@app.route('/')
def home():
    """Home page - Render the chat interface"""
    return render_template('index.html')


@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'KrishiGPT',
        'version': '1.0.0',
        'ai_ready': krishigpt is not None,
        'whatsapp_ready': twilio_client is not None
    })


# ==================== CHAT API ROUTES ====================

@app.route('/api/chat', methods=['POST'])
def chat():
    """Main chat API endpoint for web interface"""
    try:
        if krishigpt is None:
            return jsonify({
                'success': False,
                'error': 'AI Engine not initialized'
            }), 500
        
        data = request.json
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        user_id = data.get('user_id', str(uuid.uuid4()))
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({
                'success': False,
                'error': 'Message is required'
            }), 400
        
        logger.info(f"Web chat from {user_id}: {message[:50]}...")
        
        response = krishigpt.get_response(user_id, message)
        
        return jsonify({
            'success': True,
            'response': response,
            'user_id': user_id
        })
    
    except Exception as e:
        logger.error(f"Error in chat: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/clear-history', methods=['POST'])
def clear_history():
    """Clear conversation history"""
    try:
        data = request.json
        user_id = data.get('user_id', '')
        
        if krishigpt and user_id:
            krishigpt.clear_history(user_id)
        
        return jsonify({
            'success': True,
            'message': 'History cleared'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/quick-info/<topic>')
def quick_info(topic):
    """Get quick information on topics"""
    try:
        if krishigpt is None:
            return jsonify({'success': False, 'error': 'AI not ready'}), 500
        
        info = krishigpt.get_quick_info(topic)
        
        if info:
            return jsonify({'success': True, 'info': info})
        else:
            return jsonify({'success': False, 'error': 'Topic not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== WHATSAPP WEBHOOK ====================

@app.route('/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    """
    Webhook for Twilio WhatsApp messages
    
    This endpoint receives incoming WhatsApp messages from Twilio
    and sends back AI responses.
    """
    
    if request.method == 'GET':
        # Health check for webhook
        return jsonify({
            'status': 'WhatsApp webhook is active',
            'service': 'KrishiGPT'
        })
    
    try:
        # Get incoming message details from Twilio
        incoming_msg = request.values.get('Body', '').strip()
        sender = request.values.get('From', '')  # Format: whatsapp:+919876543210
        sender_name = request.values.get('ProfileName', 'किसान')
        
        # Clean sender number for logging
        sender_short = sender.replace('whatsapp:', '')[-10:] if sender else 'Unknown'
        
        logger.info(f"📱 WhatsApp from {sender_short}: {incoming_msg[:50]}...")
        
        # Create Twilio response object
        resp = MessagingResponse()
        msg = resp.message()
        
        # Check if AI is ready
        if krishigpt is None:
            msg.body("❌ सर्वर में तकनीकी समस्या है। कृपया 5 मिनट बाद प्रयास करें।\n\n📞 किसान हेल्पलाइन: 1551")
            return str(resp)
        
        # Handle special commands
        incoming_lower = incoming_msg.lower()
        
        # Welcome/Start commands
        if incoming_lower in ['hi', 'hello', 'start', 'शुरू', 'नमस्कार', 'हेलो', 'हाय', 'menu', 'help', 'मदद']:
            welcome_msg = f"""🌾 *KrishiGPT में आपका स्वागत है!*

नमस्कार {sender_name}! 🙏

मैं आपका AI कृषि सलाहकार हूं। मुझसे पूछें:

✅ फसल की बीमारी और इलाज
✅ खाद-उर्वरक की जानकारी
✅ सरकारी योजनाएं
✅ कीट नियंत्रण

📝 *कैसे पूछें:*
बस अपना सवाल हिंदी या मराठी में लिखें!

*उदाहरण:*
• टमाटर में पत्ते पीले हो रहे हैं
• कपास में गुलाबी सुंडी का इलाज
• PM-KISAN योजना की जानकारी

🔄 बातचीत रीसेट करने के लिए "नया" लिखें

💬 अब अपना सवाल पूछें! 👇"""
            msg.body(welcome_msg)
            return str(resp)
        
        # Reset/Clear commands
        if incoming_lower in ['clear', 'reset', 'नया', 'नवीन', 'रीसेट', 'new']:
            krishigpt.clear_history(sender)
            msg.body("✅ बातचीत का इतिहास साफ हो गया।\n\n🔄 अब नया सवाल पूछें!")
            return str(resp)
        
        # Helpline request
        if incoming_lower in ['helpline', 'हेल्पलाइन', 'फोन', 'contact', 'संपर्क']:
            helpline_msg = """📞 *महत्वपूर्ण हेल्पलाइन:*

🌾 किसान कॉल सेंटर: *1551* (टोल फ्री)

📱 PM-KISAN हेल्पलाइन: *155261*

🏛️ कृषि विभाग महाराष्ट्र: 022-22025024

🔬 नजदीकी KVK खोजें: kvk.icar.gov.in

💡 किसी भी समस्या के लिए 1551 पर कॉल करें - 24x7 उपलब्ध है!"""
            msg.body(helpline_msg)
            return str(resp)
        
        # Scheme information shortcut
        if incoming_lower in ['योजना', 'scheme', 'schemes', 'योजनाएं', 'yojana']:
            scheme_msg = """📋 *प्रमुख सरकारी योजनाएं:*

1️⃣ *PM-KISAN*
   💰 ₹6,000/वर्ष (3 किस्तों में)
   🌐 pmkisan.gov.in

2️⃣ *PM फसल बीमा (PMFBY)*
   🛡️ फसल नुकसान पर मुआवजा
   🌐 pmfby.gov.in

3️⃣ *किसान क्रेडिट कार्ड (KCC)*
   🏦 4% ब्याज पर ऋण
   📍 नजदीकी बैंक में आवेदन करें

4️⃣ *PM कृषि सिंचाई योजना*
   💧 ड्रिप/स्प्रिंकलर पर 55-75% सब्सिडी

📞 जानकारी के लिए: 155261

किसी योजना की विस्तृत जानकारी के लिए उसका नाम लिखें।"""
            msg.body(scheme_msg)
            return str(resp)
        
        # Empty message
        if not incoming_msg:
            msg.body("🤔 कृपया अपना सवाल लिखें।\n\nउदाहरण: टमाटर में पत्ते पीले हो रहे हैं")
            return str(resp)
        
        # Get AI response for the question
        logger.info(f"🤖 Generating AI response for: {incoming_msg[:30]}...")
        
        ai_response = krishigpt.get_response(sender, incoming_msg)
        
        # WhatsApp has 1600 character limit per message
        # If response is too long, truncate it
        if len(ai_response) > 1500:
            ai_response = ai_response[:1450] + "\n\n... (अधिक जानकारी के लिए वेबसाइट देखें)"
        
        # Add footer to response
        ai_response += "\n\n---\n📞 *किसान हेल्पलाइन:* 1551"
        
        msg.body(ai_response)
        
        logger.info(f"✅ Response sent to {sender_short}")
        
        return str(resp)
    
    except Exception as e:
        logger.error(f"❌ WhatsApp webhook error: {e}")
        
        # Send error response
        resp = MessagingResponse()
        msg = resp.message()
        msg.body("❌ माफ करें, तकनीकी समस्या है। कृपया थोड़ी देर बाद प्रयास करें।\n\n📞 किसान हेल्पलाइन: 1551")
        
        return str(resp)


# ==================== API DOCUMENTATION ====================

@app.route('/api/docs')
def api_docs():
    """API documentation"""
    docs = {
        'service': 'KrishiGPT API',
        'version': '1.0.0',
        'description': 'AI Agricultural Advisor for Indian Farmers',
        'endpoints': {
            'GET /': 'Web chat interface',
            'GET /health': 'Health check',
            'POST /api/chat': 'Web chat API',
            'POST /api/clear-history': 'Clear chat history',
            'GET /api/quick-info/<topic>': 'Quick info on topics',
            'POST /whatsapp/webhook': 'Twilio WhatsApp webhook'
        },
        'whatsapp_commands': {
            'hi/hello/start': 'Welcome message',
            'नया/reset': 'Clear conversation',
            'हेल्पलाइन': 'Emergency contacts',
            'योजना': 'Government schemes'
        },
        'supported_crops': [
            'Tomato (टमाटर)', 'Cotton (कपास)', 'Onion (प्याज)',
            'Soybean (सोयाबीन)', 'Wheat (गेहूं)', 'Sugarcane (गन्ना)',
            'Grapes (अंगूर)', 'Pomegranate (अनार)'
        ]
    }
    return jsonify(docs)


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'success': False, 'error': 'Server error'}), 500


# ==================== RUN APP ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    print("=" * 60)
    print(f"🚀 KrishiGPT Server running on http://localhost:{port}")
    print(f"📱 Web Interface: http://127.0.0.1:{port}")
    print(f"📚 API Docs: http://127.0.0.1:{port}/api/docs")
    print(f"💬 WhatsApp Webhook: http://127.0.0.1:{port}/whatsapp/webhook")
    print("=" * 60)
    print("\n💡 Press Ctrl+C to stop the server\n")
    
    app.run(host='0.0.0.0', port=port, debug=True)