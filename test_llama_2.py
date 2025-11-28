# test_llama_2.py
# Second test - testing Marathi language and different query

import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Test 1: Marathi Query
print("🧪 Test 1: Marathi Language")
print("Question: कापसावर गुलाबी बोंड अळी आली आहे, काय करू?")
print("-" * 50)

response1 = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[
        {
            "role": "system",
            "content": "You are an expert Indian agricultural advisor. Answer in Marathi. Keep response short."
        },
        {
            "role": "user",
            "content": "कापसावर गुलाबी बोंड अळी आली आहे, काय करू?"
        }
    ],
    temperature=0.7,
    max_tokens=400
)

print(response1.choices[0].message.content)
print("\n" + "=" * 50 + "\n")

# Test 2: Government Scheme Query
print("🧪 Test 2: Government Scheme Query")
print("Question: PM-KISAN योजना के बारे में बताओ")
print("-" * 50)

response2 = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[
        {
            "role": "system",
            "content": "You are an expert on Indian government schemes for farmers. Answer in Hindi."
        },
        {
            "role": "user",
            "content": "PM-KISAN योजना के बारे में बताओ"
        }
    ],
    temperature=0.7,
    max_tokens=400
)

print(response2.choices[0].message.content)
print("\n" + "=" * 50)

print("\n✅ Both tests completed successfully!")
print("Your Llama AI is ready for KrishiGPT!")