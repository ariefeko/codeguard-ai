# Snapshot — CodeGuard AI Phase 14: RAG Pipeline Planning

Date: 2026-06-27
Updated: 2026-07-02 (rev4)

---

## Current Status

```text
✅ Phase 1-11  — Foundation sampai Redis Queue
✅ Phase 12    — Sentry Integration (HMAC, parser, BugAnalysis schema)
✅ Phase 13    — pytest (50 tests passing)
⬜ Phase 14    — RAG Pipeline ← NEXT
```

---

## Phase 14 Goal

Phase 14 bertujuan menambahkan **RAG layer** ke CodeGuard supaya analisis GitHub PR dan Sentry bug tidak hanya bergantung pada LLM umum + Tavily realtime search.

RAG dipakai untuk memberi CodeGuard **curated security knowledge base** yang stabil, hemat bandwidth, dan bisa di-reuse lintas analysis.

```text
LLM = reasoning engine
Tavily = realtime external update
RAG = curated reusable security knowledge
Qdrant = vector search layer
```

---

## RAG Architecture Decision

### Tujuan RAG

RAG **bukan untuk index seluruh codebase**.

RAG dipakai untuk menyimpan dan mengambil knowledge seperti:

**Security:**
- OWASP Top 10
- CWE
- CVE summary yang relevan
- secure coding checklist
- vulnerability patterns per language

**Best Practices:**
- Laravel conventions + patterns
- FastAPI best practices
- Express.js patterns
- Spring patterns
- Go idiomatic patterns

**Code Quality:**
- Design patterns per language
- Anti-patterns yang harus dihindari
- Performance considerations per language
- Refactoring guidance

**Framework-specific:**
- Laravel Eloquent best practice
- FastAPI dependency injection patterns
- Express middleware patterns
- Spring Boot configuration best practice

Alasan tidak index seluruh codebase dulu:

```text
1. Codebase bisa besar dan mahal untuk di-embed.
2. Changed files dari PR sudah cukup jadi primary context.
3. RAG lebih valuable sebagai curated knowledge base (security, best practices, code quality).
4. CodeGuard belum butuh semantic search seluruh repository.
```

---

## Qdrant Collections

```text
security_php      → OWASP PHP, Laravel security, PHP CVE
security_python   → OWASP Python, FastAPI security, pip CVE
security_js       → OWASP Node.js, npm CVE, Express security
security_java     → OWASP Java, Spring CVE
security_go       → OWASP Go
security_general  → OWASP Top 10, CWE, general CVE
```

Phase 14 juga mencakup knowledge non-security:

```text
bestpractice_php      → Laravel conventions, PHP patterns, Eloquent best practice
bestpractice_python   → FastAPI patterns, Python idioms, dependency injection
bestpractice_js       → Express middleware, Node.js patterns, async best practice
bestpractice_java     → Spring Boot patterns, Java idioms
bestpractice_go       → Go idiomatic patterns, goroutine best practice
quality_general       → Design patterns, anti-patterns, refactoring guidance,
                        performance considerations per language
```

Alternative later:

```text
security_framework_laravel
security_framework_fastapi
security_framework_express
security_cwe
security_owasp
```

Untuk MVP, collection per language (security + best practice) sudah cukup.

### Collection Decision Rev3

Collection yang banyak **tidak dipangkas dulu** untuk MVP.

Alasan:

```text
1. CodeGuard tidak hanya menganalisis security.
2. CodeGuard juga butuh best practice, code quality, framework patterns, dan performance guidance.
3. Banyak collection bisa menjadi bahan MVP untuk menunjukkan bahwa analysis tidak terbatas ke security saja.
4. Selama topic_mapper rapi, multi-collection masih aman untuk MVP.
```

Rule:

```text
Security issue      → query security_* collection
Best practice issue → query bestpractice_* collection
Code quality issue  → query quality_general / bestpractice_* collection
Unknown issue       → fallback ke security_general / quality_general
```

---

## Deployment Strategy: Hybrid

```text
Lokal  → Qdrant Docker untuk development + indexing
Cloud  → Qdrant Cloud free tier untuk production query
Sync   → index lokal, upload/sync ke Qdrant Cloud
```

Alasan:

- Indexing mahal karena butuh embedding.
- Indexing dilakukan lokal supaya hemat biaya.
- Query di production ringan.
- Railway cukup melakukan query ke Qdrant Cloud.
- Knowledge base bisa dikurasi dan disync bertahap.

