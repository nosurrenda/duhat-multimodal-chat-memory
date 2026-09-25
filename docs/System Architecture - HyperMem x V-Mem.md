# System Architecture — Long-Term Multimodal Memory for Chat Applications

> **Design direction:** HyperMem-style hierarchical, denormalized semantic memory combined with V-Mem-style modality-routed retrieval.
>
> The system keeps `Message` and `Media` as canonical evidence, builds `Episode` and `Topic` as retrieval-oriented semantic representations, and routes queries according to both **semantic context** and **source/target modality**.

---

## 1. Phạm vi kiến trúc

Hệ thống được xây quanh ba lớp dữ liệu và xử lý chính:

1. **Canonical conversation** — `Message` và `Media` gốc, là source of truth.
2. **Hierarchical semantic memory** — `Episode` và `Topic`, được sinh từ conversation và có chủ đích duplicate các thông tin quan trọng ở nhiều mức abstraction khác nhau.
3. **Modality-routed retrieval** — query không chạy qua một pipeline cố định, mà được route vào text, visual, OCR hoặc mixed retrieval lane tùy loại evidence cần tìm.

Cấu trúc dữ liệu tổng quát:

```text
Topic
  └── Episode
        └── Message
              └── Media
```

Quan hệ được traversal theo cả hai chiều:

```text
Top-down
Topic → Episode → Message → Media

Bottom-up
Media / Message → Episode → Topic
```

Tuy nhiên kiến trúc này **không chia thông tin thành bốn tập dữ liệu độc lập**. `Topic` và `Episode` là các representation khác nhau của cùng lịch sử conversation. Một thông tin quan trọng có thể xuất hiện ở cả Topic, Episode và raw Message với độ chi tiết khác nhau.

Đây là nguyên tắc cốt lõi lấy từ HyperMem: coarse-level memory phải đủ giàu để query có thể route từ trên xuống, thay vì Topic chỉ là một category label.

Đồng thời, hệ thống không ép mọi visual detail phải được chuyển thành text memory. `Media` giữ một perceptual representation riêng, lấy cảm hứng từ V-Mem: evidence được search ở representation phù hợp với modality của clue, rồi được nối lại qua structure chung của conversation.

---

## 2. Vai trò semantic của từng level

### 2.1 Topic — long-range storyline

Topic đại diện cho một **ongoing situation / process / incident / decision / project / social thread** kéo dài qua nhiều Episode.

Topic **không** phải category rộng kiểu:

```text
Travel
Work
Server
Food
```

Một Topic phù hợp hơn:

```text
Planning the summer trip
Debugging the production payment API incident
A's move from Hanoi to Ho Chi Minh City
B's job-search process
```

Topic phải trả lời được:

> “Storyline dài hạn này là gì, đã phát triển như thế nào, các milestone lớn và outcome chính là gì?”

### 2.2 Episode — local semantic event

Episode đại diện cho một stage/event/discussion tương đối coherent bên trong Topic.

Ví dụ Topic:

```text
Debugging production payment API incident
```

có thể chứa:

```text
E1: Phát hiện payment API bắt đầu trả 502
E2: Điều tra nginx timeout
E3: Điều tra DB connection pool
E4: Thử tăng pool size nhưng không giải quyết
E5: Đổi configuration/library và fix được issue
E6: Theo dõi production sau fix
```

Episode phải trả lời được:

> “Ở stage cụ thể này đã xảy ra chuyện gì?”

### 2.3 Message — exact conversational evidence

Message giữ chính xác điều user nói, speaker, reply relationship và timestamp.

Nó là evidence text cuối cùng khi cần:

- exact wording;
- error string;
- hostname;
- code;
- ID;
- numerical value;
- reference resolution;
- speaker attribution.

### 2.4 Media — exact perceptual evidence

Media giữ ảnh/video/audio/file gốc và các representation theo modality.

Nó là evidence cuối cùng cho các câu hỏi như:

- vật gì xuất hiện trong ảnh;
- người nào xuất hiện;
- chữ gì nằm trong screenshot;
- vật A nằm bên trái/phía sau vật B;
- frame/scene nào trong video chứa action cụ thể.

---

## 3. Intentional semantic duplication

Topic, Episode và Message **có duplicate thông tin có chủ đích**.

Ví dụ conversation về server incident:

```text
Topic
"Debugging production payment API 502 incident"

Summary:
Payment API bắt đầu trả 502 sau deployment. Team điều tra nginx,
sau đó xác định DB connection-pool exhaustion. Việc tăng pool size
không giải quyết hoàn toàn. Cuối cùng issue được fix bằng thay đổi
connection-pool configuration và restart workers.
```

