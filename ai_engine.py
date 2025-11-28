# ai_engine.py
# KrishiGPT - AI Agricultural Advisor Engine
# This is the brain of KrishiGPT

import os
import json
import time
import logging
from dotenv import load_dotenv
from groq import Groq

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class KrishiGPT:
    """
    KrishiGPT - AI Agricultural Advisor for Indian Farmers
    Supports Hindi and Marathi languages
    """
    
    def __init__(self):
        """Initialize the KrishiGPT engine"""
        
        print("🌾 Initializing KrishiGPT...")
        
        # Initialize Groq client
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables!")
        
        self.client = Groq(api_key=api_key)
        
        # Find working model
        self.model = self._find_working_model()
        print(f"✅ Using model: {self.model}")
        
        # Load system prompt
        self.system_prompt = self._load_system_prompt()
        print("✅ System prompt loaded")
        
        # Load crop knowledge base
        self.crop_data = self._load_crop_data()
        print(f"✅ Crop database loaded ({len(self.crop_data.get('crops', {}))} crops)")
        
        # Store conversation history per user
        self.conversations = {}
        
        print("🚀 KrishiGPT is ready!\n")
    
    def _find_working_model(self):
        """Find a working Llama model on Groq"""
        
        # Check if we saved a working model before
        if os.path.exists("working_model.txt"):
            with open("working_model.txt", "r") as f:
                saved_model = f.read().strip()
                if saved_model:
                    print(f"   Found saved model: {saved_model}")
                    return saved_model
        
        # List of models to try (newest first)
        models_to_try = [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
        ]
        
        for model_name in models_to_try:
            try:
                print(f"   Trying model: {model_name}...")
                self.client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=5
                )
                # Save working model for future use
                with open("working_model.txt", "w") as f:
                    f.write(model_name)
                return model_name
            except Exception as e:
                continue
        
        raise RuntimeError("No working model found on Groq!")
    
    def _load_system_prompt(self):
        """Load the system prompt from file"""
        prompt_path = "prompts/system_prompt.txt"
        
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            # Default prompt if file not found
            return """तुम KrishiGPT हो - भारतीय किसानों के लिए AI कृषि सलाहकार।
            हिंदी और मराठी में जवाब दो। व्यावहारिक सलाह दो।"""
    
    def _load_crop_data(self):
        """Load crop knowledge base from JSON file"""
        data_path = "prompts/crop_data.json"
        
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            return {"crops": {}, "government_schemes": []}
    
    def _detect_crop(self, query):
        """Detect which crop the user is asking about"""
        query_lower = query.lower()
        
        for crop_key, crop_info in self.crop_data.get("crops", {}).items():
            keywords = crop_info.get("keywords", [])
            for keyword in keywords:
                if keyword.lower() in query_lower:
                    return crop_key, crop_info
        
        return None, None
    
    def _detect_query_type(self, query):
        """Detect what type of question the user is asking"""
        query_lower = query.lower()
        
        # Disease/pest related keywords
        disease_keywords = [
            "रोग", "बीमारी", "कीट", "सुंडी", "मक्खी", "इलाज", "उपचार",
            "पीला", "पीले", "सूख", "मुरझा", "धब्बे", "छेद", "सड़",
            "disease", "pest", "treatment", "yellow", "dry", "rot",
            "अळी", "माशी", "किडा", "रोग"
        ]
        
        # Fertilizer related keywords
        fertilizer_keywords = [
            "खाद", "उर्वरक", "fertilizer", "यूरिया", "DAP", "NPK",
            "पोषक", "nutrient", "खत", "मात्रा", "कितना"
        ]
        
        # Government scheme keywords
        scheme_keywords = [
            "योजना", "scheme", "सरकारी", "government", "पैसा", "सब्सिडी",
            "PM-KISAN", "किसान", "बीमा", "KCC", "क्रेडिट", "ऋण", "loan"
        ]
        
        # Irrigation keywords
        irrigation_keywords = [
            "सिंचाई", "पानी", "water", "irrigation", "ड्रिप", "drip",
            "स्प्रिंकलर", "कितना पानी"
        ]
        
        if any(kw in query_lower for kw in disease_keywords):
            return "disease"
        elif any(kw in query_lower for kw in fertilizer_keywords):
            return "fertilizer"
        elif any(kw in query_lower for kw in scheme_keywords):
            return "scheme"
        elif any(kw in query_lower for kw in irrigation_keywords):
            return "irrigation"
        else:
            return "general"
    
    def _get_relevant_context(self, query):
        """Get relevant information from knowledge base based on query"""
        context_parts = []
        
        # Detect crop
        crop_key, crop_info = self._detect_crop(query)
        
        # Detect query type
        query_type = self._detect_query_type(query)
        
        if crop_info:
            context_parts.append(f"\n📌 फसल की जानकारी ({crop_info.get('name_hi', crop_key)}):")
            context_parts.append(f"   - मौसम: {crop_info.get('season', 'N/A')}")
            context_parts.append(f"   - पानी: {crop_info.get('water_requirement', 'N/A')}")
            
            # Add disease information
            if query_type == "disease":
                context_parts.append("\n🔬 आम बीमारियां:")
                for disease in crop_info.get("common_diseases", [])[:3]:
                    context_parts.append(f"\n   {disease.get('name', 'Unknown')}:")
                    context_parts.append(f"   लक्षण: {disease.get('symptoms', 'N/A')}")
                    context_parts.append(f"   कारण: {disease.get('causes', 'N/A')}")
                    context_parts.append(f"   उपचार:")
                    for treatment in disease.get("treatment", []):
                        context_parts.append(f"      • {treatment}")
                    context_parts.append(f"   खर्च: ₹{disease.get('cost_per_acre', 0)}/एकड़")
            
            # Add fertilizer information
            if query_type in ["fertilizer", "general"]:
                context_parts.append("\n🌿 खाद अनुसूची:")
                for schedule in crop_info.get("fertilizer_schedule", []):
                    context_parts.append(f"   • {schedule.get('stage', '')}: {schedule.get('fertilizer', '')}")
                    if schedule.get('cost'):
                        context_parts.append(f"     खर्च: ₹{schedule.get('cost', 0)}")
        
        # Add government scheme information
        if query_type == "scheme":
            context_parts.append("\n📋 सरकारी योजनाएं:")
            for scheme in self.crop_data.get("government_schemes", []):
                context_parts.append(f"\n   {scheme.get('name', 'Unknown')}:")
                context_parts.append(f"   लाभ: {scheme.get('benefit', 'N/A')}")
                context_parts.append(f"   पात्रता: {scheme.get('eligibility', 'N/A')}")
                context_parts.append(f"   आवेदन: {scheme.get('apply', 'N/A')}")
                if scheme.get('helpline'):
                    context_parts.append(f"   हेल्पलाइन: {scheme.get('helpline', '')}")
        
        return "\n".join(context_parts) if context_parts else ""
    
    def get_response(self, user_id, query, max_retries=3):
        """
        Get AI response for user query
        
        Args:
            user_id: Unique identifier for the user (phone number or session ID)
            query: User's question in Hindi/Marathi/English
            max_retries: Number of retries if API fails
        
        Returns:
            AI response string
        """
        
        logger.info(f"User {user_id}: {query[:50]}...")
        
        # Initialize conversation history for new users
        if user_id not in self.conversations:
            self.conversations[user_id] = []
        
        # Get relevant context from knowledge base
        crop_context = self._get_relevant_context(query)
        
        # Build enhanced system prompt with context
        enhanced_prompt = self.system_prompt
        if crop_context:
            enhanced_prompt += f"\n\n--- 📚 संबंधित जानकारी (Knowledge Base से) ---\n{crop_context}"
            enhanced_prompt += "\n\n--- ⚠️ निर्देश ---"
            enhanced_prompt += "\nऊपर दी गई जानकारी का उपयोग करके जवाब दो। अगर जानकारी उपलब्ध है तो उसी के आधार पर बताओ।"
        
        # Build messages list
        messages = [{"role": "system", "content": enhanced_prompt}]
        
        # Add recent conversation history (last 10 messages for context)
        recent_history = self.conversations[user_id][-10:]
        messages.extend(recent_history)
        
        # Add current query
        messages.append({"role": "user", "content": query})
        
        # Try to get response with retries
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1000,
                    top_p=0.9
                )
                
                elapsed = time.time() - start_time
                logger.info(f"Response generated in {elapsed:.2f}s")
                
                ai_response = response.choices[0].message.content
                
                # Update conversation history
                self.conversations[user_id].append({"role": "user", "content": query})
                self.conversations[user_id].append({"role": "assistant", "content": ai_response})
                
                # Keep only last 20 messages per user (memory management)
                if len(self.conversations[user_id]) > 20:
                    self.conversations[user_id] = self.conversations[user_id][-20:]
                
                return ai_response
            
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)  # Wait before retry
                else:
                    return "❌ माफ करें, तकनीकी समस्या है। कृपया थोड़ी देर बाद प्रयास करें। 🙏\n\nअगर समस्या बनी रहे तो किसान कॉल सेंटर पर कॉल करें: 1551"
    
    def clear_history(self, user_id):
        """Clear conversation history for a user"""
        if user_id in self.conversations:
            self.conversations[user_id] = []
            return True
        return False
    
    def get_quick_info(self, topic):
        """Get quick information on a specific topic"""
        
        topic_lower = topic.lower()
        
        # Check for scheme info
        if "योजना" in topic_lower or "scheme" in topic_lower:
            schemes = self.crop_data.get("government_schemes", [])
            if schemes:
                result = "📋 **प्रमुख सरकारी योजनाएं:**\n\n"
                for scheme in schemes:
                    result += f"🔹 **{scheme.get('name', '')}**\n"
                    result += f"   {scheme.get('benefit', '')}\n"
                    result += f"   आवेदन: {scheme.get('apply', '')}\n\n"
                return result
        
        # Check for emergency contacts
        if "हेल्पलाइन" in topic_lower or "helpline" in topic_lower or "संपर्क" in topic_lower:
            contacts = self.crop_data.get("emergency_contacts", {})
            if contacts:
                result = "📞 **महत्वपूर्ण हेल्पलाइन:**\n\n"
                result += f"🌾 किसान कॉल सेंटर: {contacts.get('kisan_call_center', 'N/A')}\n"
                result += f"🔬 कृषि विज्ञान केंद्र: {contacts.get('krishi_vigyan_kendra', 'N/A')}\n"
                result += f"📱 PM-KISAN हेल्पलाइन: {contacts.get('pm_kisan_helpline', 'N/A')}\n"
                return result
        
        return None