### Runtime vs Refresh Split

Keputusan MVP:

```text
Runtime:
CodeGuard → topic_mapper → Qdrant query/filter → inject prompt

Refresh:
Local indexer / scheduled job → Tavily/docs check → hash compare → embed → sync Qdrant Cloud
```

Artinya:

```text
Production runtime = query only
Local indexer      = update knowledge + embed + sync
```

Railway production **tidak melakukan re-embed dan tidak update Qdrant saat request analysis sedang berjalan**.

---

## Important Production Gap: Query Embedding

Jika production Railway melakukan query ke Qdrant, production tetap butuh cara membuat **query embedding**.

Masalah:

```text
nomic-embed-text via Ollama hanya tersedia lokal.
Railway tidak otomatis punya Ollama embedding service.
```

Solusi yang tersedia:

```text
Opsi A — Cloud embedding API
  Production memakai embedding API eksternal.
  Cocok untuk production, tapi ada biaya.

Opsi B — Self-host embedding service
  Deploy service embedding sendiri.
  Lebih kontrol, tapi lebih berat.

Opsi C — Predefined topic retrieval
  Production tidak embed query bebas.
  Production memilih topic yang sudah ditentukan.
  Cocok untuk MVP.
```

Keputusan MVP:

```text
Gunakan Opsi C: predefined topic retrieval.
```

Contoh:

```text
Changed files: PHP/Laravel
Detected issue: SQL injection risk
Topics:
  - owasp_sql_injection
  - laravel_query_builder_security
  - php_pdo_prepared_statement
```

Dengan cara ini, CodeGuard bisa query knowledge berdasarkan `language`, `framework`, dan `topic`, tanpa harus production embedding query bebas dari user.

Runtime query MVP menggunakan **metadata filter / known topic lookup**, bukan free-form query embedding.

---

## RAG Flow saat PR Review

```text
PR review triggered
  → detect language/framework dari changed files
  → infer topics dari changed files + issue candidates
  → topic_mapper selects target collection + topic
  → query Qdrant Cloud by metadata/filter
  → inject matching RAG result ke prompt
  → LLM analysis
  → GitHub PR comment
```

Runtime rule:

```text
Production hanya query knowledge yang sudah tersedia.
Jika topic stale/expired, production boleh fallback ke Tavily untuk analysis saat itu,
tapi tidak melakukan re-embed dan tidak update Qdrant.
```

---

## RAG Flow saat Sentry Bug Analysis

```text
Sentry webhook triggered
  → parse error type, file, line, stacktrace
  → detect language/framework dari file path/repo metadata
  → infer topics dari error type
  → query Qdrant security knowledge
  → inject relevant RAG context ke bug analysis prompt
  → LLM bug analysis
  → GitHub issue/comment created
```

Contoh mapping:

```text
ModelNotFoundException Laravel
  → topics:
      - laravel_exception_handling
      - missing_null_handling
      - authorization_check
      - safe_error_reporting
```

---

## Topic Mapper Validation

`topic_mapper` harus sederhana, rapi, dan punya validasi parent.

Tujuan:

```text
issue/error/context → parent category → collection → topic
```

Contoh parent category:

```text
security
best_practice
code_quality
performance
framework
```

Validation rule:

```text
1. Tentukan parent category dari issue/error.
2. Cari topic yang cocok di parent tersebut.
3. Jika topic tidak sesuai dengan parent, cari parent lain yang lebih cocok.
4. Jika tetap tidak cocok, fallback ke general collection.
```

Contoh:

```text
Issue: Missing null handling
Initial parent: security
Validation: kurang cocok sebagai security utama
Fallback parent: code_quality / framework
Final topic:
  - missing_null_handling
  - laravel_exception_handling
```

Fallback order awal:

```text
security_* → bestpractice_* → quality_general → security_general
```

Rule penting:

```text
Topic yang salah parent lebih baik dipindahkan daripada dipaksa.
```

---

## Bandwidth Optimization: TTL + Content Hash

### Tujuan

Mengurangi Tavily call berulang untuk topic yang sama.

```text
Tanpa RAG:
  setiap PR → Tavily call → internet

Dengan RAG + TTL:
  topic fresh → pakai Qdrant
  topic expired → ditandai stale
  local refresh job → Tavily/docs check
  content sama → update timestamp saja
  content berubah → re-embed lokal lalu sync ke Qdrant Cloud
```

### Estimasi Penghematan

