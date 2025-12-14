# voice_handler.py
# Voice transcription handler for KrishiGPT WhatsApp bot

import os
import tempfile
import requests
import logging

logger = logging.getLogger("krishigpt.voice")

def download_twilio_media(media_url, account_sid, auth_token):
    """
    Download voice note/media from Twilio servers
    Returns: path to temporary file
    """
    try:
        response = requests.get(
            media_url,
            auth=(account_sid, auth_token),
            timeout=30
        )
        response.raise_for_status()
        
        # Determine file extension from content type
        content_type = response.headers.get('Content-Type', 'audio/ogg')
        ext = '.ogg'
        if 'mp3' in content_type:
            ext = '.mp3'
        elif 'wav' in content_type:
            ext = '.wav'
        elif 'mpeg' in content_type:
            ext = '.mp3'
        elif 'amr' in content_type:
            ext = '.amr'
        
        # Save to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        temp_file.write(response.content)
        temp_file.close()
        
        logger.info(f"Downloaded media to {temp_file.name} ({len(response.content)} bytes)")
        return temp_file.name
        
    except Exception as e:
        logger.error(f"Failed to download Twilio media: {e}")
        raise


def transcribe_audio_groq(audio_path):
    """
    Transcribe audio using Groq's Whisper API
    Returns: dict with success, text, language
    """
    try:
        from groq import Groq
        
        groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        
        with open(audio_path, "rb") as audio_file:
            # Groq Whisper API call
            transcription = groq_client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), audio_file.read()),
                model="whisper-large-v3",
                language="hi",  # Hindi - works for Hindi/Marathi
                response_format="text"
            )
        
        # Clean up temp file
        try:
            os.unlink(audio_path)
        except:
            pass
        
        text = str(transcription).strip()
        
        if not text:
            return {
                "success": False,
                "error": "No speech detected",
                "text": None,
                "language": None
            }
        
        return {
            "success": True,
            "text": text,
            "language": detect_language(text),
            "error": None
        }
        
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        # Clean up temp file on error
        try:
            os.unlink(audio_path)
        except:
            pass
        return {
            "success": False,
            "error": str(e),
            "text": None,
            "language": None
        }


def detect_language(text):
    """
    Simple language detection for Hindi/Marathi/English
    Based on presence of Devanagari script
    """
    if not text:
        return "unknown"
    
    # Count Devanagari characters (used in Hindi/Marathi)
    devanagari_count = sum(1 for c in text if '\u0900' <= c <= '\u097F')
    total_alpha = sum(1 for c in text if c.isalpha())
    
    if total_alpha == 0:
        return "unknown"
    
    devanagari_ratio = devanagari_count / total_alpha
    
    if devanagari_ratio > 0.3:
        return "hi"  # Hindi/Marathi (Devanagari script)
    return "en"


def process_voice_message(media_url, account_sid, auth_token):
    """
    Complete pipeline: download -> transcribe -> return text
    Main function to call from webhook
    """
    try:
        # Step 1: Download audio
        audio_path = download_twilio_media(media_url, account_sid, auth_token)
        
        # Step 2: Transcribe
        result = transcribe_audio_groq(audio_path)
        
        return result
        
    except Exception as e:
        logger.exception("Voice processing pipeline failed")
        return {
            "success": False,
            "error": str(e),
            "text": None,
            "language": None
        }