# ==================== TEST THE ENGINE ====================

if __name__ == "__main__":
    print("=" * 60)
    print("🌾 KrishiGPT - AI Agricultural Advisor")
    print("=" * 60)
    
    # Initialize KrishiGPT
    try:
        bot = KrishiGPT()
    except Exception as e:
        print(f"❌ Failed to initialize KrishiGPT: {e}")
        exit(1)
    
    # Test queries
    test_queries = [
        "टमाटर की पत्तियां पीली हो रही हैं, क्या करूं?",
        "कपास में गुलाबी सुंडी का इलाज बताओ",
        "PM-KISAN योजना की जानकारी दो",
        "प्याज में थ्रिप्स का उपचार",
        "सोयाबीन में कौन सी खाद डालें?"
    ]
    
    print("\n" + "=" * 60)
    print("🧪 Testing KrishiGPT with sample queries...")
    print("=" * 60)
    
    for i, query in enumerate(test_queries, 1):
        print(f"\n{'='*60}")
        print(f"📝 Test {i}: {query}")
        print("-" * 60)
        
        response = bot.get_response(f"test_user_{i}", query)
        
        print(f"\n🤖 KrishiGPT Response:")
        print(response)
        print("=" * 60)
        
        # Small delay between queries
        time.sleep(1)
    
    print("\n" + "=" * 60)
    print("✅ All tests completed!")
    print("=" * 60)
    
    # Interactive mode
    print("\n💬 Interactive Mode (type 'quit' to exit)")
    print("-" * 60)
    
    while True:
        user_input = input("\n👨‍🌾 आप: ").strip()
        
        if user_input.lower() in ['quit', 'exit', 'q', 'बंद']:
            print("\n👋 धन्यवाद! KrishiGPT का उपयोग करने के लिए शुक्रिया।")
            break
        
        if not user_input:
            continue
        
        response = bot.get_response("interactive_user", user_input)
        print(f"\n🤖 KrishiGPT: {response}")