```text
Tanpa RAG:
  Setiap PR → 3 Tavily calls

Dengan TTL 7 hari:
  Hari 1     → 3 Tavily calls
  Hari 2-7   → 0 Tavily calls
  Hari 8     → Tavily check ulang

Estimasi hemat bandwidth Tavily: ±85%
```

---

## TTL Strategy per Topic

TTL tidak dibuat global untuk semua topic.

```text
OWASP general knowledge      → 30 hari
CWE general knowledge        → 30 hari
Laravel/PHP best practice    → 14 hari
FastAPI/Python best practice → 14 hari
Node/npm security            → 7 hari
CVE/package vulnerability    → 1-3 hari
General secure coding        → 30-90 hari
```

Reasoning:

```text
CVE berubah cepat.
OWASP/CWE relatif stabil.
Framework best practice berubah sedang.
```

---

## Knowledge Source Curation

Knowledge source tidak boleh langsung dimasukkan mentah ke Qdrant.

Validation rules:

```text
1. Ambil dari sumber terpercaya.
2. Ringkas dulu sebelum disimpan.
3. Buang konten noise.
4. Simpan source_url.
5. Simpan source_title.
```

Trusted source examples:

```text
OWASP
CWE / MITRE
NVD / CVE official source
Official framework docs
Official package advisories
Vendor security advisory
Internal verified note
```

Curation flow:

```text
Tavily/docs result
  → validate source trust
  → extract relevant section
  → summarize guidance
  → remove noise/navigation/ads/boilerplate
  → save clean content + source metadata
  → embed locally
  → sync to Qdrant Cloud
```

Rule penting:

```text
Bad knowledge in RAG = bad analysis output.
```

---

## Metadata per Document di Qdrant

```python
{
    "content"          : "OWASP SQL injection prevention...",
    "content_hash"     : "sha256:abc123...",
    "topic"            : "owasp_sql_injection",
    "category"         : "security|best_practice|code_quality|performance|framework",
    "language"         : "php",
    "framework"        : "laravel",
    "framework_version": "10|11|12|unknown",
    "source_title"     : "OWASP SQL Injection Prevention Cheat Sheet",
    "source_url"       : "https://owasp.org/...",
    "source_type"      : "owasp|cwe|cve|framework_doc|best_practice|internal_note",
    "severity"         : "low|medium|high|critical|unknown",
    "tags"             : ["security", "database", "input-validation"],
    "confidence"       : 0.9,
    "license"          : "unknown|permissive|public_reference",
    "retrieved_at"     : "2026-06-27",
    "last_updated"     : "2026-06-27",
    "ttl_days"         : 30
}
```

Minimum metadata for MVP:

```text
topic
category
language
source_type
source_title
source_url
content_hash
last_updated
ttl_days
tags
```

---

## Runtime Query Logic

Runtime production hanya melakukan query/filter.

```python
collections = topic_mapper.select_collections(language, framework, topics)

results = qdrant.query_by_filter(
    collections=collections,
    filters={
        "language": language,
        "framework": framework,
        "topics": topics,
        "min_confidence": 0.65,
    },
    limit=5,
)

if not results:
    return tavily.search(topics)  # fallback for current analysis only

return results
```

Runtime tidak melakukan:

```text
- re-embed
- update content
- update Qdrant document
- sync local to cloud
```

---

## Local Refresh / Indexing Logic

Refresh knowledge dilakukan oleh local indexer atau scheduled indexing job.

```python
is_fresh = (today - last_updated).days < ttl_days

if is_fresh:
    skip_refresh(topic)
    return

new_content = tavily_or_docs_fetch(topic)
clean_content = curate_and_summarize(new_content)
new_hash = sha256(clean_content)

if new_hash == stored_hash:
    qdrant.update_metadata(topic, last_updated=today)
    return

embedding = embed_locally(clean_content)  # Ollama/nomic local

qdrant.upsert(
    topic=topic,
    content=clean_content,
    embedding=embedding,
    content_hash=new_hash,
    last_updated=today,
)

sync_to_qdrant_cloud(topic)
```

---

## Failure Handling

RAG tidak boleh membuat CodeGuard gagal total.

```text
RAG success
  → inject context ke prompt

RAG failed / Qdrant unavailable
  → fallback ke Tavily

Tavily failed
  → fallback ke normal prompt tanpa external enrichment

LLM tetap jalan selama core analysis context tersedia.
```

Rule:

```text
RAG is enrichment, not hard dependency.
RAG failed → Tavily.
Tavily failed → normal prompt.
LLM analysis must continue as long as core PR/Sentry context exists.
```