Một Episode bên trong:

```text
Episode
"Investigating DB connection-pool exhaustion"

Summary:
Team xác định payment workers thường xuyên sử dụng hết DB pool.
Họ thử tăng pool size nhưng 502 vẫn xuất hiện.

Detailed content:
A kiểm tra metric ...
B phát hiện idle connection ...
Pool size được đổi từ ... sang ...
...
```

Raw Message:

```text
A: check pool metric đi, đang chạm 100 rồi
B: max đang để 20, tăng 40 thử nhé
...
```

Cùng concept `connection pool` tồn tại ở nhiều tầng, nhưng:

```text
Topic   = milestone trong toàn incident
Episode = chi tiết stage điều tra
Message = exact evidence
```

### 3.1 Vì sao cần duplication

Nếu Topic chỉ chứa:

```text
"Team xử lý một lỗi payment API"
```

thì query:

```text
"lần trước connection pool bị lỗi như nào?"
```

có thể không bao giờ route được vào Topic đúng.

Do đó Topic representation phải giữ:

- participants chính;
- named entities;
- key sub-problems;
- major attempts;
- decisions;
- outcomes;
- important systems/objects;
- long-range temporal development;
- retrieval keywords/aliases.

Episode giữ lại những chi tiết local hơn.

### 3.2 Duplication không biến Topic thành raw transcript

Topic không cần chứa từng message hay mọi minor detail. Nó giữ **retrieval-relevant milestones**.

Một detail rất nhỏ, chẳng hạn:

```text
proxy_read_timeout tăng từ 30s lên 120s
```

có thể chỉ nằm ở Episode hoặc Message.

Điểm quan trọng là architecture phải cho phép lower-level hit recover parent memory khi coarse representation bỏ mất detail.

---

## 4. Canonical data

Message và Media phải được persist trước bất kỳ AI processing nào.

```text
Incoming Message
       ↓
Persist Message / Media
       ↓
Index raw Message / Media
       ↓
Acknowledge request
       ↓
Async Memory Pipeline
```

Điều này bảo đảm ứng dụng chat vẫn hoạt động nếu embedding, LLM hoặc memory worker bị lỗi.

### 4.1 Message

```ts
Message {
  id
  conversation_id
  sender_id
  text
  timestamp
  reply_to_message_id
  created_at
}
```

### 4.2 Media

```ts
Media {
  id
  message_id
  type                // image | video | audio | file
  object_uri
  created_at
}
```

Media **không cần lưu `topic_id` trực tiếp**.

Context được kế thừa qua structure:

```text
Media → Message → Episode → Topic
```

---

## 5. Episode memory

Episode là semantic unit local của conversation.

### 5.1 Episode representation

Không nên chỉ lưu một summary ngắn. Lấy cảm hứng từ HyperMem, Episode có một representation retrieval-rich gồm nhiều mức:

```ts
Episode {
  id
  conversation_id

  title
  summary
  detailed_content

  keywords[]
  entities[]
  participants[]

  start_time
  end_time
  state

  embedding
  version
}
```

Trong đó:

- `title`: specific và retrieval-friendly;
- `summary`: 2–4 câu mô tả WHO / WHAT / CONTEXT / OUTCOME chính;
- `detailed_content`: narrative chi tiết hơn, giữ chronology, named entities, decisions, attempts, outcomes và các detail quan trọng;
- `keywords/entities`: tăng lexical recall và giữ aliases/proper nouns.

### 5.2 Episode searchable representation

Có thể build document để lexical/dense index theo nguyên tắc tương tự HyperMem:

```text
TITLE       × high weight
SUMMARY     × medium weight
DETAIL      × base weight
KEYWORDS    × base/medium weight
ENTITIES    × base/medium weight
```

Ví dụ:

```text
[Title]
Resolving payment API 502 through DB pool reconfiguration

[Summary]
Team tiếp tục điều tra lỗi 502 sau khi tăng pool size không hiệu quả.
Issue cuối cùng được fix bằng thay đổi connection-pool configuration
và restart workers.

[Detail]
...

[Keywords]
payment API, 502, DB pool, connection pool, restart workers
```

Nhờ vậy Episode có thể match cả query broad lẫn query có detail cụ thể hơn.

---

## 6. Episode assignment

Message mới được xử lý theo micro-batch.

Episode Builder nhận:

- batch message mới;
- recent active Episodes;
- Episode chứa replied-to message;
- semantic-nearest Episodes;
- sender/timestamp/reply metadata;
- provisional tail của batch trước.

Model quyết định:

```text
ASSIGN_EXISTING_EPISODE
CREATE_NEW_EPISODE
UNCERTAIN / KEEP_PROVISIONAL
```

Assignment thực hiện ở **message level** vì group chat có thể interleave:

```text
E7: checkout incident
E8: summer trip

m101: Redis vẫn ổn        → E7
m102: Đà Lạt thì sao?     → E8
m103: check pool size đi  → E7
```

Episode vì vậy không bắt buộc contiguous tuyệt đối trong message stream.

---

## 7. Provisional tail

Streaming segmentation không nên commit chắc chắn message cuối batch khi chưa đủ future context.

```text
batch 1:
m1
m2
m3   ← provisional

batch 2:
m3 + m4 + m5
```

Sau batch sau:

- `m3`, `m4` có thể được commit;
- `m5` trở thành provisional tail mới.

Idle timeout chỉ force-flush khi conversation thực sự ngưng đủ lâu.

Episode đã đóng không nên reopen nhiều tuần sau chỉ vì topic quay lại. Continuity dài hạn được xử lý ở Topic.

---

## 8. Topic memory

Topic là cumulative representation của một long-running storyline.

### 8.1 Topic representation

```ts
Topic {
  id
  conversation_id

  title
  summary
  keywords[]
  entities[]
  participants[]

  start_time
  last_active_at
  state

  embedding
  version
}
```

Topic summary phải giữ các **key developments** từ member Episodes, bao gồm cả outcome/resolution nếu đó là milestone quan trọng.

Ví dụ:

```text
Topic title:
Debugging production payment API 502 incident

Topic summary:
Payment API bắt đầu trả 502 sau deployment. Team ban đầu điều tra
nginx, sau đó xác định DB connection-pool exhaustion. Việc chỉ tăng
pool size không giải quyết hoàn toàn. Cuối cùng issue được fix bằng
thay đổi configuration và restart application workers.
```

Nếu user hỏi:

```text
"lần trước payment API fix kiểu gì?"
```

Topic đã chứa đủ signal để route vào đúng storyline.

### 8.2 Topic không phải category

Không tạo:

```text
Server Problems
Travel
Relationships
```

Mà tạo:

```text
Debugging production payment API 502 incident
Planning the summer trip
A's relocation to Ho Chi Minh City
```

### 8.3 Topic searchable representation

Theo hướng HyperMem:

```text
TITLE      × high weight
KEYWORDS   × medium weight
SUMMARY    × base weight
ENTITIES   × base/medium weight
```

Topic và Episode **được phép chứa cùng entity và development**.

---

## 9. Topic affiliation và consolidation

Khi Episode đủ ổn định, candidate Topics được tạo từ hai đường:

```text
New Episode
   ├── direct Topic search
   └── historical Episode search
              ↓
          parent Topics
              ↓
      Candidate Topic Set
```

Đây là một cơ chế chống coarse-summary miss ngay từ ingest.

LLM judge không trả lời:

> “Topic nào semantic-similar nhất?”

Mà trả lời:

> “Episode này có phải một stage/update/return của cùng ongoing situation mà Topic đang đại diện không?”

Kết quả:

```text
SAME_THREAD
NEW_TOPIC
REFINE_AND_ATTACH
UNCERTAIN
```

### 9.1 Topic consolidation

Khi attach Episode mới, Topic không chỉ thêm `episode_id`.

Nó phải cập nhật:

- summary;
- keywords;
- entities;
- participants;
- time range;
- major development/outcome.

Member Episode cũ không cần rewrite.

---

## 10. Media representation

Media có **hai identity song song**.

### 10.1 Conversational identity

```text
Image
  ↓
Message
  ↓
Episode
  ↓
Topic
```

Đường này cho biết:

- ai gửi;
- gửi lúc nào;
- đang reply ai;
- conversation đang nói về gì;
- ảnh thuộc event/storyline nào.

### 10.2 Perceptual identity

```text
Image
  ├── SigLIP embedding
  ├── optional caption representation
  ├── lazy OCR cache
  ├── lazy VLM observations
  └── optional identity/face features
```

V1 bắt buộc chỉ cần `SigLIP embedding`.

Caption/OCR/VLM có thể thêm theo demand và cache lại.

---

## 11. Search indexes

Search Layer duy trì nhiều index độc lập nhưng join qua canonical IDs.

### 11.1 Text hierarchy indexes

```text
Topic index
  title + summary + keywords + entities

Episode index
  title + summary + detailed_content + keywords + entities

Message index
  raw text + optional contextualized text
```

Mỗi index có thể dùng:

```text
BM25 / lexical
+
dense embedding
+
RRF / reranker
```

