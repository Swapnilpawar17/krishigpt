# ai_engine.py
# KrishiGPT - AI Agricultural Advisor Engine

import os
import json
import time
import logging
import httpx
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("krishigpt.engine")

class KrishiGPT:
    """
    KrishiGPT - AI Agricultural Advisor
    Supports Hindi and Marathi
    """
    def __init__(self):
        print("🌾 Initializing KrishiGPT...")
        self.client = self._build_groq_client()
        self.model = self._find_working_model()
        print(f"✅ Using model: {self.model}")

        self.system_prompt = self._load_system_prompt()
        print("✅ System prompt loaded")

        self.crop_data = self._load_crop_data()
        print(f"✅ Crop database loaded ({len(self.crop_data.get('crops', {}))} crops)")

        self.conversations = {}
        self.ai_ready = True
        print("🚀 KrishiGPT is ready!\n")

    # ----- Setup helpers -----

    def _build_groq_client(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables!")
        proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        http_client = httpx.Client(proxies=proxy_url, timeout=30.0) if proxy_url else None
        return Groq(api_key=api_key, http_client=http_client)

    def _find_working_model(self):
        # 1) explicit env override
        env_model = (os.getenv("LLM_MODEL") or "").strip()
        if env_model:
            try:
                self.client.chat.completions.create(
                    model=env_model,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=5,
                    temperature=0,
                )
                return env_model
            except Exception as e:
                logger.warning(f"LLM_MODEL '{env_model}' failed: {e}. Falling back…")

        # 2) cached previous (ephemeral on container)
        if os.path.exists("working_model.txt"):
            try:
                with open("working_model.txt", "r") as f:
                    saved = f.read().strip()
                if saved:
                    self.client.chat.completions.create(
                        model=saved,
                        messages=[{"role": "user", "content": "test"}],
                        max_tokens=5,
                        temperature=0,
                    )
                    return saved
            except Exception:
                pass

        # 3) fallback list known to work on Groq
        models_to_try = [
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
        ]
        for m in models_to_try:
            try:
                self.client.chat.completions.create(
                    model=m, messages=[{"role": "user", "content": "test"}], max_tokens=5, temperature=0
                )
                with open("working_model.txt", "w") as f:
                    f.write(m)
                return m
            except Exception:
                continue
        raise RuntimeError("No working model found on Groq!")

    def _load_system_prompt(self):
        prompt_path = os.getenv("SYSTEM_PROMPT_PATH", "prompts/system_prompt.txt")
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        # Default
        return (
            "तुम KrishiGPT हो - भारतीय किसानों के लिए AI कृषि सलाहकार। "
            "हिंदी और मराठी में जवाब दो। व्यावहारिक, सुरक्षित, और स्पष्ट सलाह दो। "
            "यदि जानकारी अधूरी हो तो आवश्यक विवरण मांगो।"
        )

    def _load_crop_data(self):
        data_path = os.getenv("CROP_DATA_PATH", "prompts/crop_data.json")
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"crops": {}, "government_schemes": [], "emergency_contacts": {}}

    # ----- NLU helpers -----

    def _detect_crop(self, query):
        q = query.lower()
        for crop_key, crop_info in self.crop_data.get("crops", {}).items():
            for kw in crop_info.get("keywords", []):
                if kw.lower() in q:
                    return crop_key, crop_info
        return None, None

    def _detect_query_type(self, query):
        q = query.lower()
        disease_kw = ["रोग", "बीमारी", "कीट", "सुंडी", "मक्खी", "इलाज", "उपचार",
                      "पीला", "पीले", "सूख", "मुरझा", "धब्बे", "छेद", "सड़",
                      "disease", "pest", "treatment", "yellow", "dry", "rot",
                      "अळी", "माशी", "किडा"]
        fert_kw = ["खाद", "उर्वरक", "fertilizer", "यूरिया", "dap", "npk",
                   "पोषक", "nutrient", "खत", "मात्रा", "कितना"]
        scheme_kw = ["योजना", "scheme", "सरकारी", "government", "सब्सिडी",
                     "pm-kisan", "बीमा", "kcc", "क्रेडिट", "loan"]
        irrigation_kw = ["सिंचाई", "पानी", "water", "irrigation", "ड्रिप", "drip", "स्प्रिंकलर"]

        if any(w in q for w in disease_kw): return "disease"
        if any(w in q for w in fert_kw): return "fertilizer"
        if any(w in q for w in scheme_kw): return "scheme"
        if any(w in q for w in irrigation_kw): return "irrigation"
        return "general"

    def _get_relevant_context(self, query):
        parts = []
        crop_key, crop_info = self._detect_crop(query)
        qtype = self._detect_query_type(query)

        if crop_info:
            parts.append(f"\n📌 फसल ({crop_info.get('name_hi', crop_key)}):")
            parts.append(f"   - मौसम: {crop_info.get('season', 'N/A')}")
            parts.append(f"   - पानी: {crop_info.get('water_requirement', 'N/A')}")

            if qtype == "disease":
                parts.append("\n🔬 आम बीमारियां:")
                for disease in crop_info.get("common_diseases", [])[:3]:
                    parts.append(f"\n   {disease.get('name','')}:")
                    parts.append(f"   लक्षण: {disease.get('symptoms','N/A')}")
                    parts.append(f"   कारण: {disease.get('causes','N/A')}")
                    parts.append(f"   उपचार:")
                    for tr in disease.get("treatment", []):
                        parts.append(f"      • {tr}")
                    if "cost_per_acre" in disease:
                        parts.append(f"   खर्च: ₹{disease['cost_per_acre']}/एकड़")

            if qtype in ["fertilizer", "general"]:
                parts.append("\n🌿 खाद अनुसूची:")
                for sch in crop_info.get("fertilizer_schedule", []):
                    parts.append(f"   • {sch.get('stage','')}: {sch.get('fertilizer','')}")
                    if sch.get("cost"):
                        parts.append(f"     खर्च: ₹{sch['cost']}")

        if qtype == "scheme":
            parts.append("\n📋 सरकारी योजनाएं:")
            for sch in self.crop_data.get("government_schemes", []):
                parts.append(f"\n   {sch.get('name','')}:")
                parts.append(f"   लाभ: {sch.get('benefit','N/A')}")
                parts.append(f"   पात्रता: {sch.get('eligibility','N/A')}")
                parts.append(f"   आवेदन: {sch.get('apply','N/A')}")
                if sch.get("helpline"):
                    parts.append(f"   हेल्पलाइन: {sch['helpline']}")

        return "\n".join(parts) if parts else ""

    # ----- Public API -----

    def get_response(self, user_id, query, max_retries=3):
        logger.info(f"User {user_id}: {query[:80]}...")
        self.conversations.setdefault(user_id, [])

        crop_context = self._get_relevant_context(query)
        enhanced_prompt = self.system_prompt
        if crop_context:
            enhanced_prompt += "\n\n--- 📚 संबंधित जानकारी ---\n" + crop_context
            enhanced_prompt += "\n\n--- ⚠️ निर्देश ---\nऊपर दी गई जानकारी के आधार पर सुरक्षित और व्यावहारिक सलाह दो।"

        messages = [{"role": "system", "content": enhanced_prompt}]
        messages.extend(self.conversations[user_id][-10:])
        messages.append({"role": "user", "content": query})

        for attempt in range(max_retries):
            try:
                t0 = time.time()
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.4,
                    max_tokens=800,
                    top_p=0.9
                )
                dt = time.time() - t0
                logger.info(f"Response generated in {dt:.2f}s")

                ai_response = (resp.choices[0].message.content or "").strip()
                self.conversations[user_id].append({"role": "user", "content": query})
                self.conversations[user_id].append({"role": "assistant", "content": ai_response})
                self.conversations[user_id] = self.conversations[user_id][-20:]
                return ai_response
            except Exception as e:
                logger.error(f"Attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    return ("❌ माफ करें, तकनीकी समस्या है। कृपया थोड़ी देर बाद प्रयास करें। 🙏\n"
                            "यदि समस्या बनी रहे तो किसान कॉल सेंटर पर कॉल करें: 1551")

    def clear_history(self, user_id):
        self.conversations[user_id] = []
        return True

    def get_quick_info(self, topic):
        t = topic.lower()
        if "योजना" in t or "scheme" in t:
            schemes = self.crop_data.get("government_schemes", [])
            if schemes:
                result = "📋 प्रमुख सरकारी योजनाएं:\n\n"
                for sch in schemes:
                    result += f"🔹 {sch.get('name','')}\n   {sch.get('benefit','')}\n   आवेदन: {sch.get('apply','')}\n\n"
                return result
        if any(k in t for k in ["हेल्पलाइन", "helpline", "संपर्क"]):
            contacts = self.crop_data.get("emergency_contacts", {})
            if contacts:
                result = "📞 महत्वपूर्ण हेल्पलाइन:\n\n"
                result += f"🌾 किसान कॉल सेंटर: {contacts.get('kisan_call_center','N/A')}\n"
                result += f"🔬 KVK: {contacts.get('krishi_vigyan_kendra','N/A')}\n"
                result += f"📱 PM-KISAN: {contacts.get('pm_kisan_helpline','N/A')}\n"
                return result
        return None