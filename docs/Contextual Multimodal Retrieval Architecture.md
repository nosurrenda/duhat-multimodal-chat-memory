# System Architecture — Contextual Multimodal Retrieval for Chat Applications

image

---

## 1. Tổng quan

Hệ thống được xây quanh raw conversation và một retrieval layer có thể tìm kiếm đồng thời text, metadata và media.
Message và media gốc luôn là source of truth. Khi user query, hệ thống lấy candidate từ nhiều index, mở rộng conversation quanh các candidate đó, trích xuất clue mới nếu có, rồi kiểm tra media nào thực sự thuộc context mà user đang nhắc tới.

Luồng tổng quát:

Raw Chat
   ↓
Search Indexes
   ↓
Query Analysis
   ↓
Initial Retrieval
   ↓
Context Expansion
   ↓
Clue Extraction
   ↓
Context–Media Resolution
   ↓
Evidence Check
   ├── Enough → Result
   └── Missing Evidence + Useful New Clue
              ↓
          Search Jump
              ↓
          New Anchor
              ↓
       Context Expansion ...

Architecture không yêu cầu mọi query đi theo cùng một pipeline. Query có thể bắt đầu từ text, media, metadata hoặc một relation trực tiếp như reply/thread.

Điểm quan trọng là hệ thống phân biệt rõ hai thao tác:

- **Context Expansion**: khai thác thêm evidence quanh anchor hiện tại.
- **Search Jump**: dùng clue mới vừa thu được để thực hiện một retrieval mới trên toàn search scope và đi tới một vùng khác của history.

Search Jump chỉ xảy ra khi round hiện tại tạo ra information mới có ích. Hệ thống không được lặp lại cùng một search chỉ vì evidence chưa đủ.

---

## 2. Các thành phần chính

Ở mức logic, hệ thống gồm bốn phần.

**Chat Core** lưu message, media và metadata gốc. Đây là phần duy nhất nằm trên synchronous write path.

**Indexing Pipeline** nhận event từ Chat Core và cập nhật text index, vector index và visual index theo cách async.

**Retrieval Layer** cung cấp các primitive như lexical search, dense search, visual search, metadata filtering, structural traversal và context expansion.

**Query Engine** phân tích query, gọi các primitive cần thiết, giữ evidence state, trích xuất clue mới, quyết định có cần thực hiện Search Jump hay không và dừng khi đã đủ evidence hoặc không còn information gain.

V1 không cần tách từng phần thành microservice. Một backend chính, worker queue, PostgreSQL/pgvector và object storage là đủ.

---

## 3. Canonical data và write path

Message phải được lưu trước khi bất kỳ AI processing nào diễn ra.
Incoming Message
      ↓
Persist Raw Data
      ↓
Write Outbox Event
      ↓
Acknowledge Request
      ↓
Async Indexing

Schema tối thiểu:
Message {
    id
    conversation\_id
    channel\_id
    sender\_id
    text
    timestamp
    reply\_to\_message\_id
    thread\_id
    created\_at
    updated\_at
    deleted\_at
}

Media chỉ giữ relation trực tiếp với message:
Media {
    id
    message\_id
    type
    object\_uri
    created\_at
}

Relation quan trọng nhất là:
Media → Message

Mọi conversational context của media đều được lấy ngược từ message gốc.

---

## 4. Async indexing

Embedding hoặc media processing không nên nằm trên request gửi message.
Chat Core chỉ ghi dữ liệu và phát event. Worker xử lý indexing sau đó:
Outbox / Queue
      │
      ├── Text Worker
      │      ├── lexical index
      │      └── dense embedding
      │
      └── Media Worker
             └── visual embedding

Worker phải idempotent để retry không sinh duplicate.
Khi message bị edit, text embedding và lexical index được cập nhật lại. Khi message hoặc media bị xóa, search index cũng phải loại bỏ hoặc tombstone object tương ứng.
Mỗi derived artifact nên lưu model version để có thể rebuild khi đổi embedding model.

