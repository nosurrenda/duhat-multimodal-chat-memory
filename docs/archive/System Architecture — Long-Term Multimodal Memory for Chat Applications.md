System Architecture — Long-Term Multimodal Memory for Chat Applications (v2)
1. Phạm vi kiến trúc
Hệ thống gồm ba lớp dữ liệu:

Raw conversation: message và media gốc, là source of truth.
Structural context: các quan hệ deterministic lấy thẳng từ raw data: thứ tự message, reply, mention, forward, ảnh gửi liền nhau. Lớp này không dùng model.
Semantic memory: Episode và Topic, do LLM xây từ raw conversation.
Raw Message / Media ──── Structural context (seq, reply, mention, burst)
        ↓
     Episode
        ↓
      Topic
Retrieval dùng semantic memory để thu hẹp phạm vi tìm kiếm, nhưng luôn truy cập được raw data và structural context. Semantic memory không bao giờ là con đường duy nhất để tới một message hay media. Khi Episode hoặc Topic bị gán sai, hệ thống vẫn tìm và kiểm chứng được evidence qua raw search và structural context.

Episode và Topic là derived data: có thể rebuild, cập nhật hoặc sửa mà không ảnh hưởng dữ liệu chat gốc.

2. Thành phần hệ thống
Chat Core: lưu message, media và metadata gốc. Đây là thành phần duy nhất nằm trên synchronous write path.
Structural Indexer: tính seq, các cạnh reply/mention/forward, burst. Đồng thời index raw text và media.
Memory Pipeline: xây Episode, gán Episode vào Topic, cập nhật Topic. Chạy async.
Revision Worker: các thay đổi lớn trên memory (merge, split, move), chạy async theo signal.
Search Layer: lexical index, text embedding index, visual index. Phục vụ cả raw retrieval lẫn memory retrieval.
Query Engine: phân tích query, chạy retrieval, kiểm chứng evidence, điều phối các vòng tiếp theo.
V1 không cần tách microservice. Triển khai thực tế có thể chỉ gồm backend chính, một nhóm AI worker, PostgreSQL/pgvector, object storage, và search engine nếu cần.

3. Canonical data
Message và media phải được lưu trước mọi AI processing.

Incoming message
  → Persist raw data (gán seq)
  → Acknowledge request
  → Async: structural indexing + raw index
  → Async: memory processing
Hệ thống vẫn hoạt động khi LLM, embedding service hoặc memory worker gặp lỗi. Message mới search được qua raw index ngay khi index xong, không phải chờ memory.

Message {
    id
    conversation_id
    seq                     // tăng đơn điệu trong conversation; dùng để sắp thứ tự khi timestamp trùng
    sender_id
    text
    timestamp
    reply_to_message_id
    mentions[]              // user_id đã resolve
    forwarded_from          // nullable
    edited_at               // nullable
    deleted                 // tombstone
    created_at
}

Media {
    id
    message_id
    position_in_message
    type
    object_uri
    capture_time            // nullable, chỉ khi còn metadata gốc
    created_at
}
Media không lưu topic_id. Context của media đến từ hai đường độc lập (mục 4 và 6).

4. Structural context
Đây là lớp deterministic, rẻ, không phụ thuộc model. Online dùng nó làm đường context dự phòng.

Quan hệ	Nguồn	Dùng cho
(conversation_id, seq)	Chat Core	Đọc cửa sổ ±k quanh một message
reply_to, reply chain	Message	Đi theo luồng trả lời
mentions	Message	Xác định người được nói tới
reacts_to_media	Suy ra: message sau ảnh, cùng conversation, nằm trong N message, reply hoặc mention tới người gửi ảnh	Context phía sau ảnh trong group chat
burst_id	Suy ra: các ảnh liên tiếp cùng sender, không có text chen giữa	Album
forwarded_from	Message	Nối ảnh gửi lại về nguồn
reacts_to_media là cạnh mềm. Nếu người gửi có nhiều ảnh gần nhau, cạnh được gắn vào ảnh gần nhất phía trước và lưu kèm khoảng cách.

Bảng alias người dùng (tên hiển thị, biệt danh, cách gọi khác) là dữ liệu danh bạ, được dùng cho việc resolve mention và cho bước phân tích query.

