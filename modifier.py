
import os
import time
from openai import OpenAI

# Initialize the client safely using environment variables
HF_API_TOKEN = os.getenv("HF_TOKEN", "")

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=HF_API_TOKEN,
)

def enhance_query_for_graphrag(user_query: str) -> str:
    """Rewrite user input into a retrieval-optimized GraphRAG query string.

    Uses Hugging Face Router API for fast, reliable, serverless processing.
    """
    start_time = time.time()

    # Call the model using the clean OpenAI SDK structure
    completion = client.chat.completions.create(
        model="deepseek-ai/DeepSeek-V3",  
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional search query optimizer for a bank GraphRAG network. "
                    
                    "Rewrite the user query into a clean, compact, professional retrieval string. "
                    "Only output the optimized query, nothing else."
                    "give optimized query in users language only. Do not translate it to English. "

                )
            },
            {"role": "user", "content": user_query}
        ],
        max_tokens=150,
        temperature=0.1
    )

    optimized_query = completion.choices[0].message.content

    print(f"⚡ Modifier ran in {round(time.time() - start_time, 2)}s")
    return str(optimized_query).strip()

if __name__ == "__main__":
    raw_1 = "ఎన్ఆర్ఐ పత్రాలు అవసరం"
    print(f"Raw Input 1: {raw_1}")
    print(f"Enhanced 1:  {enhance_query_for_graphrag(raw_1)}")

    raw_2 = "fd rates for senior"
    print(f"Raw Input 2: {raw_2}")
    print(f"Enhanced 2:  {enhance_query_for_graphrag(raw_2)}")

