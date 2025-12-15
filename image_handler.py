# image_handler.py
# Image-based crop disease diagnosis for KrishiGPT

import os
import base64
import tempfile
import requests
import logging
import json

logger = logging.getLogger("krishigpt.image")

def download_twilio_media(media_url, account_sid, auth_token):
    """Download image from Twilio servers"""
    try:
        response = requests.get(
            media_url,
            auth=(account_sid, auth_token),
            timeout=30
        )
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        ext = '.jpg'
        if 'png' in content_type:
            ext = '.png'
        elif 'webp' in content_type:
            ext = '.webp'
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        temp_file.write(response.content)
        temp_file.close()
        
        logger.info(f"Downloaded image to {temp_file.name} ({len(response.content)} bytes)")
        return temp_file.name, response.content
        
    except Exception as e:
        logger.error(f"Failed to download Twilio media: {e}")
        raise


def analyze_crop_image_gemini(image_bytes):
    """
    Analyze crop image using Gemini 1.5 Flash Vision API
    Returns disease diagnosis in Hindi
    """
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {
                "success": False,
                "error": "Gemini API key not configured",
                "diagnosis": None
            }
        
        # Convert image to base64
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        # Gemini API endpoint
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        
        # Prompt for crop disease diagnosis
        prompt = """आप एक अनुभवी कृषि वैज्ञानिक हैं। इस फसल/पौधे की फोटो को ध्यान से देखें और निम्नलिखित जानकारी हिंदी में दें:

1. **पौधे/फसल की पहचान:** यह कौन सी फसल या पौधा है?

2. **समस्या की पहचान:** 
   - क्या कोई बीमारी दिख रही है? (नाम बताएं)
   - क्या कोई कीट का प्रकोप है? (नाम बताएं)
   - क्या पोषक तत्वों की कमी है?
   - या पौधा स्वस्थ है?

3. **उपचार/समाधान:**
   - IPM (एकीकृत कीट प्रबंधन) आधारित उपाय पहले बताएं
   - फिर रासायनिक उपचार (दवाई का नाम और मात्रा)
   - सही समय और तरीका

4. **सावधानियां:** क्या ध्यान रखें

अगर फोटो में फसल/पौधा नहीं है या साफ नहीं दिख रहा, तो बताएं कि बेहतर फोटो भेजें।

संक्षिप्त और व्यावहारिक जवाब दें जो किसान आसानी से समझ सके।"""

        # Request payload
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": image_base64
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "topK": 32,
                "topP": 1,
                "maxOutputTokens": 1024,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
            ]
        }
        
        # Make API request
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"Gemini API error: {response.status_code} - {response.text}")
            return {
                "success": False,
                "error": f"API error: {response.status_code}",
                "diagnosis": None
            }
        
        # Parse response
        result = response.json()
        
        if "candidates" in result and len(result["candidates"]) > 0:
            diagnosis = result["candidates"][0]["content"]["parts"][0]["text"]
            return {
                "success": True,
                "diagnosis": diagnosis,
                "error": None
            }
        else:
            return {
                "success": False,
                "error": "No diagnosis generated",
                "diagnosis": None
            }
            
    except Exception as e:
        logger.exception("Gemini Vision API error")
        return {
            "success": False,
            "error": str(e),
            "diagnosis": None
        }


def process_crop_image(media_url, account_sid, auth_token):
    """
    Complete pipeline: download image -> analyze -> return diagnosis
    """
    try:
        # Download image
        image_path, image_bytes = download_twilio_media(media_url, account_sid, auth_token)
        
        # Analyze with Gemini
        result = analyze_crop_image_gemini(image_bytes)
        
        # Cleanup temp file
        try:
            os.unlink(image_path)
        except:
            pass
        
        return result
        
    except Exception as e:
        logger.exception("Image processing pipeline failed")
        return {
            "success": False,
            "error": str(e),
            "diagnosis": None
        }