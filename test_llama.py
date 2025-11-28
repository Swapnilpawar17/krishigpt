# test_llama.py
# This file tests if your Groq API connection is working

import os
from dotenv import load_dotenv
from groq import Groq

# Step 1: Load the API key from .env file
print("Loading API key...")
load_dotenv()

# Step 2: Get the API key
api_key = os.getenv("GROQ_API_KEY")

# Step 3: Check if API key was found
if not api_key:
    print("❌ ERROR: API key not found!")
    print("Make sure your .env file exists and contains: GROQ_API_KEY=your_key")
    exit()
else:
    print(f"✅ API key found! (starts with: {api_key[:10]}...)")

# Step 4: Connect to Groq
print("\nConnecting to Groq AI...")
try:
    client = Groq(api_key=api_key)
    print("✅ Connected to Groq!")
except Exception as e:
    print(f"❌ ERROR connecting to Groq: {e}")
    exit()

# Step 5: Send a test message to Llama
print("\nSending test message to Llama AI...")
print("Question: टमाटर की फसल में पत्ते पीले हो रहे हैं, क्या करूं?")
print("-" * 50)

try:
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": "You are an expert Indian agricultural advisor. Answer in Hindi. Keep response short (3-4 points)."
            },
            {
                "role": "user",
                "content": "टमाटर की फसल में पत्ते पीले हो रहे हैं, क्या करूं?"
            }
        ],
        temperature=0.7,
        max_tokens=500
    )
    
    # Step 6: Print the AI response
    print("\n🤖 Llama AI Response:")
    print("=" * 50)
    print(response.choices[0].message.content)
    print("=" * 50)
    
    print("\n✅ SUCCESS! Your Llama AI connection is working perfectly!")
    print("You can now proceed to build KrishiGPT.")
    
except Exception as e:
    print(f"❌ ERROR getting response: {e}")
    print("\nTroubleshooting:")
    print("1. Check your internet connection")
    print("2. Verify your API key is correct")
    print("3. Try again in a few seconds")