---

## Tech Stack RAG

```text
LangChain         → RAG helper/orchestration ringan
Qdrant            → vector database
  - Lokal         → Docker untuk development + indexing
  - Production    → Qdrant Cloud untuk query
nomic-embed-text  → embedding model via Ollama lokal
Tavily            → realtime update check
```

LangChain usage dibatasi untuk:

```text
- document splitting
- embedding adapter
- Qdrant retriever integration
```

LangChain tidak mengambil alih orchestrator CodeGuard.

---

## File Structure

```text
src/rag/
  __init__.py
  rag_pipeline.py      → main RAG class / runtime query interface
  embedder.py          → wrapper nomic-embed-text via Ollama for local indexing
  knowledge_base.py    → topic definitions per language/framework
  updater.py           → TTL + hash check + Tavily/docs update logic for local refresh
  topic_mapper.py      → map issue/error/language ke topic + parent validation
  qdrant_client.py     → Qdrant connection wrapper
  indexer.py           → bulk indexing local knowledge base
  sync.py              → sync local indexed knowledge to Qdrant Cloud
```

Optional later:

```text
src/rag/evaluator.py    → evaluate retrieval quality
```

### RAG Script Location Rule

Semua script yang berhubungan dengan RAG dan Qdrant diletakkan di dalam folder `src/rag/`.

Tujuannya supaya Phase 14 tetap terisolasi dan tidak menyebar ke root project.

```text
src/rag/
  indexer.py        → bulk indexing curated knowledge base lokal
  sync.py           → sync local Qdrant/indexed data ke Qdrant Cloud
  updater.py        → TTL + hash compare + Tavily/docs refresh
  qdrant_client.py  → Qdrant local/cloud connection wrapper
```

Command lokal tetap boleh dipanggil dari root project, tapi entry point-nya tetap module di `src/rag/`.

Contoh:

```bash
python -m src.rag.indexer
python -m src.rag.updater
python -m src.rag.sync
```

Rule:

```text
RAG scripts stay inside src/rag.
No scattered standalone scripts in project root.
```

---

## Environment Variables

```env
QDRANT_URL=https://xxxxx.qdrant.cloud
QDRANT_API_KEY=xxxxx
QDRANT_COLLECTION_PREFIX=security
OLLAMA_URL=http://localhost:11434
RAG_ENABLED=true
RAG_TTL_DEFAULT_DAYS=30
RAG_MAX_RESULTS=5
RAG_MIN_CONFIDENCE=0.65
```

Local development:

```env
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
OLLAMA_URL=http://localhost:11434
```

Production MVP:

```env
QDRANT_URL=<qdrant-cloud-url>
QDRANT_API_KEY=<qdrant-cloud-api-key>
RAG_ENABLED=true
```

---

## Integration Point di Orchestrator

RAG dipanggil di `_enrich_with_search()` sebagai enrichment layer.

```python
def _enrich_with_search(self, context: dict) -> dict:
    results = {}

    language = context.get("language")
    framework = context.get("framework")
    topics = self.topic_mapper.from_context(context)

    try:
        rag_results = self.rag.query(
            language=language,
            framework=framework,
            topics=topics,
            max_results=5,
            mode="metadata_filter",
        )
        results["rag"] = rag_results
    except Exception as exc:
        results["rag_error"] = str(exc)
        results["rag"] = []

    # Tavily tetap dipakai jika RAG kosong/expired/perlu realtime check
    if not results["rag"]:
        results["tavily"] = self.tavily.search(topics)

    return results
```

---

## Prompt Injection Format

RAG result jangan dimasukkan mentah terlalu panjang.

Format ringkas:

```text
Relevant Security Knowledge:

1. Topic: OWASP SQL Injection
   Source: OWASP
   Relevance: High
   Guidance: Use parameterized queries/prepared statements. Avoid string concatenation for SQL.

2. Topic: Laravel Authorization
   Source: Laravel Security Best Practice
   Relevance: Medium
   Guidance: Ensure policy/gate check before accessing user-owned resources.
```

Rule:

```text
Max 3-6 knowledge snippets per analysis.
Each snippet must be short and directly useful.
Prioritize high confidence + matching language/framework.
```

---

## Observability for RAG

RAG activity harus masuk logging/observability.

Events:

```text
rag_query_started
rag_query_succeeded
rag_query_failed
rag_cache_fresh
rag_ttl_expired
tavily_refresh_started
tavily_refresh_skipped
rag_document_updated
rag_fallback_to_tavily
```