5. Episode memory
Episode là đơn vị semantic local của conversation: một discussion, event hoặc discourse goal tương đối coherent. Episode không đơn giản là "cùng chủ đề". Hai đoạn cùng nói về một chuyến đi có thể là hai Episode nếu thuộc hai phase khác nhau (chọn địa điểm, booking).

Episode không nhất thiết liền mạch trong message stream. Trong group chat, nhiều Episode có thể xen kẽ nhau.

Episode {
    id
    conversation_id
    title
    summary
    summary_embedding
    start_seq, end_seq
    start_time, end_time
    state                   // open | closed
    model_version, prompt_version
}

EpisodeMessage {
    episode_id
    message_id
    role                    // primary | secondary
    confidence
    status                  // provisional | committed
}
Quy tắc membership:

Một message có đúng một membership primary, và có thể có thêm membership secondary (ví dụ message chứa media, hoặc câu nói liên quan tới hai luồng).
Message không thuộc discussion nào ("ok", "haha", sticker) được đánh dấu unattached, không gán vào Episode, để không làm bẩn summary.
Biểu diễn của Episode gồm hai phần:

summary_embedding: phục vụ tìm theo ý chính.
Raw episode text: các message thành viên nối lại theo thứ tự, chia chunk, index lexical và dense. Summary sẽ làm mất chi tiết, còn raw text giữ nguyên, nên query chi tiết vẫn khớp được ở cấp Episode.
Summary và embedding chỉ phục vụ retrieval và memory matching. Chúng không thay thế raw message và không được trả làm evidence.

6. Episode assignment
6.1 Micro-batch và candidate
Message mới được xử lý theo micro-batch cho từng conversation. Episode Builder nhận:

batch message mới, kèm sender, timestamp, reply, mention, và việc message có media hay không;
một cửa sổ các message provisional gần đây;
một tập Episode candidate: các Episode đang open, Episode chứa message được reply hoặc mention tới, và vài Episode gần nhất về ngữ nghĩa trong cùng conversation.
LLM không đọc toàn bộ lịch sử. Candidate retrieval chỉ có nhiệm vụ bảo đảm Episode đúng có mặt trong context. Quyết định cuối cùng do Episode Builder đưa ra.

Model gán từng message vào một Episode hiện có, vào một Episode mới, hoặc đánh dấu unattached. Mỗi quyết định kèm confidence.

E7: checkout incident
E8: summer trip

m101: Redis vẫn ổn        → E7 (0.9)
m102: Đà Lạt thì sao?     → E8 (0.9)
m103: check pool size đi  → E7 (0.8)
m104: haha                → unattached
6.2 Provisional theo từng message
Streaming segmentation không biết chắc một message thuộc đâu khi context phía sau chưa tới. Trong group chat, các message chưa chắc chắn nằm rải rác chứ không chỉ ở cuối batch. Vì vậy trạng thái provisional áp dụng cho từng message, không theo vị trí:

Message có confidence dưới ngưỡng giữ trạng thái provisional, và được xét lại ở các batch sau trong một cửa sổ nhìn lùi LOOKBACK.
Message được committed khi confidence đạt ngưỡng, hoặc khi đã nằm ngoài cửa sổ nhìn lùi (khi đó commit theo quyết định tốt nhất hiện có).
Giới hạn số message provisional cho mỗi conversation (MAX_PROVISIONAL). Vượt giới hạn thì force-commit những message cũ nhất.
Idle timeout chỉ dùng để force-flush khi conversation thực sự dừng lâu. Khoảng lặng không được dùng làm quy tắc cứng cho ranh giới.
6.3 Message chứa media
Message chứa media là loại khó gán nhất: Episode Builder chỉ đọc text, text kèm ảnh thường cụt, và context thực sự nằm ở các phản hồi phía sau.

