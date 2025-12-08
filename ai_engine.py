# ai_engine.py
# KrishiGPT - AI Agricultural Advisor Engine (Groq SDK + Redis persistence)

import os
import json
import time
import logging
import redis
from datetime import datetime, date
from dotenv import load_dotenv
from groq import Groq

# Simple crop stage rules (DAS = days after sowing)
# You can refine ranges later.
STAGE_RULES = {
    "cotton": [
        (0, 20, "अंकुरण / उगवण"),
        (21, 45, "वाढीची अवस्था"),
        (46, 80, "फुलोरा / चौकट"),
        (81, 130, "बॉल विकास"),
        (131, 999, "कापणी जवळ")
    ],
    "tomato": [
        (0, 20, "रोपांची वाढ / रोपवाटिका"),
        (21, 40, "रोपांची वाढ / फुटवे"),
        (41, 70, "फुलोरा"),
        (71, 110, "फळ विकास"),
        (111, 999, "कापणी जवळ")
    ],
    "onion": [
        (0, 25, "अंकुरण / रोप वाढ"),
        (26, 50, "वाढीची अवस्था"),
        (51, 80, "कंद विकास"),
        (81, 999, "कापणी जवळ")
    ],
    "soybean": [
        (0, 15, "अंकुरण / उगवण"),
        (16, 35, "शाकीय वाढ"),
        (36, 65, "फुलोरा / फळधारणा"),
        (66, 999, "शेंगा भरणे / कापणी जवळ")
    ]
    # You can add more crops later.
}