---

## 5. Search indexes

Message được index theo hai hướng.
Lexical retrieval dùng cho những thứ cần match chính xác như error string, hostname, ID, tên riêng hoặc keyword.
Dense retrieval dùng cho semantic paraphrase.
Image có visual embedding riêng bằng SigLIP hoặc model tương đương.
Message
├── Lexical Index
└── Dense Vector Index

Image
└── Visual Vector Index

Metadata như sender, timestamp, channel, reply và thread không cần embedding. Chúng được query trực tiếp từ structured storage.
Search index chỉ là derived state. Canonical data vẫn nằm trong database và object storage.

---

## 6. Permission và search scope

Permission phải được áp dụng trước hoặc trong retrieval, không phải retrieve xong mới filter.

Query
  ↓
Resolve Accessible Channels / Threads
  ↓
Text / Media Retrieval

Nếu user không có quyền đọc một channel thì message và media của channel đó không được xuất hiện trong candidate set.
Điều này cũng áp dụng cho context expansion: một hit hợp lệ không được phép kéo theo surrounding messages mà user không có quyền truy cập.

Search Jump cũng phải giữ nguyên search scope đã resolve ban đầu. Một clue mới không được phép làm controller nhảy sang channel/thread mà user không có quyền đọc.

---

## 7. Query analysis

Query Analyzer chỉ cần xác định user đang tìm object nào và những constraint nào cần được thỏa mãn.
Ví dụ:

> “Tìm ảnh con chó B gửi trong chuyến Đà Lạt.”

có thể được hiểu thành:
target: image
visual: dog
context: Dalat trip
sender: B

Còn:

> “Screenshot lỗi Connection refused hôm deploy Acme.”

có thể thành:
target: image
media\_type: screenshot
content: Connection refused
context: Acme deployment

Analyzer không cần tạo một plan dài. Nó chỉ cung cấp structured constraints cho retrieval controller.

---

## 8. Structural retrieval trước LLM reasoning

Những relation đã tồn tại thật trong dữ liệu phải được dùng trực tiếp thay vì hỏi LLM suy luận lại.
Ví dụ:
reply\_to
thread\_id
sender\_id
timestamp
media.message\_id

Nếu user hỏi:

> “ảnh reply cái message này”

thì hệ thống nên:
Message
→ Reply Tree
→ Media

thay vì chạy semantic search.
Tương tự, query “ảnh B gửi hôm qua” có thể dùng metadata filter rất mạnh trước khi cần dense retrieval.
LLM chỉ nên được dùng ở những chỗ có ambiguity hoặc cần semantic judgment.

---

## 9. Parallel retrieval

Với query composite, các constraint ban đầu có thể được search song song.

                   Query
                      │
          ┌───────────┼───────────┐
          ↓           ↓           ↓
       Text         Media      Metadata
      Search       Search       Filter
          └───────────┼───────────┘
                      ↓
              Initial Candidates

Ví dụ với “ảnh chó trong chuyến Đà Lạt”:

- Text branch tìm các message liên quan tới Đà Lạt.
- Media branch dùng SigLIP tìm ảnh có chó.
- Metadata branch có thể filter sender hoặc khoảng thời gian nếu query cung cấp.

Các kết quả này chưa được coi là answer. Chúng chỉ tạo **initial anchors** và **media candidates** cho round đầu tiên.

Những round sau không nhất thiết chạy lại toàn bộ ba branch. Controller chỉ gọi retrieval primitive phù hợp với missing constraint và clue mới thu được.

---

## 10. Context expansion

Một message hit thường không đủ để hiểu conversation.
Context expansion vì vậy là một stage riêng của retrieval, không phải bước phụ sau search.

Từ một anchor message, hệ thống có thể mở rộng theo một số strategy:

- reply/thread context
- temporal neighborhood
- same-sender continuation
- participant-aware neighborhood
- semantic nearby messages

Ví dụ:

A: chuyến Đà Lạt vui thật  
B: công nhận  
A: còn ảnh nào không  
B: [IMAGE]  
B: con này gặp cạnh homestay

Nếu IMAGE được SigLIP nhận ra có chó, đoạn context trên là evidence mạnh cho việc ảnh thuộc chuyến Đà Lạt.

Context expansion phải có token/message budget rõ ràng. Các strategy cũng phải configurable để benchmark được cái nào hiệu quả nhất.

### Boundary của Context Expansion

Context expansion chỉ **khai thác vùng hiện tại**. Nó không được coi là một Search Jump chỉ vì hệ thống đọc thêm message.

Ví dụ:

M100 → M101 → M102 → M103

nếu các message này được lấy qua reply/thread hoặc neighborhood của M100 thì đây vẫn chỉ là expansion.

Trong quá trình expansion, hệ thống có thể phát hiện clue mới như `Pine Hill`, `B`, `Acme deploy`, một quoted phrase hoặc một time reference. Clue đó chỉ trở thành một bước nhảy khi controller dùng nó để tạo **một retrieval mới trên toàn search scope**.

---

## 11. Evidence-driven Search Jump

Search Jump là cơ chế giúp controller đi từ một vùng evidence hiện tại sang một vùng khác của history.
Nó không phải graph traversal tự do và cũng không phải đọc tiếp các message lân cận. Một jump luôn là **một search mới** được tạo từ information vừa thu được ở round trước.

### 11.1. Khi nào được phép jump

Controller chỉ nên tạo Search Jump khi đồng thời có ba điều kiện:

1. Evidence hiện tại vẫn thiếu ít nhất một constraint quan trọng.
2. Context expansion vừa tạo ra một clue mới chưa được search.
3. Clue đó có khả năng giúp resolve missing constraint hoặc nối current anchor với candidate media.

Nếu không có clue mới thì không có lý do để chạy thêm một global search giống round trước.

### 11.2. Clue Extraction

Clue có thể đến từ structural data hoặc nội dung conversation:

- entity hoặc place: `Pine Hill`, `Acme`, `Đà Lạt`
- person/sender: `B`, `Minh`
- event: `deploy production`, `company trip`
- quoted phrase hoặc error string
- time reference: `hôm đó`, `tháng trước`, `sáng hôm sau`
- resolved reference: `chỗ đó` → `Pine Hill`
- thread/reply relation
- media provenance hoặc message relation vừa tìm được

Extraction nên ưu tiên deterministic signal trước. LLM chỉ cần dùng khi clue có ambiguity hoặc cần resolve semantic reference.

### 11.3. Jump Query Construction

Jump query không nên copy toàn bộ original query một cách máy móc.
Nó nên kết hợp:

- clue mới có information gain
- structured filter vẫn còn hữu ích
- missing constraint cần được chứng minh

Ví dụ:

Original query:

> “ảnh chó trong chuyến Đà Lạt do B gửi”

Round 1:

Search `Đà Lạt`  
→ M100  
→ expand  
→ M103: “ở Pine Hill hôm đó B giữ hết ảnh”

New clues:

- `Pine Hill`
- sender = B

Jump query:

Search `Pine Hill` with sender = B

Đây là một global retrieval mới. Kết quả có thể nằm rất xa M100 trong timeline.

### 11.4. Jump Control

Controller phải tránh search loop và jump vô ích.
State tối thiểu nên giữ:

- `visited_queries`
- `visited_anchor_ids`
- `visited_media_ids`
- `used_clues`
- `remaining_constraints`
- `round`
- `jump_count`

Một jump chỉ hợp lệ nếu query fingerprint hoặc anchor mới chưa được visit và clue tạo ra information gain.

Ví dụ loop cần bị chặn:

Dalat → Pine Hill → Dalat → Pine Hill