Minimum log fields:

```text
analysis_id
project_id
source: github_pr|sentry
language
framework
topics
rag_result_count
rag_latency_ms
fallback_used
error_message
```

---

## MVP Scope

### Included

```text
✅ Qdrant local Docker
✅ Qdrant Cloud config
✅ local embedding with nomic-embed-text
✅ curated topic definitions
✅ query RAG by language/framework/topic
✅ runtime query/filter only in production
✅ local indexing + local knowledge update
✅ all RAG/Qdrant scripts located inside src/rag
✅ TTL + content hash in local refresh
✅ fallback to Tavily / normal prompt
✅ integration to orchestrator
✅ basic tests
```

### Not Included Yet

```text
❌ full repository codebase indexing
❌ multi-tenant RAG isolation
❌ dashboard UI for RAG
❌ advanced hybrid search
❌ automatic dependency vulnerability database
❌ embedding service in production
❌ production runtime re-embed/update Qdrant
```

---

## Test Plan

### Unit Tests

```text
- topic_mapper detects language/topic correctly
- topic_mapper validates parent category correctly
- topic_mapper fallback searches another parent when topic mismatch
- runtime query uses metadata/filter
- TTL fresh skips local refresh
- TTL expired calls Tavily/docs in local refresh
- same hash updates metadata only
- different hash triggers local re-embed + sync
- Qdrant failure does not fail analysis
```

### Integration Tests

```text
- PR review injects RAG context
- Sentry bug analysis injects RAG context
- fallback to Tavily works
- no RAG result still lets LLM run
```

### Manual Test Repo

```text
Repo   : github.com/ariefeko/tagihin
Branch : develop
Use    : Laravel/TALL test cases
```

---

## Next Steps

```text
1. Setup Qdrant Docker lokal
   docker run -p 6333:6333 qdrant/qdrant

2. Confirm Ollama + nomic-embed-text lokal
   ollama pull nomic-embed-text

3. Daftar Qdrant Cloud
   Ambil QDRANT_URL + QDRANT_API_KEY

4. Buat src/rag/embedder.py
   Wrapper nomic-embed-text via Ollama

5. Buat src/rag/knowledge_base.py
   Definisi topic per language/framework

6. Buat src/rag/topic_mapper.py
   Map changed files/error type ke topic

7. Buat src/rag/qdrant_client.py
   Wrapper koneksi Qdrant

8. Buat src/rag/rag_pipeline.py
   Runtime query/filter Qdrant + return context snippets

9. Buat src/rag/updater.py
   TTL check + hash compare + Tavily/docs update untuk local refresh

10. Buat src/rag/indexer.py
    Bulk indexing curated knowledge base lokal

11. Buat src/rag/sync.py
    Sync indexed knowledge lokal ke Qdrant Cloud

11a. Pastikan semua script RAG/Qdrant tetap di src/rag
    Jalankan via python -m src.rag.indexer / updater / sync

12. Update orchestrator.py
    Panggil RAG di _enrich_with_search()

13. Initial indexing
    Populate Qdrant dengan curated knowledge base awal

14. Sync ke Qdrant Cloud
    Upload local indexed knowledge ke production query store

15. Set env variables di Railway
    QDRANT_URL, QDRANT_API_KEY, RAG_ENABLED

16. Deploy + test end-to-end
```

---

## Final Decision

```text
Phase 14 tetap lanjut sebagai RAG MVP.

RAG tidak digunakan untuk index seluruh codebase.
RAG digunakan untuk curated security knowledge base.
Production MVP memakai predefined topic retrieval.
Production runtime hanya melakukan Qdrant query/filter.
Qdrant Cloud dipakai untuk query production.
Ollama/nomic dipakai untuk indexing dan update knowledge lokal.
Tavily/docs dipakai oleh local refresh/indexing job saat TTL expired.
Multi-collection tetap dipakai untuk MVP karena analysis tidak hanya security.
Semua script RAG/Qdrant disimpan di src/rag.
Prompt budget dibatasi max 3-6 snippets, ringkas, dan prioritas confidence tinggi.
Fallback wajib: RAG failed → Tavily, Tavily failed → normal prompt.
```

---

## Final Principle

```text
Changed files explain what changed.
RAG explains what matters: security, best practices, code quality, framework patterns.
Tavily checks what changed recently.
LLM connects the evidence into useful review.
```