Message chứa media giữ provisional cho tới khi có đủ phản hồi (qua reacts_to_media, reply hoặc mention), hoặc hết cửa sổ nhìn lùi.
Khi đưa cho Builder, message chứa media được kèm các phản hồi đã có.
Cho phép membership secondary nếu phản hồi thuộc nhiều luồng.
Dù gán thế nào, context theo vị trí (mục 4) vẫn luôn dùng được lúc query.
6.4 Đóng Episode
Episode chuyển sang closed khi không nhận message mới trong một khoảng thời gian, hoặc khi Builder xác định discussion đã kết thúc.
Summary được viết lại khi Episode đóng và theo chu kỳ khi Episode đang open. Không viết lại sau mỗi message.
Episode đã đóng không được mở lại chỉ vì chủ đề quay lại sau nhiều tuần. Hệ thống tạo Episode mới, còn tính liên tục dài hạn do tầng Topic xử lý.
6.5 Sửa và xóa message
Message bị sửa: đánh dấu Episode chứa nó là stale, rebuild raw episode text, và viết lại summary ở lần cập nhật tiếp theo.
Message bị xóa: loại khỏi raw episode text ngay. Summary của Episode, và của Topic chứa Episode đó, phải được viết lại trong thời hạn quy định (mục 11).
7. Topic memory
Topic đại diện cho một ongoing situation, process, decision hoặc project kéo dài qua nhiều Episode.

Topic không phải một category rộng. "Travel" là quá rộng. "Sapa Trip", "Nha Trang Trip", "Da Lat Trip" có thể lại quá hẹp nếu cả ba chỉ là phương án trong cùng một quá trình chọn chuyến đi. Topic phù hợp trong trường hợp đó là "Summer Trip Planning".

Topic thuộc phạm vi một conversation. Không gộp Topic qua nhiều conversation.

Topic {
    id                      // bất biến
    conversation_id
    title                   // có thể đổi
    summary                 // có thể đổi
    keywords[]
    embedding
    last_active_at
    version
}

EpisodeTopic {
    episode_id
    topic_id
    role                    // primary | secondary
    confidence
}
Identity của Topic là id, luôn giữ cố định. Title, summary và keywords được phép đổi khi Topic phát triển. Mọi tham chiếu và cache đều dùng id.

Biểu diễn của Topic gồm summary, embedding và các Episode thành viên. Summary làm mất chi tiết, nên một chi tiết đã biến mất khỏi summary vẫn có thể được tìm thấy qua Episode cũ.

8. Tìm Topic candidate
Khi một Episode mới đủ ổn định (đã đóng, hoặc đủ lớn), hệ thống không so nó với toàn bộ Topic. Candidate được sinh theo hai đường song song:

New Episode
   ├── search Topics (title, summary, embedding, keywords)
   └── search historical Episodes (summary embedding + raw episode text)
              ↓
          parent Topics
              ↓
       Candidate Topic Set
Đường thứ hai xử lý trường hợp summary Topic đã bỏ mất chi tiết. Ví dụ Topic chỉ còn summary chung "chuyến đi Đà Lạt", nhưng raw text của một Episode cũ vẫn chứa "book Pine Hill". Khi Episode mới nhắc lại Pine Hill, đường này vẫn tìm ra đúng Topic.

9. Topic affiliation
Một LLM rẻ đóng vai trò semantic judge. Câu hỏi đặt ra không phải "Topic nào giống Episode này nhất" mà là: "Episode này có phải một stage, update hoặc return của cùng ongoing situation mà Topic đại diện không?"

Có bốn kết quả:

SAME_SITUATION: thêm quan hệ Episode–Topic, đánh dấu Topic cần consolidation. Episode cũ không bị viết lại.
REFINE_AND_ATTACH: Episode mới cho thấy abstraction của Topic quá hẹp (Topic "Sapa Trip" nhưng Episode mới bàn Nha Trang trong cùng một quá trình chọn). Cập nhật title và summary, giữ nguyên id, rồi attach. Đây là thay đổi nhẹ, chạy được trong online ingest.
NEW_TOPIC: tạo Topic mới.
UNCERTAIN: không ép phân loại. Episode giữ trạng thái chưa có primary Topic, có thể gắn một quan hệ secondary yếu, và chờ thêm evidence. Quyết định hoãn lại ít nguy hiểm hơn một phân loại sai.
Consolidation (viết lại summary, title, keywords) được gom theo lô cho mỗi Topic, không chạy sau mỗi lần attach.

10. Memory revision
Online ingest chỉ xử lý các thay đổi nhỏ: tạo Topic, attach Episode, refine identity, thêm quan hệ secondary.