### 11.2 Visual indexes

```text
Image SigLIP vectors
Video frame/scene SigLIP vectors
```

Optional:

```text
caption text index
OCR text index
face/person index
```

### 11.3 Không cộng trực tiếp score khác model

Không làm:

```text
0.5 * episode_cosine + 0.5 * siglip_cosine
```

vì score distribution khác nhau.

Ưu tiên:

- hard filter cho explicit constraints;
- rank fusion trong cùng family;
- evidence agreement qua structural links;
- reranker/verifier ở candidate set nhỏ.

---

## 12. Nguyên tắc query: semantic level khác modality

Đây là thay đổi quan trọng nhất so với kiến trúc cũ.

Không tạo mặc định:

```text
topic_query
episode_query
message_query
```

Topic và Episode là **denormalized representations của cùng storyline**, nên cùng một **semantic context anchor** có thể được search qua cả hai level.

Ví dụ query:

```text
"ảnh con chó B gửi trong chuyến Đà Lạt"
```

Context anchor:

```text
B sharing photos during the Da Lat trip
```

Anchor này có thể match:

- Topic `Da Lat trip`;
- Episode `B shared photos during the trip`;
- Message context.

Ngược lại, khi target modality khác query modality, cần tạo **modality-specific anchor**.

Ví dụ visual target:

```text
"a photo showing a Starbucks cup beside a laptop"
```

Đây là V-Mem-style principle:

> rewrite/search according to **modality gap**, không phải theo hierarchy level.

---

## 13. QueryUnderstanding output

Query Analyzer chỉ cần tạo một representation nhỏ và có cấu trúc:

```ts
QueryIntent {
  query_modality
  target_modality

  semantic_context_anchor
  textual_precision_terms[]

  visual_anchor
  reference_image_id?

  people[]
  entities[]
  sender_id?
  time_range?

  hard_constraints[]
  soft_constraints[]
}
```

Ví dụ:

```text
User:
"Tìm ảnh có cốc Starbucks cạnh laptop mà B gửi lúc mọi người
đang nói về chuyến Đà Lạt"
```

sinh:

```yaml
target_modality: image

semantic_context_anchor: >
  B sharing images during the Da Lat trip discussion

visual_anchor: >
  a Starbucks cup positioned beside a laptop

sender_id: B
hard_constraints:
  - media_type=image
  - sender=B
```

Không cần tạo một prompt riêng cho Topic và Episode.

---

## 14. V-Mem-style modality routing

Retrieval được chọn theo **query modality × target evidence modality**.

### 14.1 T → T

User chỉ có text và target cũng là textual/conversational evidence.

Ví dụ:

```text
"Lần trước lỗi payment API fix như nào?"
```

Route:

```text
semantic context anchor
        ↓
Topic / Episode / Message text retrieval
        ↓
structural propagation
        ↓
raw textual evidence
```

### 14.2 T → I

User query là text nhưng target là image.

Ví dụ:

```text
"ảnh có cốc Starbucks cạnh laptop"
```

Generate `visual_anchor` gần distribution của image representation:

```text
"Starbucks takeaway cup positioned beside an open laptop"
```

Search lanes:

```text
SigLIP text→image
+
optional hypothetical-caption → caption index
```

Nếu query có conversation context, semantic context lane chạy song song.

### 14.3 I + T → I

User gửi reference image và mô tả thêm bằng text, target là image.

Ví dụ:

```text
[reference image]
"tìm cái giống cái này mà B gửi ở Đà Lạt"
```

V-Mem cho thấy visual→visual nên là lane mạnh nhất khi query image trực tiếp cung cấp perceptual anchor.

Route:

```text
reference image
    ↓
SigLIP image→image

text context
    ↓
Topic/Episode/Message context evidence
```

Không ép caption/text lane có trọng số ngang visual lane nếu ablation cho thấy làm giảm precision.

### 14.4 I + T → T

User gửi image nhưng target là textual evidence.

Ví dụ:

```text
[screenshot]
"lúc gửi cái này bọn mình đang fix lỗi gì?"
```

VLM/OCR extract/enrich text anchor từ query image:

```text
hostname, error string, UI labels, visible entities
```

Sau đó append/enrich raw text query và search Topic/Episode/Message.

---

## 15. Search fine, propagate through structure

V-Mem dùng nguyên tắc “search fine, return coarse” bằng cách retrieve fine-grained unit rồi collapse về shared parent round.

Trong hierarchy này, nguyên tắc được generalize thành:

> Search ở node nơi clue tự nhiên tồn tại, sau đó propagate evidence qua structural links tới object cần trả về.

