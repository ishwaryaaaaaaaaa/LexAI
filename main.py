import os
import chromadb
from dotenv import load_dotenv

# Core LlamaIndex imports
from llama_index.core import (
    SimpleDirectoryReader, 
    VectorStoreIndex, 
    Settings, 
    StorageContext
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.query_engine import RetrieverQueryEngine

# Integration imports
from llama_index.llms.nvidia import NVIDIA
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

# Correct Retriever imports for Hybrid Search
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever

# 1. Setup Security & API Keys
load_dotenv()
nvidia_key = os.getenv("NVIDIA_API_KEY")

# 2. Setup the Brain, Translator, and Scissors
print("Initializing NVIDIA Cloud LLM and Local Embeddings...")
Settings.llm = NVIDIA(model="meta/llama-3.1-8b-instruct", api_key=nvidia_key)
Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-large-en-v1.5")
Settings.node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=50)

# 3. Setup ChromaDB (Persistent Vector Database)
print("Connecting to ChromaDB...")
chroma_client = chromadb.PersistentClient(path="./chroma_db")
chroma_collection = chroma_client.create_collection("legal_docs", get_or_create=True)
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)

# 4. Ingest Documents and Build the Index
print("Reading the 53-page PDF and doing the math... (This will take a moment)")
documents = SimpleDirectoryReader("data").load_data()
index = VectorStoreIndex.from_documents(
    documents, 
    storage_context=storage_context
)

# 5. Create the Hybrid Search Engine
print("Configuring Hybrid Search (Vector + Keyword)...")

# A. Vector Retriever (for semantic meaning)
vector_retriever = index.as_retriever(similarity_top_k=5)

# B. BM25 Keyword Retriever (for exact terms like "Section 14")
bm25_retriever = BM25Retriever.from_defaults(
    docstore=index.docstore, 
    similarity_top_k=5
)

# C. Fusion Retriever (Blends the results together)
hybrid_retriever = QueryFusionRetriever(
    retrievers=[vector_retriever, bm25_retriever],
    similarity_top_k=5,
    num_queries=1,  # Uses your exact question without altering it
    mode="reciprocal_rerank" # The mathematical algorithm that merges both searches
)

# 6. Ask the Legal Question
query_engine = RetrieverQueryEngine.from_args(hybrid_retriever)

question = "What is the exact definition of 'Free consent' under the Indian Contract Act?"
print(f"\nQuestion: {question}")
print("Thinking...\n")

response = query_engine.query(question)
print(f"Answer: {response}")