V1 nên giới hạn số round/jump nhỏ, ví dụ khoảng 2–3 round tổng cộng. Nếu không còn clue mới có ích, controller phải stop hoặc trả low-confidence result thay vì tiếp tục search vô hạn.

---

## 12. Context–Media Bridge Resolver

Đây là phần trung tâm của hệ thống.
Việc tìm được đúng text và đúng image chưa đủ. Hệ thống còn phải chứng minh hai thứ liên quan tới nhau.
Ví dụ:
Text retrieval:
"Dalat trip" ✓

Visual retrieval:
dog.jpg ✓

nhưng `dog.jpg` vẫn có thể là ảnh hoàn toàn khác.
Bridge Resolver nhận candidate media cùng conversational evidence của chúng và kiểm tra:
Media
  ↓
Parent Message
  ↓
Expanded Context
  ↓
Does this context satisfy the query?

Với query:

> “ảnh chó trong chuyến Đà Lạt”

candidate phải thỏa:
VisualMatch(dog)
AND
ContextMatch(Dalat trip)

Một ảnh có visual score rất cao nhưng không có contextual evidence không nên thắng một ảnh có visual score thấp hơn một chút nhưng có relation rõ ràng với Đà Lạt.
Bridge Resolver có thể dùng rule/graph relation trước. Chỉ khi structural evidence chưa đủ mới cần LLM semantic judgment.

---

## 13. Context-first và media-first

Không có một retrieval order cố định.

Nếu query có context mạnh:

> “ảnh trong chuyến Đà Lạt có chó”

hệ thống có thể bắt đầu:

Search "Đà Lạt"
      ↓
Relevant Messages
      ↓
Context Expansion
      ↓
Media linked to context
      ↓
Visual Search("dog")

Nếu visual clue mạnh:

> “con chó B gửi hôm đó đâu?”

có thể bắt đầu:

Visual Search("dog")
      ↓
Candidate Images
      ↓
Parent Messages
      ↓
Context Expansion
      ↓
Check Sender / Context

Hai flow này chỉ mô tả **initial direction**.
Sau khi expansion tạo clue mới, controller có thể đổi branch bằng Search Jump.

Ví dụ text-first có thể nhảy sang media-first khi clue mới xác định được sender/time range. Ngược lại media-first có thể quay về global text search khi parent context tiết lộ entity hoặc event mới.

Controller vì vậy chọn hướng đầu tiên dựa trên query, còn các round sau dựa trên evidence state và information gain.

---

## 14. Bounded retrieval loop

Một retrieval pass không phải lúc nào cũng giải được query.
Controller giữ một evidence state nhỏ nhưng đủ để biết cái gì đã chứng minh được, cái gì còn thiếu và clue nào đã được dùng.

Ví dụ:

visual: dog            ✓  
sender: B              ✓  
context: Dalat         ✗  
new_clues: [Pine Hill]  
visited_queries: [...]  
visited_anchor_ids: [...]  
round: 1

Luồng mỗi round:

Retrieve
   ↓
Context Expansion
   ↓
Extract New Clues
   ↓
Resolve Context–Media Bridge
   ↓
Update Evidence State
   ↓
Sufficiency Check
   ├── Enough → Result
   └── Missing Evidence
            ↓
     Useful New Clue?
       ├── No → Stop / Low-confidence Result
       └── Yes
            ↓
       Build Jump Query
            ↓
       Global Retrieval
            ↓
         New Anchor
            ↓
         Next Round

Query refinement chỉ tập trung vào missing constraint và clue mới, không chạy lại toàn bộ search một cách mù quáng.

Không phải mọi next round đều là multi-hop. Một round chỉ được coi là có **Search Jump** khi controller dùng information mới để thực hiện một retrieval mới trên corpus và đi tới anchor khác.

V1 nên giới hạn khoảng 2–3 round tổng cộng.
Đây là một retrieval controller có action set hữu hạn, không phải general ReAct agent.