Ví dụ:

```text
visual clue
   ↓
Media hit
   ↓
Message
   ↓
Episode
   ↓
Topic
```

hoặc:

```text
context clue
   ↓
Episode hit
   ↓
child Messages / Media
```

Evidence không cần xuất phát từ cùng một level.

---

## 16. Contextual text retrieval

Với query broad/contextual:

```text
"tại sao cuối cùng team đổi library khi fix payment API?"
```

Topic có thể là entry point mạnh vì summary chứa toàn storyline:

```text
issue → attempts → diagnosis → decision → outcome
```

Flow:

```text
semantic_context_anchor
       ↓
Topic retrieval
       ↓
member Episodes
       ↓
Episode reranking
       ↓
Messages quanh evidence
```

Topic không phải answer source bắt buộc. Nó là long-range semantic representation và candidate scope.

Nếu query match Episode trực tiếp mạnh hơn Topic, Episode có thể trở thành entry point mà không cần Topic approve trước.

---

## 17. Exact image in a specific context

Đây là query quan trọng nhất của architecture.

Ví dụ:

```text
"Tìm cái ảnh có cốc Starbucks cạnh laptop mà B gửi lúc mọi người
đang nói về chuyến Đà Lạt."
```

Query có hai clue thuộc hai representation khác nhau:

```text
Context clue:
B gửi ảnh trong conversation/chuyến Đà Lạt

Perceptual clue:
cốc Starbucks cạnh laptop
```

### 17.1 Không dùng hard intersection của hai top-K list

Không làm đơn giản:

```text
Episode top-K ∩ SigLIP top-K
```

vì một lane có thể miss candidate đúng dù lane còn lại có evidence rất mạnh.

### 17.2 Evidence được bind qua hierarchy

Context lane:

```text
semantic_context_anchor
  ↓
Topic / Episode / Message hits
```

Visual lane:

```text
visual_anchor
  ↓
SigLIP image hits
```

Sau đó map mọi candidate vào structural path:

```text
image_i
  → message_i
  → episode_i
  → topic_i
```

Candidate image được đánh giá bằng **evidence support trên path**, không chỉ bằng vector score.

Ví dụ:

```text
image_91
  visual: Starbucks + laptop       ✓
  sender: B                        ✓
  Episode: Da Lat photo sharing    ✓
  Topic: Da Lat trip               ✓

image_702
  visual: Starbucks + laptop       ✓✓
  sender: C                        ✗
  Episode: desk setup discussion   ✗
```

`image_91` thắng dù SigLIP của `image_702` có thể cao hơn.

### 17.3 Context-first optimization, không phải hard gate

Nếu context lane rất chắc:

```text
Topic/Episode
   ↓
23 candidate images
   ↓
SigLIP scoped search
```

để giảm noise và latency.

Nhưng nếu scoped search không đủ evidence:

```text
global SigLIP
   ↓
Media hit
   ↓
parent Message / Episode / Topic
   ↓
context verification
```

Context memory giúp efficiency, nhưng không được làm mất recall.

---

## 18. Evidence state thay vì fixed pipeline

Retrieval Controller giữ một `EvidenceState`:

```ts
EvidenceState {
  topic_hits[]
  episode_hits[]
  message_hits[]
  media_hits[]

  satisfied_constraints[]
  unresolved_constraints[]

  candidate_paths[]
}
```

Một candidate path:

```ts
CandidatePath {
  media_id?
  message_id?
  episode_id?
  topic_id?

  evidence: [
    {source: "siglip", rank: 2},
    {source: "episode", rank: 1},
    {source: "sender_constraint", matched: true}
  ]
}
```

Controller chỉ tiếp tục search cho phần evidence còn thiếu.

---

## 19. Rank fusion và candidate scoring

### 19.1 Hard constraints

Nếu user nói rõ:

```text
B gửi
tháng trước
ảnh
trong group X
```

thì đây nên là filter khi dữ liệu reliable:

```text
sender_id = B
media_type = image
conversation_id = X
timestamp ∈ range
```

Không biến chúng thành soft semantic score.

### 19.2 Same-family fusion

BM25 + dense text retrieval có thể dùng RRF.

Ví dụ:

```text
Topic BM25
Topic dense
→ RRF Topic ranking
```

Tương tự Episode và Message.

### 19.3 Cross-modality evidence

Không cộng raw score SigLIP và text embedding trực tiếp.

Ưu tiên một trong các cách:

1. rank-based fusion;
2. normalized/calibrated score sau khi có eval data;
3. feature-based reranker;
4. final VLM/LLM verifier trên candidate set nhỏ.

