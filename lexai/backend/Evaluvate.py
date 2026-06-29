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
from typing import cast
from dotenv import load_dotenv
from ragas.dataset_schema import EvaluationResult

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from langchain_openai import ChatOpenAI
from langchain_huggingface import HuggingFaceEmbeddings

load_dotenv(Path(__file__).parent / ".env")

API = "http://localhost:8000"

# ============================================================
# YOUR TEST SET — fill this in with real questions about a
# document you've already uploaded, and the answer YOU know
# is correct (the "ground truth").
# ============================================================
TEST_SET = [
    # --- SOP.pdf questions ---
    {
        "question": "What accuracy did the neural network built from scratch achieve on Fashion-MNIST?",
        "ground_truth": "88.97% accuracy.",
    },
    {
        "question": "What two retrieval methods does LexAI combine?",
        "ground_truth": "ChromaDB vector search and BM25 retrieval.",
    },
    {
        "question": "What hackathon did MatRisk AI compete in and what was its national ranking?",
        "ground_truth": "The EXCAVATE Hackathon, ranked Top 9 nationally.",
    },
    {
        "question": "What knowledge gaps does the author acknowledge in their statement of purpose?",
        "ground_truth": "Cannot derive the XGBoost split criterion from scratch; built an LSTM-based drift detector but never formally studied sequential learning or understood why attention replaced recurrence; no dimensionality reduction experience beyond sklearn's PCA; only read about reinforcement learning; no intuition for causal inference.",
    },
    {
        "question": "What ML frameworks and tools does Job Hunter Crew use?",
        "ground_truth": "CrewAI and LLaMA 3.3-70B.",
    },
    # --- final submission.pdf (Sahayak) questions ---
    {
        "question": "What problem does Sahayak solve?",
        "ground_truth": "Sahayak connects rural Indians to government schemes and NGOs they qualify for but cannot access due to literacy, smartphone, or language barriers, using a voice-first phone call that requires no app or smartphone.",
    },
    {
        "question": "How many agents does Sahayak use and what are they called?",
        "ground_truth": "Five agents: Listener (structures the spoken account), Classifier (tags domain and urgency), Knowledge matcher (finds what the user qualifies for), Follow-up coordinator (ensures the case does not go cold), and NGO coordinator (escalates to real local help).",
    },
    {
        "question": "What is the approximate cost of a single Sahayak call and how is it broken down?",
        "ground_truth": "Approximately ₹2-4 per call: Whisper STT via Groq ~₹0.18, intake + 5 agents on Llama 3.3 70B via OpenRouter ~₹0.15, TTS via Orpheus/Sarvam ~₹2-3.",
    },
    {
        "question": "What are the three tiers in Sahayak's NGO triage system?",
        "ground_truth": "Tier 1 (self-serve): clear eligibility, simple application, full instructions given, no NGO involved. Tier 2 (batched queue): needs help but not urgent, goes into a daily/weekly batch. Tier 3 (direct escalation): time-sensitive or complex, a worker is reached in real time.",
    },
    {
        "question": "What is Krishak Bandhu's death benefit amount mentioned in the case study?",
        "ground_truth": "₹2,00,000 (two lakh rupees).",
    },
    {
        "question": "What hackathon was Sahayak submitted to?",
        "ground_truth": "ASTRA Lab Agentic AI Hackathon 2026 at IIT Kharagpur.",
    },
    # --- Trick questions (honest answer: NOT in document) ---
    {
        "question": "What is Ishwarya Mohan's CGPA at IIT Kharagpur?",
        "ground_truth": "NOT IN DOCUMENT — CGPA is not mentioned anywhere in the uploaded documents.",
    },
    {
        "question": "What programming language is Sahayak's backend written in?",
        "ground_truth": "NOT IN DOCUMENT — the case study mentions three providers and tech stack but does not specify the backend programming language.",
    },
    {
        "question": "Who were the judges of the ASTRA Lab Agentic AI Hackathon 2026?",
        "ground_truth": "NOT IN DOCUMENT — the hackathon name is mentioned but no judges or evaluators are named in the uploaded documents.",
    },
]


def ask_lexai(question):
    """Call the real /query endpoint and return (answer, contexts)."""
    resp = requests.post(f"{API}/query", json={"question": question, "owner_id": "5088c937-e178-4d1f-9319-13936e2776b1"})
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
        api_key=os.getenv("NVIDIA_API_KEY") or "",  # type: ignore[arg-type]
        model="meta/llama-3.1-8b-instruct",
        temperature=0,
    )
    ragas_llm = LangchainLLMWrapper(judge)

    # Use the same local embedding model the engine uses — no extra API key needed.
    hf_embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    ragas_embeddings = LangchainEmbeddingsWrapper(hf_embeddings)

    print("\nRunning RAGAS evaluation (this calls the judge LLM multiple times)...\n")
    results = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=ragas_llm,
        embeddings=ragas_embeddings,
    )

    print("\n" + "=" * 50)
    print("RAGAS RESULTS")
    print("=" * 50)
    print(results)

    df = cast(EvaluationResult, results).to_pandas()
    out_path = Path(__file__).parent / "ragas_results.csv"
    df.to_csv(out_path, index=False)
    print(f"\nPer-question detail saved to {out_path}")


if __name__ == "__main__":
    main()

    