Các thay đổi lớn (merge Topic, split Topic, chuyển Episode sang primary Topic khác, gán lại message giữa các Episode) do Revision Worker xử lý. Revision chỉ chạy khi có evidence cho thấy cấu trúc hiện tại có vấn đề. Evidence đến từ:

ingest mới;
query-time feedback (mục 20);
message bị sửa hoặc xóa.
Build → Use → Detect inconsistency → Revise → Reuse
Revision không bao giờ chạy trong user request, để tránh memory thay đổi mạnh chỉ vì một query đơn lẻ.

11. Quyền truy cập, sửa và xóa
Summary của Episode và Topic là bản sao dẫn xuất của nội dung message. Vì vậy:

Phạm vi: Episode và Topic kế thừa quyền truy cập của conversation. Mọi truy vấn memory đều filter theo scope của user ở tầng store.
Quy tắc lịch sử: nếu app không cho thành viên mới xem tin nhắn trước khi tham gia, summary không được dùng làm evidence hay hiển thị cho user đó. Summary chỉ được dùng để tìm kiếm. Evidence trả về luôn là message gốc đã được kiểm tra quyền.
Xóa: message bị xóa được loại khỏi raw index và raw episode text ngay. Summary chứa nội dung đó được viết lại trong thời hạn DELETE_PROPAGATION_SLA. Trước khi viết lại xong, summary bị đánh dấu stale và không được đưa cho LLM ở query time.
Sửa: giống xóa, nhưng không bắt buộc cùng thời hạn.
12. Image processing
V1 xử lý ảnh theo hướng nhẹ:

Image → Object Storage → Visual Embedding (SigLIP hoặc tương đương) → Visual Index
OCR, VLM và caption không chạy mặc định trên toàn bộ ảnh.

Ảnh có ba biểu diễn, được kết hợp lúc query:

Visual: embedding.
Structural: Image → Message → cửa sổ ±seq, reply, reacts_to_media, burst.
Semantic: Image → Message → Episode → Topic.
13. Raw retrieval
Raw message luôn được index độc lập bằng lexical retrieval (có trường không dấu cho tiếng Việt) và dense retrieval.

Lexical phù hợp với giá trị chính xác: chuỗi lỗi, ID, hostname, số, tên riêng.
Dense phù hợp với diễn đạt khác nghĩa tương đương.
Raw search không phụ thuộc trạng thái memory. Đây là đường chính khi Topic hoặc Episode bị mất chi tiết hay gán sai.

14. Query analysis
Query Analyzer không lập kế hoạch dài. Nó tạo ra một cấu trúc cố định:

{
  "target": "media | message | answer",
  "constraints": [
    {"id": "C1", "kind": "visual",  "text": "con chó"},
    {"id": "C2", "kind": "context", "text": "chuyến đi Đà Lạt"},
    {"id": "C3", "kind": "sender",  "value": "user_B", "hard": true},
    {"id": "C4", "kind": "time",    "range": ["2024-07-01", "2024-07-31"], "hard": false}
  ],
  "visual_query": "...",
  "semantic_rewrite": "...",
  "keyword_variants": ["Đà Lạt", "da lat"],
  "mode_hint": "precision | contextual | judgment | visual | hybrid"
}
Input gồm: danh bạ có alias, user hiện tại, thời điểm hiện tại và timezone.
Tên người được resolve sang id. Thời gian được resolve sang khoảng tuyệt đối.
Người gửi, người được nói tới, và người được mention là ba loại constraint khác nhau.
mode_hint chỉ ảnh hưởng tới trọng số và thứ tự ưu tiên. Nó không loại bỏ các kênh retrieval khác, nên phân loại sai mode không làm mất recall.

15. Vòng retrieval đầu tiên
Vòng 1 luôn chạy song song các kênh rẻ, bất kể mode_hint:

Kênh	Chạy trên	Khi nào
Raw lexical	Message	Có constraint context hoặc text
Raw dense	Message	Có constraint context
Episode search	Summary embedding và raw episode text	Có constraint context
Topic search	Title, summary, embedding	Có constraint context và mode_hint là contextual hoặc judgment
Visual	Media	Có constraint visual
Metadata	Media hoặc Message, chỉ filter	Có constraint hard (sender, time, conversation)
Mọi kênh đều áp scope quyền truy cập và các filter hard.