### 19.4 Structural support

Một candidate được boost khi nhiều evidence độc lập converge vào cùng structural path:

```text
visual hit
+
sender match
+
episode context match
+
topic context match
```

Nhưng không được yêu cầu tất cả lane phải hit, nếu clue không thuộc representation đó.

---

## 20. OCR và VLM

OCR/VLM là **lazy verifier / enrichment**, không phải default full-corpus processing.

### OCR phù hợp khi

- screenshot có exact error string;
- UI label;
- code/text trong image;
- document screenshot.

### VLM phù hợp khi

- spatial relation;
- chart reasoning;
- complex scene relation;
- visual state;
- cần verify nhiều visual constraints cùng lúc.

Ví dụ:

```text
"cốc Starbucks nằm bên trái laptop"
```

SigLIP dùng để recall candidate:

```text
500k images
  ↓ SigLIP
Top 20
```

sau context/metadata filtering:

```text
Top 3–8
  ↓ VLM verify
Final image
```

Không dùng VLM scan toàn corpus.

---

## 21. Retrieval Controller

Controller là bounded state machine, không phải general autonomous agent.

Action set:

```text
SEARCH_TOPIC
SEARCH_EPISODE
SEARCH_MESSAGE
SEARCH_VISUAL_T2I
SEARCH_VISUAL_I2I
SEARCH_CAPTION
SEARCH_OCR

EXPAND_PARENT
EXPAND_CHILDREN
READ_LOCAL_CONTEXT

GENERATE_VISUAL_ANCHOR
ENRICH_TEXT_FROM_IMAGE

RUN_VLM_VERIFY
STOP
```

Controller luôn biết:

- target modality;
- constraints nào đã chứng minh;
- constraints nào chưa;
- evidence hiện đang nằm ở node nào.

---

## 22. Query routing examples

### 22.1 Broad process query

```text
"Tại sao cuối cùng team đổi library để fix payment API?"
```

Preferred route:

```text
T→T
Topic → Episode → Message
```

### 22.2 Exact textual detail

```text
"proxy_read_timeout lúc đó đổi từ bao nhiêu lên bao nhiêu?"
```

Preferred route:

```text
Message lexical/dense
+
Episode index
```

Nếu Message hit trước:

```text
Message → Episode → Topic
```

### 22.3 Pure visual query

```text
"tìm ảnh có cốc Starbucks cạnh laptop"
```

Preferred route:

```text
T→I
visual anchor → SigLIP
```

Topic không cần tham gia nếu query không có contextual clue.

### 22.4 Visual + context query

```text
"ảnh có cốc Starbucks cạnh laptop mà B gửi trong chuyến Đà Lạt"
```

Preferred route:

```text
context lane + visual lane
        ↓
structural evidence binding
        ↓
candidate image
```

### 22.5 Reference-image query

```text
[image]
"tìm ảnh giống cái này mà A gửi"
```

Preferred route:

```text
I+T→I
SigLIP image→image dominant
+
sender constraint
```

### 22.6 Image asks for text context

```text
[screenshot]
"lúc gửi cái này tụi mình đang fix lỗi gì?"
```

Preferred route:

```text
I+T→T
OCR/VLM enrich text anchor
→ Episode/Topic/Message search
```

---

## 23. Memory revision

Memory structure là derived state và có thể sai.

Query-time retrieval có thể tạo revision signal nếu:

```text
Topic route miss
nhưng Episode/Message/Media hit rất mạnh
và structural context cho thấy memory affiliation hiện tại không hợp lý.
```

Revision signal:

```ts
RevisionSignal {
  source_node_id
  current_parent_id?
  candidate_parent_id?
  evidence_ids[]
  reason
  confidence
}
```

Repair chạy async, không mutate mạnh memory ngay trong user request.

Lifecycle:

```text
Build → Retrieve → Detect inconsistency → Revise → Reuse
```

---

## 24. Storage và indexing

V1 có thể triển khai bằng:

```text
Backend API
AI workers
PostgreSQL
pgvector
Object Storage
Optional OpenSearch/Elasticsearch
```

### PostgreSQL canonical tables

```text
Messages
Media

Episodes
EpisodeMessages

Topics
EpisodeTopics

MemoryDecisions
RevisionSignals
```

### Derived indexes

```text
Topic lexical index
Topic vector index

Episode lexical index
Episode vector index

Message lexical index
Message vector index

Media SigLIP index

Optional:
Caption index
OCR index
Face/person index
```

Search indexes có thể rebuild từ canonical/derived memory records.

---

## 25. Suggested schemas

### Episode

