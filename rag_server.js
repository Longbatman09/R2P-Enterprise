/**
 * RAG Server — Express.js backend for pdf-parse / Pinecone / NVIDIA NIM
 *
 * Uses:
 *   - express + formidable (file upload)
 *   - pdf-parse (PDF text extraction)
 *   - node-fetch (NVIDIA NIM API calls)
 *   - pinecone-client (Pinecone vector DB)
 *
 * Endpoints:
 *   POST /api/rag/ingest    — upload PDF, extract, chunk, embed, store
 *   POST /api/rag/query     — RAG question answering
 *   GET  /api/rag/textbooks — list ingested textbooks
 *   DELETE /api/rag/textbook/:name — delete a textbook
 */

import express from 'express';
import formidable from 'express-formidable';
import { PDFParse } from 'pdf-parse';
import { Pinecone } from '@pinecone-database/pinecone';
import fetch from 'node-fetch';

const app = express();
app.use(formidable());
app.use(express.static('UI'));

// ─── Config ───────────────────────────────────────────────────────────────────
const PINECONE_API_KEY = process.env.PINECONE_API_KEY || '';
function getPineconeIndex(name) {
  const slug = name.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/-+/g, '-').slice(0, 32);
  const idx = 'textbook-' + slug;
  return { indexName: idx, ensure: async () => {
    const indexes = await pinecone.listIndexes();
    const names = indexes.indexes ? indexes.indexes.map(i => i.name) : [];
    if (!names.includes(idx)) {
      await pinecone.createIndex({ name: idx, dimension: 1024, metric: 'cosine' });
      await new Promise(r => setTimeout(r, 30000)); // wait for ready
    }
    return pinecone.index(idx);
  }};
}
const NVIDIA_API_KEY = process.env.NVIDIA_API_KEY || '';
const NVIDIA_EMBED_URL = 'https://integrate.api.nvidia.com/v1/embeddings';
const NVIDIA_CHAT_URL  = 'https://integrate.api.nvidia.com/v1/chat/completions';
const PINECONE_INDEX = 'textbook-rag';
const EMBED_MODEL = 'nvidia/nemotron-3-embed-1b';
const CHAT_MODEL  = 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning';
const CHUNK_SIZE = 4000;
const CHUNK_OVERLAP = 200;

// ─── Pinecone Setup ───────────────────────────────────────────────────────────
const pinecone = new Pinecone({ apiKey: PINECONE_API_KEY });

async function getIndex() {
  const client = pinecone;
  const indexes = await client.listIndexes();
  const exists = indexes.indexes?.some(i => i.name === PINECONE_INDEX);

  if (!exists) {
    await client.createIndex({
      name: PINECONE_INDEX,
      dimension: 1024,
      metric: 'cosine',
      spec: { serverless: { cloud: 'aws', region: 'us-east-1' } }
    });
    // Wait for index to be ready
    await new Promise(r => setTimeout(r, 3000));
  }

  return client.index(PINECONE_INDEX);
}

// ─── NVIDIA NIM Helpers ───────────────────────────────────────────────────────
async function embedText(texts, inputType = 'passage') {
  const res = await fetch(NVIDIA_EMBED_URL, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${NVIDIA_API_KEY}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      model: EMBED_MODEL,
      input: texts,
      input_type: inputType
    })
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`NVIDIA embed failed: ${res.status} ${err}`);
  }

  const data = await res.json();
  return data.data.map(d => d.embedding);
}

async function chat(messages) {
  const res = await fetch(NVIDIA_CHAT_URL, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${NVIDIA_API_KEY}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      model: CHAT_MODEL,
      messages,
      max_tokens: 512,
      temperature: 0.3
    })
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`NVIDIA chat failed: ${res.status} ${err}`);
  }

  const data = await res.json();
  return data.choices[0].message.content;
}

// ─── Text Chunking ────────────────────────────────────────────────────────────
function chunkText(text, size = CHUNK_SIZE, overlap = CHUNK_OVERLAP) {
  const chunks = [];
  let start = 0;

  while (start < text.length) {
    let end = start + size;
    if (end >= text.length) {
      chunks.push(text.slice(start));
      break;
    }

    // Try to break at paragraph boundary
    const breakPoint = text.lastIndexOf('\n\n', end);
    if (breakPoint > start + overlap) {
      end = breakPoint;
    }

    chunks.push(text.slice(start, end));
    start = end - overlap;
  }

  return chunks.filter(c => c.trim().length > 50);
}