### Hai kiến trúc được đo đối chứng

Câu trên là một **luận điểm kiến trúc**, không phải một sự thật đã được chứng minh. V1 vì vậy xây cả hai và đo:

- **Config D** — bounded controller mô tả ở §11 và §14: action set hữu hạn, 2–3 round, clue extraction và jump gate viết tay.
- **Config E** — agent loop một tầng: LLM tool-calling trên history tuyến tính, budget cứng (8 tool call, 6 turn, 20 giây), hết budget thì buộc `submit_answer` với confidence thấp.

Hai bên dùng **chung** các retrieval primitive, chung permission scope resolve trước khi retrieval, và chung bộ metric. Không bên nào bị xóa; cả hai nằm sau config flag. Không multi-agent, không thêm framework agent.

Trong Config E, Bridge Resolver không còn là một stage riêng. Nó trở thành hai phần: hướng dẫn trong prompt, và validator của `submit_answer`. **Validator chỉ chặn id bịa** — nó chứng minh một id từng xuất hiện trong kết quả tool của chính run đó, chứ không chứng minh ảnh thật sự thỏa ràng buộc context. Độ đúng của bridge vẫn đo bằng **Bridge Recall**, cho cả hai kiến trúc.

Nếu Config E ngang hoặc hơn Config D, đó là một kết quả âm đáng giá đối với chính luận điểm của tài liệu này — và benchmark tồn tại để tìm ra điều đó.

---

## 15. Long-range context

Context cần thiết có thể nằm rất xa media. Long-range retrieval vì vậy không nên dựa vào việc kéo một temporal window ngày càng lớn.

Ví dụ:

May:  
“đi Đà Lạt không?”

Context expansion quanh message này có thể tìm thấy:

“book Pine Hill đi”

Clue mới:

`Pine Hill`

Controller thực hiện:

GLOBAL SEARCH JUMP("Pine Hill")

và tìm thấy một vùng khác:

July:  
“mai 6h xuất phát, B nhớ mang máy ảnh”

Expansion tạo thêm clue:

sender = B

Controller có thể tiếp tục:

GLOBAL SEARCH JUMP("Pine Hill", sender=B)

và đi tới:

September:  
“tìm lại được ảnh cũ này”  
[IMAGE]

Sau đó Bridge Resolver kiểm tra visual evidence và conversational evidence để xác nhận IMAGE có thực sự thuộc context Đà Lạt/Pine Hill hay không.

Điểm chính là controller không “nhảy” bằng magic semantic traversal. Mỗi bước đi xa đều phải có một clue mới được lấy từ evidence trước đó và một retrieval mới có thể trace lại được.

Context dài hạn vì vậy được reconstruct theo nhu cầu của query thay vì đọc toàn bộ history.

---

## 16. Group chat interleaving

Một channel có thể chứa nhiều conversation cùng lúc:

A: prod down  
B: mai Đà Lạt nhé  
C: auth timeout  
B: 6h xuất phát  
A: DB connection full  
B: [IMAGE]

Query về production sẽ tạo một context quanh `prod`, `auth timeout`, `DB connection`.
Query về Đà Lạt lại tạo context quanh `Đà Lạt`, `6h xuất phát`, `IMAGE`.

Cùng một message stream có thể tạo ra những context khác nhau tùy query.
Context expansion vì vậy không được dựa duy nhất vào contiguous time window.

Nếu một interleaved region tạo ra clue mới, Search Jump có thể dùng clue đó để đi tới một region không liền kề khác. Điều này giúp controller không cần pre-segment toàn bộ channel thành topic cố định trước khi query.

---

## 17. Image processing

V1 tập trung vào image.
Image
→ Object Storage
→ SigLIP Embedding
→ Visual Index

Ảnh vẫn giữ `message_id`, nhờ đó visual candidate luôn có thể quay lại đúng conversation.
Image
├── Visual Representation
└── Conversational Provenance

