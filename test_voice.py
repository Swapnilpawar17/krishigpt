import requests
import os

def test_transcription():
    """Test voice transcription locally"""
    from voice_handler import transcribe_audio
    
    # Test with a sample audio file
    test_audio = "test_samples/farmer_query_hindi.ogg"
    
    if os.path.exists(test_audio):
        result = transcribe_audio(test_audio)
        print(f"Transcription: {result}")
    else:
        print("Create test audio file first")

if __name__ == "__main__":
    test_transcription()