16. Tập hợp candidate
Chiếu sang đơn vị target. Khi target = media:

Message hit → media trong message đó, media trong cửa sổ ±seq, media được nối bằng reacts_to_media hoặc reply.
Episode hit → media của các message thành viên (primary và secondary).
Topic hit → media trong các Episode thành viên. Đây là scope, không phải kết quả.
Mỗi candidate lưu lại provenance: đến từ kênh nào, qua đường nào, cách bao xa.

Gộp vùng. Các hit cùng conversation nằm cách nhau không quá MERGE_GAP được gộp thành một vùng trước khi cắt top-K, để một đoạn chat không chiếm hết kết quả.

Fusion chạy trên đơn vị target (media_id hoặc message_id), không trộn nhiều loại đơn vị trong cùng một ranking. Với query có cả visual lẫn context, media có mặt ở cả kênh visual và kênh context được xếp trước. Lấy top-N cho bước tiếp theo.

17. Kiểm chứng context: hai đường
Với mỗi candidate trong top-N, evidence card được dựng từ cả hai đường:

Semantic: Episode chứa message (primary và secondary), summary Episode, title Topic. Chỉ dùng làm gợi ý.
Structural: message chứa media, cửa sổ ±seq (phía sau ảnh rộng hơn trong group chat), reacts_to_media, reply chain, burst.
Evidence dùng để kết luận luôn là message gốc. Nếu đường semantic và đường structural mâu thuẫn nhau, ví dụ Episode nói ảnh thuộc chuyện A nhưng các phản hồi quanh ảnh nói về chuyện B, thì đó là một revision signal (mục 20).

18. Evidence analysis và validator
Một VLM call nhận plan, các evidence card (kèm thumbnail ảnh), và trạng thái của các vòng trước. Output có cấu trúc:

Với mỗi candidate và mỗi constraint: supported, contradicted, hoặc unknown, kèm id của evidence.
Danh sách action đề xuất cho vòng sau.
Validator deterministic:

Mọi id được trích dẫn phải xuất hiện trong evidence của run này, còn tồn tại, và nằm trong scope.
Constraint hard bị contradicted thì loại candidate.
answered: có candidate (hoặc một burst) mà mọi constraint đều supported.
ambiguous: nhiều candidate cùng thỏa nhưng thuộc các context khác nhau.
insufficient: không candidate nào thỏa đủ. Chỉ lúc này mới chạy vòng tiếp theo.
Với precision query, một exact hit cùng context xung quanh đủ để kết thúc ngay ở vòng 1.

19. Retrieval Controller
Query không chạy qua một pipeline cố định Topic → Episode → Message. Sau vòng 1, controller thực thi các action do evidence analysis đề xuất, chọn từ một tập hữu hạn có type:

Action	Mô tả
SEARCH_MESSAGE(query, filters)	Query phải lấy từ evidence đã đọc (tên, alias, cụm từ), không paraphrase câu gốc
SEARCH_EPISODE(query, filters)
SEARCH_TOPIC(query)
SEARCH_VISUAL(visual_query, filters)	Đổi filter theo sender, time, conversation, scope
EXPAND_PARENT(id)	Message → Episode → Topic
EXPAND_CHILDREN(id)	Topic → Episode → Message/Media
READ_CONTEXT(message_id, before, after)	Đọc theo vị trí seq, không phụ thuộc Episode
FOLLOW_STRUCTURE(message_id, rel)	reply, reacts_to_media, forward, burst
RUN_OCR(media_id)	Lazy, có cache
RUN_VLM(media_id, question)	Lazy, có cache
STOP(reason)
Giới hạn và điều kiện dừng:

Evidence qua các vòng chỉ gộp thêm, dedupe theo id.
Action được chuẩn hóa và hash. Action trùng không chạy lại.
Dừng khi: đạt MAX_ROUNDS; một vòng không thêm id mới nào; hết budget LLM call hoặc hết thời gian. Khi dừng, trả kết quả tốt nhất cùng trạng thái từng constraint.
Đây là bounded retrieval loop, không phải một general research agent.