// ─── PDF Extraction ───────────────────────────────────────────────────────────
async function extractPDF(buffer) {
  const parser = new PDFParse({ data: buffer });
  const textResult = await parser.getText();

  if (textResult && textResult.pages) {
    return textResult.pages.map(p => ({
      page: p.pageNumber,
      text: p.text || ''
    })).filter(p => p.text.trim());
  }

  return [{ page: 1, text: textResult?.text || '' }];
}

// ─── API Endpoints ────────────────────────────────────────────────────────────

// Ingest PDF
app.post('/api/rag/ingest', async (req, res) => {
  try {
    const file = req.files?.file;
    const textbookName = req.fields?.textbook_name || 'untitled';

    if (!file) {
      return res.status(400).json({ error: 'No file uploaded' });
    }

    console.log(`[RAG] Ingesting: ${file.originalFilename || file.name}`);

    // Read file buffer
    let buffer;
    if (Buffer.isBuffer(file.data)) {
      buffer = file.data;
    } else if (file.path) {
      const fs = await import('fs');
      buffer = fs.readFileSync(file.path);
    } else {
      return res.status(400).json({ error: 'Invalid file upload' });
    }

    // Extract text from PDF
    const pages = await extractPDF(buffer);
    console.log(`[RAG] Extracted ${pages.length} pages`);

    if (pages.length === 0) {
      return res.status(400).json({ error: 'No text found in PDF (might be scanned)' });
    }

    // Combine all pages and chunk
    const fullText = pages.map(p => p.text).join('\n\n');
    const chunks = chunkText(fullText);
    console.log(`[RAG] Created ${chunks.length} chunks`);

    if (chunks.length === 0) {
      return res.status(400).json({ error: 'PDF text too short to chunk' });
    }

    // Embed chunks in batches
    const BATCH_SIZE = 32;
    const txName = (req.body && req.body.textbook) ? req.body.textbook : (req.params && req.params.name) || "default";
    const index = await getIndex(txName);
    let upserted = 0;

    for (let i = 0; i < chunks.length; i += BATCH_SIZE) {
      const batch = chunks.slice(i, i + BATCH_SIZE);
      const embeddings = await embedText(batch);

      const vectors = batch.map((text, idx) => ({
        id: `${textbookName.replace(/\s+/g, '-').toLowerCase()}-${i + idx}`,
        values: embeddings[idx],
        metadata: {
          text,
          textbook: textbookName,
          chunkIndex: i + idx
        }
      }));

      await index.upsert({ vectors });
      upserted += vectors.length;
      console.log(`[RAG] Upserted ${upserted}/${chunks.length} vectors`);
    }

    // Save manifest
    const fs = await import('fs');
    const manifest = {
      name: textbookName,
      chunks: chunks.length,
      upserted,
      createdAt: new Date().toISOString()
    };

    fs.mkdirSync('rag_data', { recursive: true });
    fs.writeFileSync(
      `rag_data/${textbookName.replace(/\s+/g, '-').toLowerCase()}.json`,
      JSON.stringify(manifest, null, 2)
    );

    res.json({
      status: 'success',
      textbook: textbookName,
      chunks: chunks.length,
      upserted,
      pages: pages.length
    });

  } catch (error) {
    console.error('[RAG] Ingest error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Query RAG
app.post('/api/rag/query', async (req, res) => {
  try {
    let { textbook, question, username } = req.body;

    if (!textbook || !question) {
      return res.status(400).json({ error: 'textbook and question required' });
    }

    if (!username) {
      try {
        const fs = await import('fs');
        if (fs.existsSync('session.json')) {
          const sessionData = JSON.parse(fs.readFileSync('session.json', 'utf-8'));
          username = sessionData.username;
        }
      } catch (e) {}
    }

    let searchQuery = question;
    if (username && /\b(i|me|my|myself|we|us|our|ours)\b/i.test(question)) {
      searchQuery = `${username} ${question}`;
    }

    console.log(`[RAG] Query: "${searchQuery}" in "${textbook}"`);

    // Embed question as query
    const [qEmbedding] = await embedText([searchQuery], 'query');

    // Search Pinecone
    const txName = (req.body && req.body.textbook) ? req.body.textbook : (req.params && req.params.name) || "default";
    const index = await getIndex(txName);
    const results = await index.query({
      vector: qEmbedding,
      topK: 4,
      includeMetadata: true,
      filter: { textbook: { $eq: textbook } }
    });

    const contexts = results.matches
      .sort((a, b) => b.score - a.score)
      .slice(0, 4)
      .map(m => m.metadata.text);

    if (contexts.length === 0) {
      return res.json({
        answer: "I couldn't find relevant information in the textbook. Try uploading it first.",
        sources: []
      });
    }

    // Build prompt
    const contextText = contexts.join('\n\n---\n\n');
    let systemPrompt = `You are an academic assistant. Answer the question using ONLY the context below.
If the answer is not in the context, say "The textbook doesn't cover this."`;
    if (username) {
      systemPrompt += ` The current user asking the question is named '${username}' (referred to as 'I', 'me', 'my', 'myself' in the question).`;
    }

    const prompt = `${systemPrompt}

Context from "${textbook}":
${contextText}

Question: ${question}

Answer concisely:`;

    // Get LLM response
    const answer = await chat([
      { role: 'user', content: prompt }
    ]);

    res.json({
      answer,
      sources: contexts.map((c, i) => ({
        excerpt: c.slice(0, 150) + '...',
        score: results.matches[i]?.score || 0
      }))
    });

  } catch (error) {
    console.error('[RAG] Query error:', error);
    res.status(500).json({ error: error.message });
  }
});

// List textbooks
app.get('/api/rag/textbooks', async (req, res) => {
  try {
    const fs = await import('fs');
    const path = await import('path');

    const ragDir = 'rag_data';
    if (!fs.existsSync(ragDir)) {
      return res.json({ textbooks: [] });
    }

    const files = fs.readdirSync(ragDir).filter(f => f.endsWith('.json'));
    const textbooks = files.map(f => {
      const data = JSON.parse(fs.readFileSync(path.join(ragDir, f), 'utf-8'));
      return data;
    });

    res.json({ textbooks });
  } catch (error) {
    console.error('[RAG] List error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Delete textbook
app.delete('/api/rag/textbook/:name', async (req, res) => {
  try {
    const { name } = req.params;
    const txName = (req.body && req.body.textbook) ? req.body.textbook : (req.params && req.params.name) || "default";
    const index = await getIndex(txName);

    // Delete all vectors with this textbook name
    await index.deleteMany({ filter: { textbook: { $eq: name } } });

    // Delete manifest
    const fs = await import('fs');
    const manifestPath = `rag_data/${name.replace(/\s+/g, '-').toLowerCase()}.json`;
    if (fs.existsSync(manifestPath)) {
      fs.unlinkSync(manifestPath);
    }

    res.json({ status: 'deleted', textbook: name });
  } catch (error) {
    console.error('[RAG] Delete error:', error);
    if (error.code === 'NOT_FOUND' || error.message?.includes('not found')) {
      res.json({ status: 'deleted', textbook: req.params.name });
    } else {
      res.status(500).json({ error: error.message });
    }
  }
});

// Health check
app.get('/api/rag/health', async (req, res) => {
  try {
    const txName = (req.body && req.body.textbook) ? req.body.textbook : (req.params && req.params.name) || "default";
    const index = await getIndex(txName);
    const stats = await index.describeIndexStats();

    res.json({
      status: 'ok',
      pinecone: stats,
      model: CHAT_MODEL
    });
  } catch (error) {
    res.status(500).json({ status: 'error', error: error.message });
  }
});

// ─── Start Server ─────────────────────────────────────────────────────────────
const PORT = process.env.PORT || 8000;
app.listen(PORT, () => {
  console.log(`[RAG] Server running on http://localhost:${PORT}`);
  console.log(`[RAG] UI at http://localhost:${PORT}/rag_chat.html`);
});