Visual representation trả lời ảnh chứa gì.
Conversational provenance giúp xác định ảnh có ý nghĩa gì trong cuộc trò chuyện.

---

## 18. OCR và VLM

OCR/VLM chỉ được chạy khi query cần information mà visual embedding không biểu diễn tốt.
Ví dụ:

> “Screenshot có lỗi Connection refused.”

cần OCR.

> “Chart mà latency tăng mạnh sau deploy.”

có thể cần VLM.
Flow:
Text / Metadata / Visual Retrieval
              ↓
         Candidate Images
              ↓
             Top-K
              ↓
          OCR / VLM
              ↓
            Rerank

Kết quả OCR/VLM có thể cache theo `media_id + model_version`.

---

## 19. Incremental sync và thread activity

Nếu dữ liệu đến từ connector như Slack hoặc Discord, checkpoint không được chỉ dựa vào top-level message timestamp.
Một thread cũ vẫn có thể nhận reply hoặc media mới.
Do đó sync state phải theo dõi ít nhất activity của channel/thread và bảo đảm reply mới được ingest ngay cả khi parent message đã rất cũ.
Old Parent Message
      ↓
New Reply / New Media
      ↓
Index as new activity

Đây là phần quan trọng để search index không âm thầm stale.

---

## 20. Provenance và trace

Mỗi result phải giữ được đường dẫn ngược về source evidence.
Một media candidate không nên chỉ có:

media_id  
score

mà nên giữ:

media_id  
parent_message_id  
visual_score  
context_message_ids  
structural_relations  
supported_constraints

Nếu query đi qua nhiều round, trace còn nên giữ:

round  
source_anchor_id  
extracted_clues  
jump_query  
destination_anchor_ids  
missing_constraints_before_jump  
supported_constraints_after_jump

Ví dụ:

dog             ✓  
sender B        ✓  
Dalat trip      ✓

evidence:  
M118  
M121  
M124  
M126

jump trace:

M118  
→ clue: `Pine Hill`  
→ search("Pine Hill", sender=B)  
→ M124

Nhờ vậy khi retrieval sai, có thể biết lỗi nằm ở visual search, text search, context expansion, clue extraction, jump query construction, bridge resolution hay reranking.
Trace cũng là dữ liệu đầu vào cho eval harness.

---

## 21. Storage

V1 có thể dùng PostgreSQL, pgvector và object storage.
PostgreSQL giữ:
Messages
Media
Channels
Threads
Permissions

pgvector giữ dense text embedding và visual embedding.
PostgreSQL full-text search có thể đủ cho prototype. Khi corpus lớn, lexical retrieval có thể chuyển sang OpenSearch hoặc Elasticsearch mà không thay đổi canonical data model.
Search indexes luôn có thể rebuild từ canonical data.

---

## 22. V1 boundary

V1 cần đủ để kiểm chứng một câu hỏi chính:

> Hệ thống có tìm đúng media khi user mô tả cả nội dung media lẫn conversational context hay không?

Phần ingest gồm raw storage, async text indexing, visual embedding và metadata/reply relations.

Phần query gồm query analysis, hybrid text retrieval, visual retrieval, structural traversal, configurable context expansion, clue extraction, evidence-driven Search Jump, Context–Media Bridge Resolver và bounded retrieval controller.

OCR/VLM chạy lazy.

Các thành phần như full-corpus captioning, knowledge graph, precomputed topic/episode hierarchy hoặc general research agent chưa cần nằm trong V1.

---

## 23. Evaluation harness

Eval phải chạy trên cùng dataset nhưng cho phép thay đổi retrieval configuration.

Ví dụ:

Config A:  
temporal context only

Config B:  
reply + temporal

Config C:  
reply + temporal + participant

Config D:  
context expansion + Search Jump

Mỗi run phải lưu snapshot của:

text_embedding_model  
visual_model  
BM25/dense weights  
top_k  
context strategy  
context window  
clue extraction strategy  
jump query strategy  
max retrieval rounds  
max jumps  
LLM model  
prompt version

Nhờ vậy khi thay một component có thể biết chính xác quality tăng hay giảm vì đâu.
Dataset cần chứa gold media và gold evidence messages, không chỉ final answer.

Với các case long-range, eval nên lưu thêm gold intermediate evidence hoặc gold clue nếu có thể. Điều này cho phép phân biệt:

- initial retrieval fail
- context expansion fail
- clue extraction fail
- jump fail
- bridge fail

thay vì chỉ biết final result đúng hay sai.

---

## 24. Metrics

Metric cuối cùng quan trọng nhất là **Exact Media Accuracy**.
Ngoài ra cần đo **Media Recall@K** để biết visual/search candidate generation có bỏ mất đáp án không và **Message Evidence Recall** để biết conversation evidence có được retrieve hay không.

**Context Precision** đo context expansion có kéo vào quá nhiều conversation không liên quan không.

Một metric đặc biệt quan trọng là **Context–Media Bridge Recall**. Nếu hệ thống tìm được đúng message về Đà Lạt và đúng ảnh chó nhưng không xác định được chúng có liên quan, query vẫn phải tính là fail.

Với query composite có thể đo thêm **Constraint Satisfaction Accuracy**. Ví dụ result của:

dog  
AND sender B  
AND Dalat trip

phải thỏa toàn bộ ba constraint.

Đối với Search Jump nên theo dõi thêm:

- **Jump Success Rate**: jump có dẫn tới gold evidence/gold region hay không
- **Useful Clue Precision**: clue được chọn có thực sự giúp retrieval hay không
- **Average Jumps / Query**
- **No-gain Jump Rate**: tỷ lệ jump không thêm evidence mới
- **Loop Prevention Count**: số jump bị chặn vì query/anchor đã visit

Ở mức hệ thống cần theo dõi average retrieval rounds, search calls/query, LLM calls/query, OCR/VLM calls, latency p50/p95 và cost/query.

Evaluation nên tách riêng direct visual, context-only, visual+context, referential, interleaved, long-range và multi-round jump để biết architecture fail ở loại query nào.

---

## 25. Luồng tổng thể

Ingest:

Message
├→ Raw Store
├→ Outbox / Queue
├→ Lexical Index
├→ Dense Embedding
└→ Metadata / Reply Relations

Image
├→ Object Storage
├→ Message Link
└→ SigLIP Embedding

Query:

                         Query
                           │
                     Query Analyzer
                           │
             ┌─────────────┼─────────────┐
             ↓             ↓             ↓
         Text Search   Media Search   Metadata /
         BM25+dense      SigLIP       Structure
             │             │             │
             └─────────────┼─────────────┘
                           ↓
                    Initial Anchors
                           ↓
                  Context Expansion
                           ↓
                    Clue Extraction
                           ↓
                Context–Media Bridge
                           ↓
                     Evidence State
                           ↓
                   Sufficiency Check
                    ↙             ↘
                 Result          Missing
                                   ↓
                           Useful New Clue?
                            ↙            ↘
                          No              Yes
                          ↓                ↓
                    Stop / Low      Build Jump Query
                    Confidence             ↓
                                    Global Retrieval
                                           ↓
                                      New Anchor
                                           ↓
                                   Context Expansion
                                           ↓
                                      Next Round

Mục tiêu của architecture là giữ chat write path đơn giản, giữ raw data làm source of truth và chỉ sử dụng reasoning phức tạp khi query thực sự cần nó.

Phần tạo khác biệt của hệ thống nằm ở việc kết hợp ba loại evidence — **textual, structural và visual** — đồng thời có khả năng dùng evidence mới để thực hiện **bounded Search Jump** sang vùng khác của history khi local context chưa đủ.