def parse_date_str(date_str: str):
    """Try to parse sowing date from common formats."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Env
load_dotenv()


class KrishiGPT:
    """
    KrishiGPT - AI Agricultural Advisor for Indian Farmers
    Supports Hindi and Marathi languages
    """
    def __init__(self):
        print("🌾 Initializing KrishiGPT...")

        # ---- LLM client (Groq) ----
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables!")
        self.client = Groq(api_key=api_key)

        # Pick a working model (env override → cached → fallback list)
        self.model = self._find_working_model()
        print(f"✅ Using model: {self.model}")

        # ---- Prompts & KB ----
        self.system_prompt = self._load_system_prompt()
        print("✅ System prompt loaded")

        self.crop_data = self._load_crop_data()
        print(f"✅ Crop database loaded ({len(self.crop_data.get('crops', {}))} crops)")

        # ---- Memory stores ----
        self.conversations = {}  # in-memory fallback

        # Redis (Render Key Value)
        self.redis = None
        self.kv_ready = False
        self.history_ttl = int(os.getenv("CONV_TTL_SECONDS", "604800"))  # 7 days
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            try:
                self.redis = redis.from_url(redis_url, decode_responses=True)
                self.redis.ping()
                self.kv_ready = True
                print("✅ Redis connected")
            except Exception as e:
                print(f"⚠️ Redis not available: {e}")
                self.redis = None
                self.kv_ready = False

        # Mark AI ready after a quick ping
        self.ai_ready = True
        print("🚀 KrishiGPT is ready!\n")

    # ---------------- Model selection ----------------
    def _find_working_model(self):
        # 1) Env override
        env_model = (os.getenv("LLM_MODEL") or "").strip()
        if env_model:
            try:
                self.client.chat.completions.create(
                    model=env_model, messages=[{"role": "user", "content": "test"}], max_tokens=5
                )
                return env_model
            except Exception as e:
                logger.warning(f"LLM_MODEL '{env_model}' failed: {e}. Falling back…")

        # 2) Cached previous (ephemeral file)
        if os.path.exists("working_model.txt"):
            try:
                with open("working_model.txt", "r") as f:
                    saved = f.read().strip()
                if saved:
                    self.client.chat.completions.create(
                        model=saved, messages=[{"role": "user", "content": "test"}], max_tokens=5
                    )
                    return saved
            except Exception:
                pass

        # 3) Fallback list
        models_to_try = [
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
        ]
        for model_name in models_to_try:
            try:
                self.client.chat.completions.create(
                    model=model_name, messages=[{"role": "user", "content": "test"}], max_tokens=5
                )
                with open("working_model.txt", "w") as f:
                    f.write(model_name)
                return model_name
            except Exception:
                continue
        raise RuntimeError("No working model found on Groq!")

    # ---------------- Data loading ----------------
    def _load_system_prompt(self):
        prompt_path = "prompts/system_prompt.txt"
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        return (
            "तुम KrishiGPT हो - भारतीय किसानों के लिए AI कृषि सलाहकार। "
            "हिंदी और मराठी में जवाब दो। व्यावहारिक, सुरक्षित, और स्पष्ट सलाह दो।"
        )

    def _load_crop_data(self):
        data_path = "prompts/crop_data.json"
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"crops": {}, "government_schemes": [], "emergency_contacts": {}}

    # ---------------- NLU helpers ----------------
    def _detect_crop(self, query):
        q = query.lower()
        for crop_key, crop_info in self.crop_data.get("crops", {}).items():
            for kw in crop_info.get("keywords", []):
                if kw.lower() in q:
                    return crop_key, crop_info
        return None, None

    def _detect_query_type(self, query):
        q = query.lower()
        disease_keywords = [
            "रोग","बीमारी","कीट","सुंडी","मक्खी","इलाज","उपचार","पीला","पीले","सूख",
            "मुरझा","धब्बे","छेद","सड़","disease","pest","treatment","yellow","dry","rot",
            "अळी","माशी","किडा"
        ]
        fertilizer_keywords = ["खाद","উర్వরक","fertilizer","यूरिया","dap","npk","पोषक","nutrient","खत","मात्रा","कितना"]
        scheme_keywords = ["योजना","scheme","सरकारी","government","सब्सिडी","pm-kisan","बीमा","kcc","क्रेडिट","loan"]
        irrigation_keywords = ["सिंचाई","पानी","water","irrigation","ड्रिप","drip","स्प्रिंकलर"]

        if any(kw in q for kw in disease_keywords): return "disease"
        if any(kw in q for kw in fertilizer_keywords): return "fertilizer"
        if any(kw in q for kw in scheme_keywords): return "scheme"
        if any(kw in q for kw in irrigation_keywords): return "irrigation"
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
                    for treatment in disease.get("treatment", []):
                        parts.append(f"      • {treatment}")
                    if "cost_per_acre" in disease:
                        parts.append(f"   खर्च: ₹{disease['cost_per_acre']}/एकड़")
            if qtype in ["fertilizer","general"]:
                parts.append("\n🌿 खाद अनुसूची:")
                for schedule in crop_info.get("fertilizer_schedule", []):
                    parts.append(f"   • {schedule.get('stage','')}: {schedule.get('fertilizer','')}")
                    if schedule.get('cost'):
                        parts.append(f"     खर्च: ₹{schedule['cost']}")

        if qtype == "scheme":
            parts.append("\n📋 सरकारी योजनाएं:")
            for scheme in self.crop_data.get("government_schemes", []):
                parts.append(f"\n   {scheme.get('name','')}:")
                parts.append(f"   लाभ: {scheme.get('benefit','N/A')}")
                parts.append(f"   पात्रता: {scheme.get('eligibility','N/A')}")
                parts.append(f"   आवेदन: {scheme.get('apply','N/A')}")
                if scheme.get('helpline'):
                    parts.append(f"   हेल्पलाइन: {scheme['helpline']}")

        return "\n".join(parts) if parts else ""

    # -------- Stage helper --------
    def _get_stage_info(self, crop_key: str, sowing_date_str: str):
        """
        Compute approximate crop stage based on sowing date and simple DAS rules.
        crop_key: internal crop key (e.g., "cotton", "tomato")
        sowing_date_str: string date, e.g. "2025-06-15" or "15-06-2025"
        """
        if not crop_key or not sowing_date_str:
            return None

        crop_key = str(crop_key).lower()
        rules = STAGE_RULES.get(crop_key)
        if not rules:
            return None

        sow_date = parse_date_str(sowing_date_str)
        if not sow_date:
            return None

        today = date.today()
        das = (today - sow_date).days
        if das < 0:
            return None

        stage_label = "अवस्था (अंदाजे)"
        for start, end, label in rules:
            if start <= das <= end:
                stage_label = label
                break

        return {"crop": crop_key, "das": das, "label": stage_label}

    # ---------------- Redis helpers ----------------
    def _conv_key(self, user_id):
        return f"conv:{user_id}"

    def _get_history(self, user_id):
        if self.redis:
            try:
                s = self.redis.get(self._conv_key(user_id))
                return json.loads(s) if s else []
            except Exception as e:
                logger.warning(f"Redis get failed, falling back to memory: {e}")
                self.redis = None
                self.kv_ready = False
        return self.conversations.get(user_id, [])

    def _set_history(self, user_id, msgs):
        msgs = msgs[-20:]  # keep last 20
        if self.redis:
            try:
                self.redis.setex(self._conv_key(user_id), self.history_ttl, json.dumps(msgs))
                return
            except Exception as e:
                logger.warning(f"Redis set failed, falling back to memory: {e}")
                self.redis = None
                self.kv_ready = False
        self.conversations[user_id] = msgs
    def _clear_history(self, user_id):
        if self.redis:
            self.redis.delete(self._conv_key(user_id))
        else:
            self.conversations.pop(user_id, None)

    # ---------------- Public API ----------------
    def get_response(self, user_id, query, max_retries=3, meta=None):
        logger.info(f"User {user_id}: {query[:50]}...")
        history = self._get_history(user_id)

        # --- Stage text (optional) ---
        stage_text = ""
        if meta and isinstance(meta, dict):
            crop_key = meta.get("crop_key") or meta.get("crop")
            sowing_date = meta.get("sowing_date")
            stage_info = self._get_stage_info(crop_key, sowing_date)
            # fallback: detect crop from query if not provided
            if not stage_info and sowing_date:
                detected_crop, _ = self._detect_crop(query)
                if detected_crop:
                    stage_info = self._get_stage_info(detected_crop, sowing_date)
            if stage_info:
                stage_text = (
                    f"\n\n--- 🌱 फसल की अवस्था ---\n"
                    f"सध्या अंदाजे {stage_info['das']} दिवस झाले आहेत (DAS). "
                    f"अवस्था: {stage_info['label']}."
                )

        crop_context = self._get_relevant_context(query)
        enhanced_prompt = self.system_prompt

        if stage_text:
            enhanced_prompt += stage_text

        if crop_context:
            enhanced_prompt += f"\n\n--- 📚 संबंधित जानकारी (Knowledge Base से) ---\n{crop_context}"
            enhanced_prompt += "\n\n--- ⚠️ निर्देश ---\nऊपर दी गई जानकारी का उपयोग करके सुरक्षित, व्यावहारिक जवाब दो।"

        messages = [{"role": "system", "content": enhanced_prompt}]
        messages.extend(history[-20:])  # last 20 messages
        messages.append({"role": "user", "content": query})

        for attempt in range(max_retries):
            try:
                t0 = time.time()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1000,
                    top_p=0.9
                )
                dt = time.time() - t0
                logger.info(f"Response generated in {dt:.2f}s")

                ai_response = response.choices[0].message.content

                history.append({"role": "user", "content": query})
                history.append({"role": "assistant", "content": ai_response})
                self._set_history(user_id, history)

                return ai_response
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    return ("❌ माफ करें, तकनीकी समस्या है। कृपया थोड़ी देर बाद प्रयास करें। 🙏\n"
                            "अगर समस्या बनी रहे तो किसान कॉल सेंटर पर कॉल करें: 1551")

    def _clear_history(self, user_id):
        if self.redis:
            try:
                self.redis.delete(self._conv_key(user_id))
                return
            except Exception as e:
                logger.warning(f"Redis delete failed, falling back to memory: {e}")
                self.redis = None
                self.kv_ready = False
        self.conversations.pop(user_id, None)

    def get_quick_info(self, topic):
        topic_lower = topic.lower()
        if "योजना" in topic_lower or "scheme" in topic_lower:
            schemes = self.crop_data.get("government_schemes", [])
            if schemes:
                result = "📋 **प्रमुख सरकारी योजनाएं:**\n\n"
                for scheme in schemes:
                    result += f"🔹 **{scheme.get('name', '')}**\n"
                    result += f"   {scheme.get('benefit', '')}\n"
                    result += f"   आवेदन: {scheme.get('apply', '')}\n\n"
                return result
        if "हेल्पलाइन" in topic_lower or "helpline" in topic_lower or "संपर्क" in topic_lower:
            contacts = self.crop_data.get("emergency_contacts", {})
            if contacts:
                result = "📞 **महत्वपूर्ण हेल्पलाइन:**\n\n"
                result += f"🌾 किसान कॉल सेंटर: {contacts.get('kisan_call_center', 'N/A')}\n"
                result += f"🔬 कृषि विज्ञान केंद्र: {contacts.get('krishi_vigyan_kendra', 'N/A')}\n"
                result += f"📱 PM-KISAN हेल्पलाइन: {contacts.get('pm_kisan_helpline', 'N/A')}\n"
                return result
        return None


# Local test runner
if __name__ == "__main__":
    print("=" * 60)
    print("🌾 KrishiGPT - AI Agricultural Advisor")
    print("=" * 60)
    bot = KrishiGPT()

    # Optional: quick stage test
    meta_example = {"crop": "cotton", "sowing_date": "01-10-2025"}  # adjust date as needed

    qs = [
        "टमाटर की पत्तियां पीली हो रही हैं, क्या करूं?",
        "कपास में गुलाबी सुंडी का इलाज बताओ",
        "PM-KISAN योजना की जानकारी दो",
        "प्याज में थ्रिप्स का उपचार",
        "सोयाबीन में कौन सी खाद डालें?"
    ]
    for i, q in enumerate(qs, 1):
        print(f"\n[{i}] {q}")
        # pass meta only if you want stage info; otherwise just bot.get_response(..., q)
        print(bot.get_response(f"test_user_{i}", q, meta=meta_example if i == 2 else None))
        time.sleep(0.5)