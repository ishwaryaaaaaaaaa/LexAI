"""
evaluate.py
===========
Runs a RAGAS evaluation against the live LexAI backend.

Usage:
  1. Make sure the backend is running (uvicorn api:app ...) with at least
     one document already uploaded.
  2. Fill in TEST_SET below with real questions about that document, and
     the correct answer for each (written by you, the human).
  3. Run:  python evaluate.py
  4. Read the printed scores. These are your real numbers for the README/CV.

This does NOT use OpenAI. It points RAGAS's judge LLM at NVIDIA NIM, the
same provider your engine already uses, via an OpenAI-compatible wrapper.
"""

import os
import requests
from pathlib import Path
from dotenv import load_dotenv

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from ragas.llms import LangchainLLMWrapper
from langchain_openai import ChatOpenAI

load_dotenv(Path(__file__).parent / ".env")

API = "http://localhost:8000"

# ============================================================
# YOUR TEST SET — fill this in with real questions about a
# document you've already uploaded, and the answer YOU know
# is correct (the "ground truth").
# ============================================================
TEST_SET = [
    {
        "question": "What is the confidence threshold used by the system?",
        "ground_truth": "The confidence threshold is 0.30.",
    },
    {
        "question": "What embedding model does LexAI use?",
        "ground_truth": "LexAI uses BAAI/bge-small-en-v1.5 for embeddings.",
    },
    # Add 8-13 more real questions about your actual uploaded document(s).
    # Aim for 10-15 total for a meaningful average.
]


def ask_lexai(question):
    """Call the real /query endpoint and return (answer, contexts)."""
    resp = requests.post(f"{API}/query", json={"question": question})
    resp.raise_for_status()
    data = resp.json()
    answer = data.get("answer", "")
    contexts = [s["text"] for s in data.get("sources", [])]
    if not contexts:
        # Refused or no sources -> RAGAS still needs a non-empty list
        contexts = [""]
    return answer, contexts


def build_dataset():
    questions, answers, contexts_list, ground_truths = [], [], [], []
    for case in TEST_SET:
        print(f"Asking: {case['question']}")
        answer, contexts = ask_lexai(case["question"])
        questions.append(case["question"])
        answers.append(answer)
        contexts_list.append(contexts)
        ground_truths.append(case["ground_truth"])

    return Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "contexts": contexts_list,
        "ground_truth": ground_truths,
    })


def main():
    print("Building evaluation dataset from live backend responses...\n")
    dataset = build_dataset()

    # Point RAGAS's judge LLM at NVIDIA NIM instead of OpenAI.
    judge = ChatOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=os.getenv("NVIDIA_API_KEY"),
        model="meta/llama-3.1-8b-instruct",
        temperature=0,
    )
    ragas_llm = LangchainLLMWrapper(judge)

    print("\nRunning RAGAS evaluation (this calls the judge LLM multiple times)...\n")
    results = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=ragas_llm,
    )

    print("\n" + "=" * 50)
    print("RAGAS RESULTS")
    print("=" * 50)
    print(results)

    df = results.to_pandas()
    out_path = Path(__file__).parent / "ragas_results.csv"
    df.to_csv(out_path, index=False)
    print(f"\nPer-question detail saved to {out_path}")


if __name__ == "__main__":
    main()

    