```ts
Episode {
  id: string
  conversation_id: string

  title: string
  summary: string
  detailed_content: string

  keywords: string[]
  entities: string[]
  participants: string[]

  start_time: datetime
  end_time: datetime
  state: "provisional" | "active" | "closed"

  embedding_version: string
  prompt_version: string
  version: number
}
```

### Topic

```ts
Topic {
  id: string
  conversation_id: string

  title: string
  summary: string

  keywords: string[]
  entities: string[]
  participants: string[]

  start_time: datetime
  last_active_at: datetime
  state: "active" | "dormant" | "closed"

  embedding_version: string
  prompt_version: string
  version: number
}
```

### MediaIndexRecord

```ts
MediaIndexRecord {
  media_id: string
  message_id: string

  modality: "image" | "video_frame" | "video_scene"

  siglip_embedding

  caption?: string
  caption_embedding?: vector

  ocr_text?: string
  ocr_version?: string

  visual_observations?: object
}
```

---

## 26. Async processing và versioning

Mọi AI-derived artifact phải ghi:

```text
model_version
prompt_version
embedding_version
created_at
```

Workers phải idempotent.

Memory decision audit:

```ts
MemoryDecision {
  operation
  source_ids[]
  target_ids[]
  evidence_ids[]
  confidence
  model_version
  prompt_version
  created_at
}
```

Điều này cho phép trace:

- vì sao message thuộc Episode;
- vì sao Episode thuộc Topic;
- vì sao Topic được rename/refine;
- vì sao query-time recovery tạo revision signal.

---

## 27. V1 boundary

V1 nên tập trung kiểm chứng ba giả thuyết chính.

### Hypothesis A — hierarchical memory

Topic/Episode representation có tăng contextual recall và giảm search space so với flat raw retrieval không?

### Hypothesis B — denormalized memory

Việc Topic giữ key developments/outcomes và Episode giữ detailed content có giảm coarse-level miss mà không gây quá nhiều semantic noise không?

### Hypothesis C — modality routing

Việc route T→T, T→I, I+T→I, I+T→T và propagate evidence qua hierarchy có tốt hơn một unified similarity search không?

### V1 bắt buộc

- raw Message persistence;
- raw Media persistence;
- Message lexical+dense index;
- SigLIP image index;
- Episode Builder;
- provisional tail;
- Episode retrieval-rich representation;
- Topic affiliation + cumulative summary/keywords/entities;
- Topic/Episode lexical+dense retrieval;
- query modality router;
- visual anchor generation cho T→I;
- structural parent/child traversal;
- bounded retrieval controller;
- lazy OCR/VLM verification.

### Chưa cần V1

- full Event Knowledge Graph;
- atomic Fact layer như HyperMem;
- full-corpus OCR;
- full-corpus VLM captions nếu cost cao;
- complex merge/split automation;
- generic deep-research agent;
- learned cross-modal ranker.

Atomic Fact layer chỉ nên thêm nếu evaluation chứng minh Message/Episode retrieval không đủ cho text precision queries.

---

## 28. Evaluation

Evaluation phải tách ba lớp: memory construction, retrieval và final answer.

### 28.1 Memory construction

```text
Episode Assignment Accuracy
Episode Boundary / Coherence
Topic Affiliation Accuracy
Topic Over-Merge Rate
Topic Over-Split Rate
Topic Summary Coverage
Episode Detail Preservation
```

### 28.2 Text retrieval

```text
Topic Recall@K
Episode Recall@K
Message Recall@K
Topic→Episode Path Recall
Episode-direct Recovery Rate
Raw-message Recovery Rate
```

### 28.3 Multimodal retrieval

```text
Media Recall@K
Text→Image Recall@K
Image→Image Recall@K
Context+Visual Recall@K
OCR Query Recall@K
VLM Verification Precision
```

### 28.4 Routing

```text
Route Accuracy
T→T success rate
T→I success rate
I+T→I success rate
I+T→T success rate
Average retrieval actions/query
Global visual fallback rate
```

### 28.5 Hierarchical value

Một metric quan trọng:

```text
Có bao nhiêu query mà flat Message/Media search miss,
nhưng Topic/Episode route recover được?
```

Và chiều ngược lại:

```text
Có bao nhiêu query mà Topic route miss,
nhưng fine-grained Message/Media retrieval recover được?
```

Hai metric này cho biết hierarchy và fine-grained retrieval có bổ sung cho nhau thật hay không.

---

## 29. End-to-end example: server incident

Conversation qua nhiều ngày:

```text
E1: Payment API bắt đầu trả 502.
E2: Team kiểm tra nginx, tăng proxy timeout nhưng không hết.
E3: Team phát hiện DB pool exhaustion.
E4: Tăng pool size nhưng vẫn lỗi.
E5: Đổi connection-pool config/library + restart workers và fix được.
```

Topic:

```text
Debugging production payment API 502 incident
```

Topic summary giữ:

```text
502 appeared → nginx investigation → DB pool diagnosis →
failed pool-size attempt → final config/library change → resolved.
```

User hỏi:

```text
"Lần trước lỗi payment API fix thế nào?"
```

Retrieval:

```text
Query semantic anchor
       ↓
Topic hit
       ↓
Episode E5 hit
       ↓
raw Messages around E5
       ↓
answer
```

User hỏi:

```text
"proxy_read_timeout lúc đó đổi 30 lên bao nhiêu?"
```

Topic có thể không giữ detail này.

Retrieval:

```text
Message lexical hit / Episode detailed-content hit
       ↓
Episode E2
       ↓
optional parent Topic for context
       ↓
answer
```

Không bắt Topic trở thành hard gate.

---

## 30. End-to-end example: exact image inside a context

Conversation:

```text
Topic:
Da Lat trip of A and B

Episode:
B shares photos while everyone discusses the trip

Messages:
A: Milo hồi bé nhìn buồn cười vl.
B: đây này
B: [image_91]
```

Trong `image_91` có:

```text
Milo
laptop
Starbucks cup
```

User hỏi:

```text
"Tìm ảnh có cốc Starbucks cạnh laptop mà B gửi lúc nói về
chuyến Đà Lạt."
```

QueryUnderstanding:

```yaml
semantic_context_anchor: "B sharing photos during the Da Lat trip"
visual_anchor: "Starbucks cup beside a laptop"
sender: B
target_modality: image
```

Context retrieval:

```text
Topic / Episode / Message
       ↓
context evidence for Episode
```

Visual retrieval:

```text
SigLIP text→image
       ↓
image_91, image_702, ...
```

Structural binding:

```text
image_91
 → message from B
 → matching Episode
 → matching Topic

image_702
 → unrelated sender/context
```

Nếu visual relation cần exact verification:

```text
Top candidate images
       ↓
VLM verify:
"Starbucks cup beside laptop?"
       ↓
Final result
```

Đây là kiến trúc kết hợp hai ý chính:

1. **HyperMem:** build retrieval-rich, intentionally overlapping semantic representations ở Topic/Episode để coarse-to-fine context retrieval hoạt động.
2. **V-Mem:** route retrieval theo query/target modality, search clue ở representation phù hợp, sau đó bind/collapse evidence qua shared structural parent thay vì ép một embedding space giải quyết mọi thứ.

---

## 31. Design rules tóm tắt

1. `Message` và `Media` là source of truth.
2. `Topic` và `Episode` là derived, retrieval-oriented memory.
3. Topic/Episode **duplicate key information có chủ đích** ở độ chi tiết khác nhau.
4. Topic phải chứa major development/outcome, không chỉ category/theme label.
5. Episode phải giữ detailed content đủ để retrieval các stage/detail quan trọng.
6. Không mặc định tạo `topic_query`, `episode_query`, `message_query` riêng.
7. Tạo **semantic context anchor** dùng qua textual hierarchy.
8. Chỉ tạo anchor khác khi clue/target đổi modality: visual anchor, OCR terms, image-derived text anchor.
9. Không bắt Topic là hard gate.
10. Search clue ở representation nơi clue tự nhiên tồn tại.
11. Propagate evidence qua `Media ↔ Message ↔ Episode ↔ Topic`.
12. Không cộng trực tiếp raw score từ text embedding và SigLIP.
13. Explicit metadata constraints nên là filter.
14. SigLIP dùng cho high-recall perceptual retrieval; VLM dùng để verify candidates khó.
15. OCR/VLM chạy lazy và cache kết quả.
16. Retrieval Controller là bounded state machine, không phải general-purpose agent.
17. Query-time recovery có thể tạo revision signal để memory ngày càng tốt hơn.

---

## 32. Nguồn thiết kế

- **HyperMem — Hypergraph Memory for Long-Term Conversations**: Topic → Episode → Fact hierarchy, retrieval-rich duplicated representations và coarse-to-fine retrieval.  
  https://github.com/EverMind-AI/HyperMem

- **V-Mem — Modality-Routed Retrieval for Long-Term Multimodal Agentic Memory**: 2×2 modality routing, generated anchors, SigLIP visual retrieval, shared-parent collapse và nguyên tắc search ở representation phù hợp với modality.  
  https://github.com/Dingyi-Kang/V-Mem