Top-down
Query → Topic → relevant Episodes → relevant Messages / Media
Level	Vai trò
Topic	Scope dài hạn
Episode	Anchor ngữ nghĩa cục bộ
Message	Evidence dạng text
Media	Evidence đa phương thức
Topic không phải nguồn trả lời, chỉ là scope.

Bottom-up
Message / Image → (structural context) → Episode → Topic → sibling Episodes
Nếu raw hit đã đủ trả lời thì không mở rộng. Bottom-up là một chế độ retrieval chính thức, không chỉ là phương án khẩn cấp. Bước kiểm chứng context khi đi bottom-up luôn dùng cả đường structural, vì message chứa ảnh có thể đã bị gán nhầm Episode.

20. Hybrid media retrieval
Với query có cả context lẫn visual:

Context retrieval (raw + Episode + Topic)
  → scope (các Episode và vùng message)
  → media trong scope (qua cả đường semantic và structural)
  → xếp hạng bằng visual
Nếu scoped search tìm được candidate thỏa mọi constraint thì trả kết quả.
Nếu không, chạy global visual search. Mỗi ảnh tìm được được kiểm chứng ngược lên qua cả hai đường ở mục 17.
Nhờ vậy Topic giúp tăng hiệu quả mà không trở thành hard gate làm giảm recall.
21. OCR và VLM
OCR và VLM chỉ chạy khi query cần thông tin mà visual embedding biểu diễn kém:

OCR: screenshot chứa chuỗi lỗi hoặc text giao diện.
VLM: biểu đồ, trạng thái UI, quan hệ trong ảnh, hoặc câu hỏi cần suy luận sâu trên nội dung ảnh. Bước evidence analysis (mục 18) cũng dùng VLM trên thumbnail của top-N.
Trước khi gọi, candidate set phải được thu hẹp xuống số lượng nhỏ. Kết quả được cache theo media_id, model version và câu hỏi.

22. Query-time feedback
Query có thể phát hiện memory có vấn đề. Ví dụ:

Topic-first thất bại, nhưng raw hoặc visual search tìm được evidence mạnh nằm trong một Topic khác.
Đường semantic và đường structural mâu thuẫn về context của một ảnh (mục 17).
Khi đó query engine tạo một revision signal gồm: entity hiện tại, Episode hoặc Topic đề xuất, evidence hỗ trợ, và confidence. User request vẫn được trả lời bằng evidence hiện có. Việc sửa memory chạy async sau đó.

23. Storage và indexing
V1 dùng PostgreSQL, pgvector và object storage.

PostgreSQL lưu:

Messages, Media
MessageEdges            // reply, mention, reacts_to_media, forward
MediaBursts
Episodes, EpisodeMessages
Topics, EpisodeTopics
MemoryDecisions
RevisionSignals
pgvector lưu embedding của message, raw episode chunk, summary Episode, Topic, và media.

PostgreSQL full-text search đủ cho prototype. Khi corpus lớn, lexical retrieval có thể chuyển sang OpenSearch hoặc Elasticsearch mà không đổi canonical data model. Mọi search index đều là derived state, rebuild được.

24. Async processing, versioning và chi phí
Memory processing chạy async qua queue hoặc outbox pattern. Worker phải idempotent để retry không tạo trùng membership hoặc trùng Topic.

Mọi artifact do AI tạo ra đều ghi model version và prompt version: embedding, Episode assignment, summary, Topic affiliation, revision.

Mọi quyết định memory quan trọng được ghi audit:

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
Kiểm soát chi phí:

Build lazy: conversation ít hoạt động chỉ build memory khi có query chạm tới, hoặc theo lịch thưa.
Micro-batch theo kích thước và thời gian, không xử lý từng message.
Summary viết lại khi Episode đóng hoặc theo chu kỳ. Consolidation Topic gom theo lô.
Theo dõi và đặt ngưỡng chấp nhận cho số LLM call trên 1000 message.
25. V1 boundary
V1 kiểm chứng một giả thuyết: precomputed Episode/Topic memory có giúp retrieval tốt hơn raw search cộng structural context hay không.

Ingest cần có:

