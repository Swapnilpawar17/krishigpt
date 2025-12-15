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
        prompt = """तुम्ही एक अनुभवी कृषी शास्त्रज्ञ आहात. या पिकाच्या/झाडाच्या फोटोचे काळजीपूर्वक निरीक्षण करा आणि खालील माहिती मराठीत द्या:

1. **पीक/झाडाची ओळख:** हे कोणते पीक किंवा झाड आहे?

2. **समस्येची ओळख:** 
   - काही रोग दिसत आहे का? (नाव सांगा)
   - काही कीड आहे का? (नाव सांगा)
   - पोषक तत्वांची कमतरता आहे का?
   - किंवा झाड निरोगी आहे?

3. **उपचार/उपाय:**
   - IPM (एकात्मिक कीड व्यवस्थापन) आधारित उपाय प्रथम सांगा
   - नंतर रासायनिक उपचार (औषधाचे नाव आणि प्रमाण)
   - योग्य वेळ आणि पद्धत

4. **सावधानता:** काय काळजी घ्यावी

जर फोटोमध्ये पीक/झाड नसेल किंवा स्पष्ट दिसत नसेल, तर चांगला फोटो पाठवायला सांगा.

संक्षिप्त आणि व्यावहारिक उत्तर द्या जे शेतकरी सहज समजू शकेल."""

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