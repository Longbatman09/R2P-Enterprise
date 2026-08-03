import os
import glob
import json
import time
from dotenv import load_dotenv

# Load env before importing rag_system so it gets the keys
load_dotenv()

from mcp_servers.rag_system import _ensure_index, _build_chunks, _nim_embed, _pc_upsert, EMBED_MODEL, RAG_DATA_DIR

def ingest_md(md_path, textbook_name):
    print(f"Reading {md_path}...")
    with open(md_path, 'r', encoding='utf-8') as f:
        full_text = f.read()
    
    idx_name = _ensure_index(textbook_name)
    pages = [{"page": 1, "text": full_text}]
    chunks = _build_chunks(pages, textbook_name)
    
    if not chunks:
        print(f"No chunks generated for {textbook_name}")
        return
        
    print(f"Generated {len(chunks)} chunks. Uploading to Pinecone...")
    
    BATCH = 64
    upserted = 0
    for start in range(0, len(chunks), BATCH):
        batch = chunks[start: start + BATCH]
        vectors = []
        try:
            embeddings = _nim_embed([c["text"] for c in batch])
        except Exception as exc:
            print(f"NIM embed failed at chunk {start}: {exc}")
            return
            
        for chunk, emb in zip(batch, embeddings):
            vectors.append({
                "id": chunk["id"],
                "values": emb,
                "metadata": {
                    "textbook": chunk["textbook"],
                    "page": chunk["page"],
                    "text": chunk["text"][:4000],
                },
            })
        try:
            _pc_upsert(idx_name, vectors)
            upserted += len(vectors)
            print(f"Upserted {upserted}/{len(chunks)}...")
        except Exception as exc:
            print(f"Pinecone upsert failed: {exc}")
            return
            
    manifest = {
        "textbook_name": textbook_name,
        "pinecone_index": idx_name,
        "n_pages": 1,
        "total_chars": len(full_text),
        "n_chunks": len(chunks),
        "upserted": upserted,
        "embed_model": EMBED_MODEL,
        "file_name": os.path.basename(md_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(RAG_DATA_DIR / f"{idx_name}.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Finished ingesting {textbook_name}\n")

if __name__ == "__main__":
    md_files = glob.glob('Local_Mem/**/*.md', recursive=True)
    if not md_files:
        print("No .md files found in Local_Mem/")
    for md_file in md_files:
        name = os.path.basename(md_file).replace('.md', '')
        ingest_md(md_file, name)
    print("All files processed.")