Structural indexing.
Episode Builder với provisional theo từng message và xử lý riêng cho message chứa media.
Topic candidate retrieval, affiliation với các thao tác create / attach / refine.
Cơ chế lan truyền khi message bị xóa.
Query cần có:

Vòng 1 song song (raw, Episode, Topic, visual, metadata).
Kiểm chứng context qua hai đường.
Evidence analysis và validator.
Bounded retrieval controller.
Ảnh: visual embedding lúc ingest. OCR và VLM chạy lazy.

Chưa cần trong V1: Event Knowledge Graph, lớp atomic fact, Deep Research agent, OCR/VLM cho toàn bộ corpus, merge/split tự động mạnh. Chỉ thêm khi eval chỉ ra một lỗi cụ thể mà kiến trúc hiện tại không giải quyết được.

26. Evaluation
Evaluation tách chất lượng memory khỏi chất lượng câu trả lời cuối.

26.1 Gold data cần có
Theo turn: episode_id (hoặc thread_id) cho từng message, kể cả trong group chat có nhiều luồng xen kẽ.
Theo episode: topic_id, bao gồm các Topic kéo dài qua nhiều ngày.
Theo câu hỏi: answer_message_ids (phân biệt bắt buộc và hỗ trợ), answer_media_ids (cho phép rỗng), stratum.
Ảnh gây nhiễu ở nhiều mức: giống về hình nhưng khác context; giống về hình và context gần giống; nhiều ảnh giống nhau trong cùng một context.
Nếu dataset hiện có chưa có các nhãn này, bộ sinh data phải ghi thêm. Nhãn nên được tạo ra từ lúc sinh, không gán lại sau.

26.2 Cấu hình so sánh
Cấu hình	Mục đích
Raw + structural	Baseline
Gold structure (dùng Episode/Topic gold)	Mức lợi tối đa của memory
LLM-built memory	Mức lợi thực tế
LLM-built memory, bỏ đường structural khi kiểm chứng	Đo sự phụ thuộc vào đường dự phòng
Nếu khoảng cách giữa baseline và gold structure nhỏ, memory layer không đáng chi phí. Cấu hình này nên được chạy trước khi đầu tư vào Topic layer.

26.3 Metric
Ingest:

Episode assignment accuracy, tách riêng cho message chứa media và cho group chat.
Chất lượng ranh giới Episode; over-splitting và over-merging.
Topic affiliation accuracy; tỉ lệ UNCERTAIN; tỉ lệ message unattached.
Thời gian lan truyền khi message bị xóa.
Retrieval:

Message Recall@K, Media Recall@K, Episode Recall@K, Topic Recall@K. Đếm theo vùng hoặc theo đơn vị riêng biệt.
Topic-first success rate, flat fallback rate, bottom-up recovery rate.
Tỉ lệ ảnh đúng được cứu nhờ đường structural khi Episode gán sai.
Kết quả tách theo stratum: visual, context, visual + context, multi-hop, ảnh nằm xa đoạn bàn luận, chat hai người và chat nhóm.
Chi phí: LLM call trên 1000 message, số action trung bình trên mỗi query, số OCR/VLM call, latency, cost.

Metric cho memory revision: các query từng phải dùng bottom-up recovery có chuyển thành thành công trực tiếp qua Topic sau khi memory được sửa hay không. Nếu có, memory layer đang thực sự tích lũy giá trị chứ không chỉ tạo thêm tầng trừu tượng.

27. Tham số cấu hình (giá trị khởi đầu)
Tham số	Ý nghĩa
LOOKBACK	Số message gần nhất được xét lại khi gán Episode
MAX_PROVISIONAL	Số message provisional tối đa cho mỗi conversation
IDLE_FLUSH	Thời gian im lặng để force-flush
REACTION_WINDOW	Số message sau ảnh được xét cho reacts_to_media
MERGE_GAP	Khoảng cách tối đa để gộp hit thành vùng
N_ANALYZE	Số candidate đưa vào evidence analysis
CONTEXT_BEFORE / CONTEXT_AFTER	Cửa sổ ±seq khi đọc context
MAX_ROUNDS	Số vòng retrieval tối đa
DELETE_PROPAGATION_SLA	Thời hạn viết lại summary sau khi message bị xóa
Giá trị cụ thể được chọn qua eval.
