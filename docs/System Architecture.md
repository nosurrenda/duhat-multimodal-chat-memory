# System Architecture — Contextual Multimodal Retrieval for Chat

**v18 · 2026-09-23 · khớp `coordination/plans/PLAN.md` rev 61**

Tài liệu này mô tả kiến trúc đang xây. Nó **không phải hợp đồng** — hợp đồng là `PLAN.md`; chỗ nào hai bên lệch nhau thì `PLAN.md` đúng. Các bản v1, v2, v3 nằm ở `docs/archive/` và là lịch sử, không phải mô tả hiện tại.

Mọi con số trong tài liệu này đều đo trên `data/processed`, không ước lượng.

---

## §1. Phạm vi và nguyên tắc

Bài toán không phải "tìm ảnh" cũng không phải "tìm text". Nó là **chứng minh ảnh X thuộc ngữ cảnh Y**.

Một hệ thống trả về ảnh có visual score cao nhất mà không buộc nó phải gắn với đoạn hội thoại người dùng mô tả thì đã giải sai bài. Toàn bộ kiến trúc dưới đây xoay quanh chỗ đó: ảnh được **xếp hạng bên trong tập ngữ cảnh đã sống sót**, chứ không xếp hạng toàn cục rồi kiểm ngữ cảnh sau.

Bốn nguyên tắc không đổi:

1. **Raw data là source of truth.** Mọi index là derived state, rebuild được (§5).
2. **Structural relation dùng trực tiếp, LLM không tái suy luận** (§8).
3. **Scope giải quyết trước retrieval**, không phải lọc sau (§6).
4. **Mọi ranh giới cắt phải là ranh giới dữ liệu thật sự có**, không phải nhãn ai đó viết ra (§9).

Nguyên tắc 4 là cái đắt nhất phải học. Ba lần liên tiếp kiến trúc chọn một ranh giới rồi phải bỏ:

| Ranh giới | Vì sao bỏ |
| --- | --- |
| `Episode` / `Topic` do LLM suy ra | giá trị chưa từng được chứng minh; xây một lớp lớn trên câu hỏi còn mở |
| `session` của dataset | ranh giới chủ đề **do tác giả dataset viết ra** — dùng nó là lấy sẵn đáp án |
| `ngày` | **304/306 ngày chứa đúng 1 session**, toàn bộ 7.078 timestamp là `synthetic` dựng từ nhãn `timeline_date` theo session → ngày *chính là* session, chỉ đi vòng |

Cái còn lại là **conversation** — phân vùng duy nhất mà một ứng dụng chat thật cung cấp miễn phí và không ai bịa ra.

---

## §2. Thành phần hệ thống

```
Chat Core          lưu message, media, metadata gốc
                   thành phần DUY NHẤT nằm trên synchronous write path

Chunker            cắt message thành chunk (§9), build-time

Structural Indexer tính channel-local ordinal, cạnh mention / reacts_to(media)
                   index lexical + dense trên text chunk

Search Layer       BM25 (bm25s) · dense (bge-m3) · visual (SigLIP 2)

Query Engine       pipeline sáu bước của §11
```

V1 không tách microservice. Triển khai thật: một backend Go, một nhóm Python job, Postgres 16 + pgvector, MinIO.

---

## §3. Canonical data

Message và media phải được lưu **trước** mọi xử lý AI.

```
Message đến
  → persist raw (gán channel-local ordinal)
  → acknowledge
  → async: chunking + structural indexing + lexical/dense index
  → async: visual embedding
```

Hệ thống vẫn chạy khi LLM, embedding service hay worker chết. Message mới search được ngay khi index xong.

```
Message {
    message_id            ch:sess:turn_idx
    channel_id
    sender_id             canonical, có alias table đi kèm
    text
    timestamp             từ timeline_date thật, KHÔNG phải nguồn thứ tự
    timestamp_source      synthetic trên corpus này
    source_turn_index
    session_index
    source_session_id     provenance + eval ONLY, không bao giờ là retrieval primitive
    chronological_rank    global 0..7077 — KHÔNG phải seq (§4)
    reply_to_message_id   NULL toàn corpus
    thread_id             NULL toàn corpus
}

Media {
    media_id              không bao giờ merge; giữ đủ 1.265 logical row
    parent_message_id
    content_sha256        dedup vật lý
    storage_object_ref
    near_dup_cluster_id   chỉ để nhóm split + đào distractor, KHÔNG phải quan hệ identity
}
```

**`thread_id` để NULL là cố ý.** Production Slack/Discord có thread thật; corpus này không. Gán `thread_id = session_id` là **oracle leakage**: session là ranh giới chủ đề do dataset viết, nên thread expansion sẽ trao thẳng cho retriever cái phân đoạn ngữ cảnh mà sản phẩm sinh ra để tái dựng.

---

## §4. Structural context — và cái corpus này không có

Quan hệ deterministic lấy thẳng từ raw data, không qua model.

| Quan hệ | Nguồn kỳ vọng | H2HMEM |
| --- | --- | --- |
| `(conversation, seq)` | Chat Core | **chạy** — nhưng là **channel-local ordinal**, không phải `chronological_rank` |
| `reply_to` | `reply_to_message_id` | **0 / 7.078** |
| `mentions[]` | `Message.mentions[]` | một phần — 917 / 7.078 (13%), suy ra, non-authoritative |
| `forwarded_from` | field | **không có field** |
| `reacts_to_media` | reply/mention tới người gửi ảnh trong N message | yếu — 311 / 1.265 ảnh (25%) ở N = 5 |
| `burst_id` | ảnh liên tiếp, cùng người gửi, không text ở giữa | **2 cặp / 1.265 ảnh** |

Một trên sáu chạy được, một phần, bốn chết. Hệ quả phải mang theo mọi kết quả structural: **cấu trúc dùng được là chunk adjacency trong một conversation, cộng `mention` và `reacts_to(media)` làm filter input** — không gì khác. Kết quả structural yếu trên H2HMEM là bằng chứng về **corpus**, không phải về thiết kế.

**`chronological_rank` không phải `seq`.** Nó global trên `[0..7077]` và cả 25 channel đan vào nhau, khoảng hở tới **1.370** giữa hai message liên tiếp của cùng channel. Hai message cách nhau sáu bước trong hội thoại có thể cách hàng trăm bậc ở rank global. Channel-local ordinal — vị trí trong channel theo `(session_index, source_turn_index)` — mới là thứ `chunk_index` sinh ra từ đó.

---

## §5. Derived state

Mọi index rebuild được từ raw data. Không index nào là nguồn duy nhất tới một message hay media.

Ràng buộc kèm theo: **rebuild phải ra cùng bytes**. Với visual vector điều đó không tự nhiên đúng — PyTorch MPS ở `float16` cộng dồn không kết hợp được giữa các threadgroup, nên build lại trên Metal có thể lệch bit thấp. Vì vậy manifest ghi cả `device`:

```
visual_embeddings_manifest.json {
    model_name, model_version        pinned
    preprocessing                    image size, interpolation, normalization
    tensor_spec                      dimension, dtype, l2_normalized
    media_input_manifest_sha256      trên tập (media_id, content_sha256, storage_object_ref) đã sort
    matrix_sha256
    device                           build trên máy khác = artifact khác
}
```

Đã kiểm thực nghiệm: build CPU và build MPS cho ra **bytes khác nhau, cosine 1.000000, max abs diff 3.63e-06**. Đủ để xác nhận `device` thuộc về manifest.

---

## §6. Authorization

Mọi query mang `caller_id`. Membership là `(caller_id, channel_id, role, valid_from, valid_to)`. Danh tính thiếu, lạ hoặc hết hạn là **default-deny** — tập channel truy cập được rỗng, **không phải** search không lọc.

`ScopedRepository` là **đường duy nhất** tới message, media, embedding, cache VLM và provenance. Tra ID chính xác, đọc cache, đọc chunk/section, mở rộng, kết quả `SEARCH_AGAIN`, serialize provenance — tất cả đi qua nó. Truy cập không scope là **build failure**, chặn bằng architectural test.

**Scope bind bên trong executor, không bao giờ là tham số của stage.** Không lời gọi LLM nào nới rộng được nó.

**`channel_id` chỉ dùng nội bộ, KHÔNG BAO GIỜ đưa vào prompt/context cho LLM (D72, thêm 2026-09-23, cùng lúc với quyết định demo feed 1-luồng của Phase 2.7).** `channel_id` chỉ là scope filter, storage key, document boundary, và trace/eval field — không phải content field. Lý do: identifier dataset kiểu `dyadic_d19`/`multiparty_d5` mô tả cấu trúc corpus, không phải nội dung hội thoại — LLM thấy được có thể học pattern-match theo cấu trúc đó (benchmark shortcut) thay vì tìm kiếm thật. **Chỉ scope đúng `channel_id`, không phải anonymization chung** — sender, nội dung message, timestamp, parent-media anchor vẫn là context field hợp lệ, rule này không yêu cầu ẩn chúng. Áp dụng cho mọi phase lắp context cho LLM (stage [0]-[6], Phase 9's provenance UI).

---

## §7. Ingest path

Đồng bộ: persist raw, gán ordinal, ack. Mọi thứ còn lại async qua outbox — table-backed, `FOR UPDATE SKIP LOCKED`, lease/visibility timeout, idempotency key, retry có giới hạn, dead-letter. Không broker trong V1.

---

## §8. Structural dùng trực tiếp

Một **router** đứng trước pipeline, **ba nhánh**, quyết định là hàm thuần của constraint object mà `query_analyzer` phát ra:

| nhánh | điều kiện | làm gì |
| --- | --- | --- |
| structural / metadata | query thuần cấu trúc hoặc thuần metadata | đường nhanh deterministic |
| **visual** | **`context` rỗng** | SigLIP toàn corpus dưới filter `sender` / `time` / `media_type` → xếp hạng → trả. Hết. **Không qua [1]–[6]**, nên luật *"ứng viên chỉ từ section sống sót sau [4]"* của §18 **không áp dụng** ở đây |
| **context** | còn lại | sáu bước của §11 |

Không có nhánh thứ tư kiểu *visual trước rồi kiểm ngược ngữ cảnh*: query không nêu ngữ cảnh thì không có gì để kiểm.

Đây cũng là chỗ §8 được giữ: quan hệ structural được **dùng**, không phải nhờ LLM suy lại. `mention` và `reacts_to(media)` tiêu thụ ở đây, và **chỉ ở đây**.

---

## §9. Document và chunk (offline)

```
conversation                 document — đơn vị mà expansion không bao giờ rời
     25 document             ~44 chunk mỗi cái
        │
   section                   các chunk liền kề, tối đa 3, ghép lúc query
        │
   chunk                     <= 20 turn HOẶC <= 400 token, không overlap
     1.103 chunk             dyadic 357 tok / 5,3 turn
                             multi  369 tok / 17,8 turn
        │
   media                     key (conversation, day, filename)
                             index riêng: SigLIP vector, message_id, sender, timestamp
```

**Ngày là field, không bao giờ là ranh giới.** Nó lọc, nó phân tầng distractor, nó tham gia key media. Không có gì cắt theo nó.

### Text của chunk

```
[conversation: dyadic_d7 · 2024-09-05 · An, Bình]
An: mai mình đi Đà Lạt
Bình: [image:m_0412] đây là chỗ mình ở lần trước
An: nhìn ổn đấy
```

Ảnh viết inline dạng `[image:<media_id>]`. Đây là **nửa offline của bridge**: nó làm một tấm ảnh tìm được bằng chính đoạn text quanh nó, trong cùng một index với text — và không tốn gì.

Mỗi chunk lưu `chunk_id`, `chunk_index`, `message_ids[]`, `media_ids[]`, `day`.

### Vì sao 400 token

Hai đường cong ngược nhau, đo trên corpus thật:

| token | chunk | phủ kín 1 session bằng ±1 / ±2 / ±5 | chunk lẫn 2 session |
| --- | --- | --- | --- |
| 250 | 1.869 | 3% / **37%** / 98% | 11% |
| **400** | **1.103** | 34% / **79%** / **100%** | **21%** |
| 800 | 527 | 92% / 100% / 100% | **47%** |

Dưới 400 thì mở rộng không với tới: ở 250 một session trải 6 chunk, `±2` chỉ phủ 37%, gần như mọi query rơi xuống `±5` — bốn nấc hoạt động như hai.

Trên 400 thì đơn vị retrieval thôi là một chủ đề: ở 800, **47%** chunk vắt qua hai session, nhồi hai đề tài vào một embedding.

### Vì sao 20 message

Ở 400 token, cap 10 thôi làm rào mà thành người cắt chính cho group chat:

| cap | dyadic | multi | cap chạm (multi) | phủ ±2 |
| --- | --- | --- | --- | --- |
| 10 | 355 tok | **204 tok** | **174 / 174** | 72% |
| **20** | 357 tok | **369 tok** | 40 / 94 | **79%** |
| 25 | 357 tok | 373 tok | 3 / 93 | 78% |

Ở 10, chunk multi-party chỉ bằng **57% mật độ** dyadic — hai quần thể trong một index, đúng lỗi mà việc bỏ cắt-theo-số-turn sinh ra để tránh. Ở 20 hai bên bằng nhau. Từ 25 trở lên rào chết.

Kết quả: token cap gánh **96%** số lần cắt, message cap 4%.

---

## §10. Index

| Index | Engine | Đơn vị | Ghi chú |
| --- | --- | --- | --- |
| Lexical | **`bm25s`**, pin chặt | chunk | analyzer, tokenizer, `k1`, `b`, field weighting, tie order, score normalization đều ghi lại |
| Dense text | `bge-m3` | chunk | |
| Visual | `siglip2-base-patch16-384` | media | |
| Metadata | SQL | message | |

`bm25s` bị pin vì anti-leak gate của AC3 định nghĩa trên đúng hành vi top-1 của nó. Một BM25 viết bằng Go là **retriever khác**, y như Postgres FTS — FTS có thể làm candidate retriever nhãn `fts_candidate`, nhưng không bao giờ được gọi là BM25 và không bao giờ dùng cho gate hay baseline báo cáo.

**`meta` không bao giờ được index.** Loại trừ: `session_title`, `theme`, `meta.full_summary`, `meta.resolved_events`, `meta.active_events`, `generated_at`, `target_turns`. `meta.resolved_events` là **đáp án**: nó liệt kê đúng những sự kiện cross-modal mà câu hỏi hỏi.

Chứng minh bằng **canary, không phải bằng grep**: tiêm một token entropy cao khác nhau vào từng field cấm và một vào `message.text`; sau khi build cả ba index, mọi canary cấm phải trả **0 hit**, canary hợp lệ phải trả **đúng 1, hạng 1**. Positive control là thứ làm cho sự vắng mặt có nghĩa — thiếu nó, một indexer hỏng trả 0 cho tất cả và vẫn pass.

---

## §11. Online pipeline

Sáu bước, **thứ tự cố định trong code**, không phải lựa chọn của model.

```
[0]  sinh query + filter        LLM, song song
      │
      ├─ context RỖNG ──► SigLIP + filter → trả kết quả. HẾT.
      │                   (không section, không chọn, không mở rộng, không kiểm chứng)
      │
      └─ có context ───► tiếp [1]
      │
[1]  search + RRF + section     KHÔNG LLM · TEXT ONLY
      │
[2]  chọn <= 10 section         1 LLM call, nhìn tất cả cùng lúc
      │
[3]  mở rộng 0/1/2/3            1 LLM call mỗi section, song song
      │
[4]  gộp + giới hạn 30 chunk    KHÔNG LLM
      │
[6]  media: SigLIP trong tập    + VLM có điều kiện
      │
[5]  agent: trả lời | SEARCH_AGAIN
      └──────── SEARCH_AGAIN quay về [0], tối đa 6 cycle ───┘
                cycle cuối gỡ tool, buộc trả lời
```

**Số hiệu bước là nhãn, không phải thứ tự chạy.** Thứ tự thực thi là `[0] [1] [2] [3] [4] [6] [5]`. Bước [6] giải quyết media **trước** khi [5] quyết định, vì agent không thể chọn giữa trả lời và search lại khi chưa biết có tìm được ảnh chấp nhận được hay không — và đường loại của [6] được định nghĩa là quay về [5], chỉ mạch lạc theo thứ tự đó. Nhãn giữ nguyên theo bản thiết kế gốc để truy vết được.

Hình dạng này ăn tiền ở chỗ ít người nói tới: **[1]–[4] không có lựa chọn tự do**, nên các kiểu hỏng đếm được thay vì nổi lên bất ngờ, và chỗ duy nhất hệ thống có thể lặp là [5] — nơi bị chặn tường minh.

---

## §12. Bước [0] — sinh query và filter

Song song, một lần gọi:

- **1 query viết lại** theo lịch sử hội thoại
- **2–3 query keyword**, mỗi cái kèm bản không dấu
- **filter thời gian + phạm vi** do LLM suy ra — **chỉ được thu hẹp** filter người dùng đưa, không bao giờ nới. Đây là **test**, không phải câu dặn trong prompt.
- **1 visual query tiếng Anh** khi query có ràng buộc visual

Output theo JSON Schema enforced.

**Sweep candidate: `jev` (D63).** Output là JSON Schema cố định — kiểu có sẵn, đúng dạng bài của `jev`. Chưa dùng mặc định; đo độc lập trên corpus trước khi thay `gemini-3.5-flash-lite`.

---

## §13. Lexical index và tiếng Việt

Lexical index mang **field không dấu** — yêu cầu build cho sản phẩm tiếng Việt.

Trên corpus này nó **không đo được**: data là tiếng Anh với cặn CJK, zero tiếng Việt. Xây bây giờ và đo sau thì mạch lạc; bịa corpus tiếng Việt để đo bây giờ thì không.

---

## §14. Bước [1] — search và gộp section

Không LLM.

```
(chỉ chạy trên nhánh context — xem §8)

(3 nhánh text — query visual KHÔNG vào fusion, nó đi thẳng tới [6])

mỗi query text → top TOPK_CHUNKS (50) chunk
fusion         → weighted RRF
                   query viết lại  1.3
                   keyword         1.0   (nghiêng BM25)
                   câu gốc         0.5
gộp            → chunk liền kề cùng document → section
cắt            → MAX_SECTIONS, rồi token budget
                   mỗi section tối đa SECTION_MAX_CHUNKS (3) chunk
```

### Section merging

```
adjacency     = chunk_index liên tiếp trong MỘT document   # KHÔNG BAO GIỜ là retrieval rank
membership    = một dải tối đại các chunk liên tiếp được retrieve
cap           = 3 chunk, giữ các thành viên hạng cao nhất
center        = thành viên hạng cao nhất
section.rank  = rank của center                             # không trung bình, không cộng
section.media = hợp media của các thành viên
                mỗi media GIỮ parent_message_id riêng
```

**Media giữ neo điểm.** Section mang hợp media của các thành viên, nhưng mỗi `media_id` giữ `parent_message_id` của nó. Nâng neo lên mức section sẽ mất đúng cái độ phân giải mà Bridge Recall sinh ra để đo — và §18 xếp hạng đúng trên cái neo đó.

**Cap 3 chunk thay cho `MERGE_GAP`.** Lỗi region chạy loạn đo được trước đây (span 83 khi chain bắc cầu) giờ bất khả thi về cấu trúc: bắc cầu không có chỗ để chạy trong ba chunk.

---

## §15. Bước [2] — chọn section

Một LLM call nhìn **mọi section cùng lúc**, giữ tối đa 10. **Đây là bước rerank sau retrieval** — [1] xếp hạng thô bằng điểm số (RRF), [2] xếp lại bằng model hiểu nghĩa.

Prompt cố ý rộng tay: phân vân thì giữ, chỉ liên quan một phần cũng giữ. Bước loại thật nằm ở [3], nơi mỗi section được xét riêng với ngữ cảnh quanh nó.

**Sweep candidate: `jev` (D63).** Output của bước này là tập nhị phân giữ/bỏ — đã có kiểu sẵn, không phải văn bản tự do — nên đúng dạng bài `jev` (model "System One" trả giá trị có kiểu kèm độ tin cậy, không sinh câu). Chưa dùng mặc định; phải đo độc lập trên corpus trước, không tin số nhà sản xuất công bố.

**Cảnh báo về quy mô.** Với 1.103 chunk, giữ 50 section × 3 chunk là **14% toàn corpus** trong một prompt ~54.000 token. Ở tỉ lệ đó bước [2] không phải "chọn từ kết quả retrieval" mà là **đọc một phần bảy corpus**, và chất lượng bước [1] thôi quan sát được trong kết quả. Vì thế `MAX_SECTIONS` là knob **bắt buộc sweep** trên dev trước khi báo cáo bất kỳ giá trị nào là đã tinh chỉnh (§23).

---

## §16. Bước [3] — mở rộng

Một LLM call **mỗi section**, chạy song song. **Trên gemini**, input là section cộng `±2` chunk **đã prefetch sẵn**, output là một con số. **Trên nhánh jev, KHÔNG có prefetch trước vòng 1** (D69/D70/D71, sửa 2026-09-22 theo review Codex F-129) — vòng 1 chỉ có section, `±2` chỉ được fetch nếu vòng 1 escalate. Xem cơ chế đầy đủ bên dưới.

| | |
| --- | --- |
| **0** | không liên quan, **hoặc đúng chủ đề nhưng sai đối tượng** → bỏ |
| **1** | chỉ giữ section chính |
| **2** | giữ cả `±2` đã lấy |
| **3** | lấy thêm tới `±5` |

**Mức `0` mang thứ quan trọng nhất của cả dự án.** *Đúng chủ đề nhưng sai đối tượng* là kiểu hỏng mà một điểm similarity không diễn đạt được, và là lý do tồn tại của một bước rerank. Fixture conformance **phải chứa một ca như vậy** — một prompt gộp nó vào "không liên quan" nói chung vẫn sẽ đẹp trên mọi metric khác.

**`EXPAND_SECTION` không được phát một truy vấn index nào.** Trên gemini, input của nó prefetch theo thiết kế; trên jev, mỗi lần fetch thêm (vòng escalate) cũng chỉ đọc chunk lân cận theo chunk index, không phải semantic search — cả 2 nhánh đều test được trực tiếp, mạnh hơn và rẻ hơn việc chặn một semantic search vào vùng lân cận. Gọi vượt ra ngoài conversation trả `CROSS_DOCUMENT_EXPANSION_REJECTED`, ghi vào trace, không bao giờ âm thầm hạ cấp.

Token giao cho LLM ở mỗi mức: **1.075 / 2.509 / 4.660**.

**Sweep candidate: `jev` (D63).** Output là một trong bốn số nguyên — kiểu classification thuần, đúng dạng bài của `jev`. Bước này còn là **10 trong 14 lời gọi LLM** mỗi query — nếu `jev` đo được là đáng tin, đây là chỗ tiết kiệm lớn nhất. Rủi ro cần đo riêng: mức `0` mang discriminator *"đúng chủ đề, sai đối tượng"* — một phán đoán ở rìa của "phân loại có kiểu"; sweep phải kiểm `jev` có giữ được độ chính xác của discriminator này, không chỉ khớp baseline ở phần dễ.

**Trên baseline (gemini): [2] và [3] tách biệt, như D49 gốc.** [2] một lượt nhìn hết section, giữ ≤10; [3] một lượt/section giữ, chấm `0/1/2/3`.

**Trên nhánh jev: [2] và [3] GỘP thành một lượt/section (D66, xác nhận lại 2026-09-22).** Một bản trước của tài liệu này từng nói vậy mà **chưa được xác nhận** — Codex bắt được mâu thuẫn với `PLAN.md` (F-117), tôi rút lại, rồi người dùng xác nhận **đây đúng là hướng muốn làm**. D66 là bản ghi có xác nhận thật.

**Vì sao gộp không mất gì, CHỈ trên nhánh jev.** [2] trên baseline có giá trị thật: một lượt nhìn tất cả section, so sánh cái này với cái kia. jev **không làm được việc đó** — nó là model "System One", chấm từng input độc lập, không tự nhiên nhìn nhiều candidate cùng lúc theo kiểu so sánh chéo. Nên nếu dùng jev cho [2], nó *cũng* chỉ chấm từng section riêng lẻ — y hệt việc [3] đang làm. Gộp thành một lượt không mất khả năng so sánh nào, vì khả năng đó chưa từng có ở nhánh jev.

**Quyết định level KHÔNG phải 1 lượt đoán (D69, sửa 2026-09-22 theo câu hỏi trực tiếp của user).** Thiết kế gốc (D49, có từ trước jev) để model đoán thẳng `level 0..3` chỉ từ `±2` đã prefetch. Level 0/1/2 (kể cả one-sided) đều **grounded thật** — toàn bộ nội dung đã nằm trong `±2`. Chỉ riêng **level 3** là đoán mù — model chưa hề thấy chunk thứ 3-5 mà đã phải khẳng định có cần hay không. Trên gemini, việc này chấp nhận được vì per-call đắt nên không đủ tiền làm 2 lượt; trên jev, chi phí mỗi lượt rất rẻ nên sửa được.

**Cơ chế: 3 vòng, backend điều phối, không phải jev tự "reasoning" qua các vòng.** jev là model đọc-1-state, không có bộ nhớ giữa các lượt gọi — mỗi lượt luôn trả về **đủ 4 field cố định**, không có chuyện "chỉ tính field cần khi cần". **Backend mới là nơi quyết định giữ hay bỏ output của vòng nào.**

```
Vòng 1 — input: chỉ section
  jev trả: {level: 0|1|2, relevance_confidence, direction, target_attribution}
           (direction PHẢI = none nếu level 0/1, giữ nguyên rule D65)

  backend đọc level:
    0 → drop
    1 → NHẬN, final, size = section-only
        (backend giữ relevance_confidence + target_attribution của vòng này)
    2 → backend đọc direction vòng này và KHÓA LẠI (D70 — xem giải thích dưới),
        fetch ±2 theo hướng đó (both = 2 mỗi bên; one-sided = 4 một bên, theo budget D65 cũ)
        bỏ relevance_confidence/target_attribution vòng này, đẩy sang vòng 2

Vòng 2 — input: section + ±2 (theo hướng đã khóa)
  jev trả: cùng shape trên (vẫn có field direction, nhưng bị BỎ QUA nếu escalate — D70)
  backend:
    0 → drop
    1 → NHẬN, final, size = ±2
    2 → dùng ĐÚNG hướng đã khóa từ vòng 1 (không đổi),
        fetch thêm tới ±5 theo hướng đó (both = 5 mỗi bên; one-sided = 10 một bên),
        chỉ fetch phần CHƯA có từ vòng 1, đẩy sang vòng 3

Vòng 3 — input: section + ±5
  jev trả: cùng shape trên
  backend:
    0 → drop
    1 → NHẬN, final, size = ±5
    2 → DROP (insufficient_at_max_expansion) — hết ngân sách để mở rộng thêm,
        và một section mà chính jev vẫn thấy chưa đủ ở mức tối đa
        thì không đáng tin làm evidence, không ép nhận
```

Mỗi vòng là **một lượt gọi batch** trên toàn bộ section còn "sống" ở vòng đó (không phải 1 section = 1 call) — tối đa 3 batch tuần tự mỗi query, batch sau luôn nhỏ hơn vì đã lọc dần qua các vòng trước.

Cap `MAX_SELECTED_SECTIONS` (10) **vẫn ở [4]** như D66: lọc section có final `level>0`, xếp theo final `relevance_confidence`, cắt còn 10 — trước khi merge+cap 30 như cũ.

**Số lượng lời gọi thật không mặc định.** Mỗi vòng jev có thể chấm nhiều section trong một request (batch theo token budget). Chi phí đo được — tối đa 3 round-trip/query trên nhánh này thay vì 1, nhưng chấp nhận được nhờ per-call rẻ của jev, điều không đúng với gemini nên baseline giữ nguyên không đổi.

**Hướng bị KHÓA từ vòng đầu tiên quyết định, không đổi giữa các vòng (D70, sửa 2026-09-22 theo review Codex F-126, user chọn khóa thay vì định nghĩa rule evict/retain).** Một section chỉ có thể bắt đầu escalate ở **vòng 1** (vòng duy nhất mà việc "cần mở rộng" lần đầu xuất hiện) — hướng jev chọn ở vòng đó được khóa cho **mọi vòng sau**. jev vẫn trả `direction` ở mọi vòng (đúng rule full-schema), nhưng backend chỉ dùng giá trị của vòng khóa, các vòng sau bị bỏ qua (chỉ ghi lại để audit). Kết quả: mỗi vòng sau chỉ **mở rộng thêm cùng 1 hướng** — vòng 1's `±2` (both=2 mỗi bên, one-sided=4 một bên) nới ra vòng 3's `±5` (both=5 mỗi bên, one-sided=10 một bên) bằng cách fetch phần chênh lệch — **không bao giờ trộn 2 hướng, không bao giờ vượt max D65 (10 chunk một bên)**, và không cần rule "giữ hay hủy chunk cũ" vì chunk chỉ tăng dần, không bao giờ bị bỏ.

**Trace mới (D69, mở rộng D70 theo F-127/F-128, sửa tiếp D71 theo F-129/F-130/F-131):** mỗi section ghi `effective_level` (0-3, cùng ý nghĩa cả 2 nhánh — kích thước context cuối cùng) thay vì `level` (để không đè lên `level` 0-2 của từng vòng jev). `final_round` (1/2/3 — vòng nào ra quyết định cuối). `exclusion_reason` có thêm giá trị **`insufficient_at_max_expansion`** (drop ở vòng 3 vì vẫn `level 2`), phân biệt với `level_zero` (model thấy không liên quan) — hai lý do khác nhau, cần hướng sửa khác nhau. **`rounds[]` (D70, F-127; ràng buộc nội dung D71, F-130)** — chỉ nhánh jev — ghi lại TỪNG vòng đã chạy: `{round, level (0-2, raw output của jev vòng đó), direction (raw, kể cả vòng bị bỏ qua), transition, context_chunk_ids, fetched_chunk_ids}`, với ràng buộc thật: thứ tự theo document, không trùng, chỉ chunk của đúng conversation; `context_chunk_ids` vòng 1 = đúng chunk của section; vòng sau = context vòng trước ∪ fetched vòng trước; `fetched_chunk_ids` rỗng trừ khi `transition=escalate`, lúc đó đúng bằng phần chênh lệch chưa có; **vòng 3 không bao giờ escalate** — cho phép audit lại chính xác jev đã thấy gì ở mỗi vòng, không chỉ biết kết quả cuối.

**`direction` (terminal) tách khỏi `locked_direction` (lịch sử) — D71, sửa theo F-131.** Trước đây `direction` ở top-level chính là hướng đã khóa từ vòng 1 — nhưng nếu section escalate (khóa hướng "forward") rồi bị drop ở vòng sau, `effective_level=0` mà `direction=forward` thì **vi phạm chính rule D65** (direction phải "none" khi effective_level 0/1). Sửa: thêm field mới **`locked_direction`** (chỉ nhánh jev) — ghi lại hướng đã khóa như lịch sử thuần túy, không null khi và chỉ khi section từng escalate ít nhất 1 lần. Field `direction` (terminal) quay lại đúng rule D65 gốc: **luôn "none" khi `effective_level` 0 hoặc 1**, kể cả khi section đã từng escalate rồi bị bỏ — chỉ bằng `locked_direction` khi `effective_level` là 2 hoặc 3 (tức section được nhận với context đã mở rộng thật). **`direction` trên gemini luôn `null`** (D70, F-128) — không bao giờ ghi `"both"` như một giá trị output, vì gemini không hề chọn hướng, mở rộng đối xứng là tính chất thiết kế, không phải quyết định.

**`jev` cũng resolve target trong cùng lượt của bước [3] (D64, sửa 2026-09-22 — tổng quát hóa từ chỉ-media, rồi sửa schema theo review của Codex, F-118).** Chunk đã có sẵn trong context để chấm mức mở rộng — không tốn thêm lời gọi để hỏi thêm: *"ảnh này / claim này trong chunk là gì?"*, trả về kèm confidence.

```
target_attribution[]:
  anchor:      {type: media, media_id}  hoặc  {type: entity, message_id, span}
                                          (span = byte offset, KHÔNG phải quote hiển thị)
  resolution:  {relation: owned_by | said_by, sender_id}     — claim sở hữu/người nói
             | {relation: refers_to, message_id, media_id?}  — tham chiếu mơ hồ trỏ về đâu
             | {relation: unresolved}                        — dưới ngưỡng confidence
  confidence:  float
```

**Ban đầu đặt tên `media_attribution`, chỉ phủ ảnh — sai phạm vi** (F-116): một câu như *"nó bị hỏng rồi"* hay *"cái áo đó"* cần resolve WHO/WHAT y hệt một caption ảnh. **Rồi bản sửa đầu tiên vẫn giữ `attributed_sender` làm field duy nhất — sai cho chính ca entity vừa nêu** (F-118, Codex): "cái áo đó" không resolve ra một người gửi, nó resolve ra **tin nhắn nào định nghĩa cái áo đó là gì**. `resolution` giờ gắn nhãn `relation` để phân biệt hai loại kết quả khác nhau, và `unresolved` là một kết quả hợp lệ, không phải im lặng.

`submit_answer` (D64b) so **trực tiếp** claim trong câu trả lời với `resolution` của đúng `anchor` — không gọi thêm model nào để so sánh. Lệch thì từ chối; `unresolved` thì bỏ qua bước này, chỉ còn kiểm id-đã-thấy như cũ.

Khác D46 (`reacts_to(media)`): D46 deterministic, build-time, chỉ phủ trường hợp reply *sau* ảnh (đo được 25%); D64/D64b model-based, query-time, phủ cả caption *trước* ảnh và mọi tham chiếu text không có ảnh. Hai cái bổ sung nhau — chỗ nào D46 có, D46 đáng tin hơn.

**`jev` cũng quyết định HƯỚNG mở rộng, không chỉ mức độ — cùng lượt của bước [3] (D65, sửa 2026-09-22 theo review của Codex, F-119).**

```
level:      0..3           (như cũ)
direction:  none | both | forward | backward

level 0 hoặc 1  →  direction PHẢI là none (không có gì để phân hướng)
level 2, both      →  ±2 (như cũ, 2 mỗi bên)
level 2, một bên   →  4 chunk dồn một phía, 0 bên kia
level 3, both      →  ±5 (như cũ, 5 mỗi bên)
level 3, một bên   →  10 chunk dồn một phía, 0 bên kia

điểm gốc: forward tính từ chunk CUỐI của section; backward từ chunk ĐẦU
khử trùng: phần đã prefetch ±2 không bị lấy lại lần hai ở [4]
hết tài liệu: vượt biên document → cắt còn thực tế có, trace ghi
              truncated_by_document_boundary: true
```

Đo được: trong 132 câu xuyên session (D53), **81% (106/131)** nhắc session sớm trước, muộn sau — bằng chứng thật, nhưng **chỉ cho tầm xa xuyên session**, chưa đo được cho expansion trong-session thông thường (dữ liệu `gold_evidence_message_ids` ở mức chunk chưa tồn tại). **Chỉ áp cho nhánh jev** — khác D63, quyết định hướng không phải việc riêng jev làm được mà gemini không làm được, chỉ là nơi bằng chứng và công sức sweep của phiên này tập trung vào.

**Nhánh gộp cần hợp đồng xếp hạng xác định và quan sát được (D67, sửa 2026-09-22 theo review của Codex, F-123/F-124).** `relevance_confidence` là input xếp hạng mới, toàn cục — quyết định section nào sống sót qua cap `MAX_SELECTED_SECTIONS`. Không có ràng buộc, một ranking tệ vô hình: trông giống lỗi expansion, hoặc biến mất khỏi metric vì metric cũ (`stage [2] keep rate`, `selection miss`) mô tả một bước không còn tồn tại riêng trên nhánh này.

```
section_id (xác định):  {conversation_id}:{first_chunk_index}-{last_chunk_index}
                         suy ra từ output [1], KHÔNG bao giờ do model gán

relevance_confidence:   phải là số hữu hạn, trong [0,1]
tie-break (xác định):   rank section theo [1], rồi section_id
                         — hai lần chạy cùng input không bao giờ ra kết quả khác nhau

trace (mỗi section được chào, trước khi fetch phần còn lại của mức 3):
  {section_id, level, relevance_confidence, rank, exclusion_reason}
  exclusion_reason ∈ {null (giữ), level_zero (model chấm không liên quan),
                       selected_cap (liên quan nhưng thua ở top-10)}
```

`null`/`level_zero`/`selected_cap` là hai kiểu lỗi khác nhau, cần fix khác nhau, và không được gộp làm một.

**AC4 và error attribution branch-aware:** baseline gemini vẫn báo **stage [2] keep rate**; nhánh jev/D66 báo **level-zero rate**, **cap-exclusion rate**, và **selection recall@10** (tỉ lệ section có gold ở output [1] sống sót qua cap) — không bao giờ gộp chung số với baseline.

**Ablation dev-only đặt tên (F-124).** D66 đổi cả model lẫn topology cùng lúc — so sánh "gemini hai bước" với "jev gộp" chỉ cho biết cấu hình nào thắng, không tách được kết quả do `jev` hay do dời cap. Ablation chạy cùng candidate từ [1] qua cả hai cấu hình, báo song song: selection recall@10, cap-exclusion rate, phân bố level, metric answer/retrieval, chi phí. **Nói rõ: đo cấu hình trọn vẹn, không phải claim nhân quả jev-riêng-lẻ** — tách jev khỏi gộp cần một cấu hình thứ ba (jev trong khung hai bước) không nằm trong phạm vi đang xây.

---

## §17. Bước [4] — gộp và giới hạn

Gộp các section chồng lấn, giới hạn tổng output ở `MAX_OUTPUT_CHUNKS` (30).

---

## §18. Bước [6] — media và kiểm chứng

Đây là **bridge**.

```
(đường CONTEXT — đường visual không qua đây, xem §8)

ứng viên      = ảnh mang bởi các section sống sót sau [4], KHÔNG GÌ KHÁC
xếp hạng      = SigLIP, TRONG tập đó — không bao giờ toàn cục
                chấm mỗi ứng viên với QUERY VISUAL tiếng Anh sinh ở [0]
                → đó là nơi DUY NHẤT tiêu thụ query ấy trên đường context
VLM           = 3–8 ảnh + text của section
                chấm ràng buộc visual, trả xếp hạng cuối
                CÓ ĐIỀU KIỆN (dưới)
fallback      = VLM loại hết → SEARCH_AGAIN ở [5], hoặc trả rỗng
                KHÔNG BAO GIỜ tụt xuống visual toàn cục
```

Đường context không có fallback visual toàn cục. Nếu nó chạy, hệ thống sẽ trả ảnh chọn thuần theo hình thức, cho một query **có nêu** ràng buộc ngữ cảnh — đúng kiểu hỏng *đúng chủ đề, sai đối tượng*, đến từ chính fallback của mình. Query có ngữ cảnh mà không tìm ra thì nói không tìm ra, hoặc search lại.

Xếp hạng **bên trong** các section sống sót, thay vì toàn cục, chính là toàn bộ ý nghĩa của bridge: nó biến *ảnh thuộc ngữ cảnh này* thành **điều kiện tiên quyết** của việc xếp hạng, chứ không phải một phép kiểm tra dán vào sau.

### VLM chạy có điều kiện — điều kiện là CẤU TRÚC, không phải NLU

Chỉ chạy khi **section sống sót sau [4] có mang ảnh ứng viên** *và* **tra cứu ảnh→ảnh chưa giải quyết được** (D62). **Không còn dựa vào việc `query_analyzer` có đoán ra câu hỏi "nghe giống visual" hay không** — bản cũ (D52 tới rev 47) gán điều kiện theo cách diễn đạt câu hỏi, và một chunk kiểu `An: con chó nhà t này [image:m_412]` (ảnh nằm inline theo D44) sẽ bị trích dẫn thẳng từ caption mà **không ảnh nào được nhìn qua**, nếu câu hỏi không dùng từ ngữ nghe "visual" — đúng thứ VLM sinh ra để kiểm.

Đổi sang điều kiện cấu trúc thì chi phí không đổi ở chỗ quan trọng: câu hỏi thuần text (`context_only`, `metadata_only`, phần lớn `long_range`) có section không mang ảnh nào → vẫn không kích hoạt. Chỉ rộng ra đúng ở chỗ **đã có ảnh nằm trong context** — đúng chỗ cần verify.

Ba phép đo dựng nên phần "khi nào KHÔNG cần" (I→I đã giải quyết, hoặc không phải ảnh):

| | |
| --- | --- |
| câu hỏi về chart/graph/table | **79** trên 2.236 |
| câu có đáp án thuần số | **15 (0,7%)** |
| câu mang ảnh mà ảnh đó **đã có sẵn trong corpus** | **562 / 618 (91%)** → tra I→I |

Hơn 81% đáp án của câu hỏi ảnh nằm sẵn trong text hội thoại. Một bước VLM bắt buộc sẽ tính tiền mọi query cho một năng lực mà khoảng 3% cần tới.

Cache key là `media_id + model_version`, chế độ extract độc lập query (`{description, visible_text, chart_trend, numbers}`). Gọi có điều kiện theo query sẽ phá chính cái key đó.

---

## §19. Bước [5] — agent và `SEARCH_AGAIN`

Đọc output của [4], rồi hoặc trả lời, hoặc gọi `SEARCH_AGAIN` với keyword mới.

- Thấy **mọi query đã chạy**, kể cả query được mở rộng
- **Không giữ nội dung tool response giữa các lượt** — đây là thứ giữ chi phí tuyến tính theo cycle thay vì bậc hai
- Tối đa `MAX_CYCLES` (6); **cycle cuối gỡ tool và buộc trả lời**
- `SEARCH_AGAIN` đòi một tập keyword **chưa phát trong run này**; lặp lại một query đã phát bị từ chối chứ không phục vụ từ cache, để bộ đếm cycle không tiêu vào no-op

Không multi-agent, không agent framework.

### `submit_answer` validator

Từ chối submission khi: id được trích dẫn chưa từng xuất hiện trong kết quả stage nào của **chính run đó**; media nằm ngoài scope; evidence không phủ hết ràng buộc đã nêu.

**Validator chỉ chặn id bịa** — ở nhánh model không phải jev. Nó chứng minh một id đã được thấy, không bao giờ chứng minh được media đó thỏa ràng buộc ngữ cảnh. Tính đúng của bridge đo bằng **Bridge Recall**, và không kết quả validator nào thay thế được.

**Ở nhánh jev (D64b, sửa theo F-118), validator so trực tiếp:** claim trong câu trả lời về một `anchor` (media hoặc entity) đối chiếu với `resolution` mà `target_attribution` đã ghi cho đúng `anchor` đó — lệch thì từ chối. `resolution.relation = unresolved` thì bước này không chạy, chỉ còn kiểm id-đã-thấy như thường. Không gọi thêm model nào để so sánh — đây là phép so khớp field trực tiếp. Đây vẫn không phải chứng minh tuyệt đối — bản thân attribution là một claim có confidence score, không phải ground truth — nhưng nó thu hẹp đúng khoảng trống caption-trước-ảnh (và caption-trước-claim) đã nêu ở §19.

---

## §20. Trace

Trace là **input của eval harness**, không phải log gỡ lỗi. Nhiều metric chỉ tính được từ nó, nên nó phải tồn tại **trước khi bất kỳ stage runtime nào được viết**, không phải sau. (Không còn controller nào để nói "trước controller" — D14 đã bỏ Config D; runtime là pipeline cố định của §11, phần duy nhất còn quyết định là câu hỏi *trả lời hay `SEARCH_AGAIN`* ở bước [5].)

Action set đóng, chín cái:

```
[1]  SEARCH_CHUNK_LEXICAL · SEARCH_CHUNK_DENSE · FILTER_METADATA   (TEXT ONLY)
     SEARCH_VISUAL        (nhánh visual và bước [6] — KHÔNG BAO GIỜ ở [1])
[2]  SELECT_SECTIONS
[3]  EXPAND_SECTION       (bắt buộc có level + document key)
[5]  SEARCH_AGAIN         (mang new_keywords, queries_already_run)
[6]  RUN_VLM
     STOP
```

Sự kiện chung: `retrieval_step { tool, args, returned_ids }`.
Mức cycle: `cycle`, `stage`, `section_ids`, `expand_level`, `new_keywords`, `queries_already_run`, `forced_answer`.
Mức kết quả: `media_id`, `parent_message_id`, `visual_score`, `context_message_ids`, `structural_relations`, `supported_constraints`.
Mỗi lời gọi LLM: role, dated model id, provider đã resolve, prompt version, tokens, cost, latency.

**Sự kiện thứ hai: `section_judgment` (D67, F-125; mở rộng D69; level domain và round history sửa D70 theo F-126/F-127/F-128; ràng buộc nội dung và terminal direction sửa D71 theo F-129/F-130/F-131).** `TraceRecord` có `extra="forbid"` — nghĩa là dữ liệu per-section của D65/D67/D69 không có chỗ nào hợp lệ trong trace cho tới khi được đặt tên như một event riêng, không phải mô tả bằng lời rồi để implementation tự log riêng (không verify được).

```
section_judgment {
  section_id,                      (xác định: {conversation_id}:{first_chunk_index}-{last_chunk_index})
  stage1_rank,                     (rank từ output [1] — input cho tie-break D67)
  effective_level,                 (0..3, D70 — kích thước context CUỐI CÙNG, ý nghĩa giống nhau cả 2 nhánh:
                                     gemini giữ nguyên one-shot cũ; jev suy ra từ vòng nào NHẬN (1→section,
                                     2→±2, 3→±5), hoặc 0 nếu bị drop)
  relevance_confidence,            (finite, [0,1] — CHỈ nhánh jev/D66, gemini không có field này)
  direction,                       (none|both|forward|backward|null — TERMINAL, mô tả context được NHẬN, không
                                     phải lịch sử. LUÔN "none" khi effective_level là 0 hoặc 1 — kể cả section
                                     từng escalate rồi bị drop (D71, F-131). Chỉ bằng locked_direction khi
                                     effective_level là 2 hoặc 3. Gemini: LUÔN null — không hề chọn hướng)
  locked_direction,                (none|both|forward|backward|null — D71, F-131. CHỈ nhánh jev: hướng đã khóa
                                     từ vòng 1 (D70, F-126) như LỊCH SỬ thuần túy, không phụ thuộc kết quả cuối.
                                     null khi và chỉ khi section chưa từng escalate)
  exclusion_reason,                (null | level_zero | selected_cap | insufficient_at_max_expansion (D69)
                                     — nhánh jev. Gemini: null | level_zero thôi, không có cap post-hoc
                                     vì MAX_SELECTED_SECTIONS lọc ở [2] TRƯỚC KHI có effective_level)
  final_round,                     (1|2|3 — CHỈ nhánh jev, D69. Gemini single-round, không có field này)
  post_cap_rank,                   (rank sau khi xếp theo relevance_confidence ở [4], null nếu bị loại trước cap)
  truncated_by_document_boundary,  (bool, D65 — chỉ có nghĩa khi effective_level > 1 và expansion thật sự chạy)
  rounds: [                        (D70, F-127; ràng buộc nội dung D71, F-130 — CHỈ nhánh jev; gemini không có,
                                     vì single-shot không có gì để tái tạo, và vòng 1 của gemini KHÔNG phải
                                     "section-only" như jev — đó là input ±2 đã prefetch sẵn, D49, F-129.
                                     1-3 phần tử, mỗi vòng đã thực sự chạy:)
    { round,                       (1 | 2 | 3)
      level,                       (0 | 1 | 2 — raw output của jev vòng đó, KHÁC effective_level)
      direction,                   (raw output jev vòng đó, kể cả vòng bị khóa-bỏ-qua — giữ để audit)
      transition,                  (drop | accept | escalate)
      context_chunk_ids,           (thứ tự document, không trùng, chỉ chunk của đúng conversation. Vòng 1 =
                                     ĐÚNG chunk của section. Vòng sau = context vòng trước ∪ fetched vòng trước)
      fetched_chunk_ids,           (thứ tự document, không trùng. RỖNG trừ khi transition=escalate — lúc đó
                                     ĐÚNG BẰNG phần chênh lệch chưa có tới ngân sách vòng sau, trừ khi cắt bởi
                                     document boundary. Vòng 3 KHÔNG BAO GIỜ escalate → luôn rỗng)
    }, ...
  ],
}
```

**Cardinality khác nhau theo nhánh** (khớp đúng phạm vi khác nhau của D66): **jev** — đúng 1 `section_judgment` cho **mọi** section từ [1] (D66: "mọi section, không phải 10 cái lọc trước"). **Gemini** — đúng 1 `section_judgment` cho mỗi section **sống sót qua [2]** và vào [3] — section bị [2] loại không bao giờ có `effective_level` nên không có event. `relevance_confidence`/`final_round`/`locked_direction`/`rounds[]` vắng mặt trên gemini vì không áp dụng, không phải bị bỏ sót.

**Validation:** `relevance_confidence` khi có phải finite và trong `[0,1]` (F-123); `effective_level` phải trong `{0,1,2,3}`; `direction` phải là `none`/`null` bất cứ khi nào `effective_level` là 0 hoặc 1, cả 2 nhánh. Trên jev: `rounds[]` phải có 1-3 phần tử, `round` tăng dần liên tục; `context_chunk_ids`/`fetched_chunk_ids` phải khớp đúng rule union/delta ở trên; phần tử cuối `transition` phải là `drop` hoặc `accept`, không bao giờ `escalate`; `final_round` = `round` của phần tử cuối; `effective_level` = `round` đó nếu `transition=accept`, ngược lại 0; `locked_direction` khác null khi và chỉ khi có phần tử nào đó `transition=escalate` — sai bất kỳ điều nào là lỗi validate schema/conformance cứng, không phải clamp. **Viewer/evaluator đọc trực tiếp event này** để tính `effective_level` distribution của [3], các metric selection/exclusion của cả 2 nhánh (level-zero rate, cap-exclusion rate, expansion-insufficient rate D69), và selection recall@10 — không qua log riêng nào khác. Trên nhánh jev, `rounds[]` còn cho phép verify constraint "không phát index query nào trong expansion" theo từng vòng, và quy trách nhiệm 1 kết quả sai về đúng vòng/quyết định gây ra nó.

**Enum phải có required field theo từng action**, không chỉ allowlist. Một allowlist không đòi gì là lỗ hổng đã từng để lọt.

---

## §21. Stack

```
RUNTIME — Go ────────────────────────────────────
  API · pipeline · scope · trace
  storage · outbox · pgvector query
       │
       └─ local RPC ──► Python retrieval service
                          bm25s search · bge-m3 / siglip2 encode

BUILD — Python ──────────────────────────────────
  normalization · chunking · embedding · index construction
       │
       └─ file có checksum ──► Go đọc lúc runtime
```

**Ranh giới là runtime/build, không phải Go/Python.** Go được chọn vì **người duy trì thạo Go hơn** — một quyết định về ai nuôi code này, và nó đứng trên mọi tính chất của hai ngôn ngữ.

Ba thứ không sang Go: `bm25s` bị pin bởi anti-leak gate; `bge-m3` và `siglip2` là model PyTorch, export ONNX sẽ đổi numerics và phá `matrix_sha256`. Cái giá là một RPC local trên đường retrieval, cỡ một millisecond — không đáng kể.

**`config` và `manifest` là chỗ duy nhất hai bên cài cùng một thứ**, và đó là rủi ro cross-language thật: `config_hash` và mọi checksum artifact phải **trùng nhau**, nếu không một manifest Python ghi ra sẽ không verify được ở Go — âm thầm, đúng chỗ các gate sinh ra để bắt giả mạo. Giữ bằng một **cross-language conformance test**: cùng input, hai bên hash, assert bằng nhau.

Canonical form phải chốt: float serialize thành **string** trước khi JSON hóa (`format(v, ".12g")`), key sort, separator không khoảng trắng, `ensure_ascii`. Rủi ro Go còn lại là `SetEscapeHTML(false)` và escape `\uXXXX`.

---

## §22. Ngoài phạm vi V1

Caption toàn corpus, knowledge graph, phân cấp topic/episode precompute, research agent tổng quát, modality video/voice/document.

**Context card không nằm trong V1.** Trace và media schema chỉ cần không cản việc thêm nó sau.

---

## §23. Config knobs

Mọi knob hash vào `config_hash`, không bao giờ là literal trong code, giá trị khởi đầu chọn bằng đánh giá chứ không khẳng định.

| Knob | Giá trị | Ghi chú |
| --- | --- | --- |
| `CHUNK_MAX_MESSAGES` | **20** | rào sống — chạm 40/94 lần cắt multi-party, 4% tổng |
| `CHUNK_MAX_TOKENS` | **400** | cap quyết định, 96% số lần cắt |
| `TOPK_CHUNKS` | 50 | **sweep {20, 50}** trên dev |
| `MAX_SECTIONS` | 50 | **sweep {15, 30, 50}** trên dev — bắt buộc, xem §15 |
| `SECTION_MAX_CHUNKS` | 3 | |
| `RRF_W_REWRITE / KEYWORD / ORIGINAL` | 1.3 / 1.0 / 0.5 | nhánh rewrite không đo được trên corpus này (§26) |
| `MAX_SELECTED_SECTIONS` | 10 | |
| `EXPAND_LEVELS` | `{0,1,2,3}` → `{bỏ, section, ±2, ±5}` | |
| `MAX_OUTPUT_CHUNKS` | 30 | |
| `MAX_CYCLES` | 6 | cycle cuối gỡ tool |
| `RUN_VLM` | có điều kiện | §18 |
| `REACTION_WINDOW`, `N_ANALYZE`, `DELETE_PROPAGATION_SLA` | — | |

Các giá trị này là **của Onyx, lấy làm điểm khởi đầu trên một corpus nhỏ hơn mục tiêu của Onyx ba bậc độ lớn**. Đó chính xác là lý do hai knob co giãn theo cỡ corpus bị bắt sweep thay vì báo cáo như đã tinh chỉnh.

---

## §24. Counters và chi phí

Đếm mỗi query: `LLM calls`, `cost`, `tool calls`, `cycles`, latency p50/p95.

Đo được ở `MAX_SECTIONS` 50:

| | |
| --- | --- |
| LLM call / query | **14** ở 1 cycle · **79** ở worst case 6 cycle |
| input token / query | **~91.000** |
| — riêng bước [2] | **54.300** |
| — riêng bước [3] | 25.300 (nhưng là 10/14 số call) |
| 2.236 câu, 1 cycle | ~204M token → **\$20** flash-lite / **\$61** flash |
| ở `MAX_SECTIONS` 15 | 53.000 token/query → **\$12** |

Độ chính xác **luôn báo cáo cạnh cost/query và LLM calls/query**. Thắng bằng ngân sách gấp ba không phải cùng một kết quả với thắng ở chi phí ngang nhau.

---

## §25. Failure modes

Mỗi query hỏng phân loại từ trace:

| Loại | Nghĩa |
| --- | --- |
| **retrieval miss** | gold media chưa từng xuất hiện ở kết quả stage nào |
| **selection miss** | xuất hiện ở [1], bước [2] bỏ |
| **expansion miss** | section được giữ nhưng gold evidence nằm ngoài mức đã chọn |
| **context miss** | gold media xuất hiện nhưng gold evidence của nó thì không |
| **reasoning miss** | cả hai xuất hiện, câu trả lời vẫn trượt AC13 |

Loại cuối chỉ **thật sự đo được từ rev 36**: trước khi chấm câu trả lời, "vẫn trả lời sai" không quan sát được, nên mọi reasoning miss bị hòa lặng vào metric retrieval nào đó chưa hoàn hảo.

Hai loại giữa là mới: pipeline thêm đúng hai chỗ để đánh mất một ứng viên đúng mà thiết kế trước không có.

---

## §26. Corpus này chứng minh được gì

Phần này tồn tại để một người đọc sau biết chiết khấu các con số cho đúng.

### Ba tính chất làm kết quả đẹp hơn thực tế

| | |
| --- | --- |
| **một chủ đề mỗi ngày, một session mỗi ngày** — 304/306 | mọi kết quả hưởng lợi từ locality theo ngày đều lạc quan |
| **không có giờ trong ngày** | khoảng lặng, "tối qua", "lúc nãy" không có chỗ bám |
| **mọi ranh giới chunk rơi trong một session** | chat thật đan chủ đề trong cùng một đoạn; corpus này không bao giờ |

Tính chất thứ ba nặng hơn khi document là conversation: một section ba chunk liền kề ở đây **được bảo đảm nhất quán chủ đề** vì một lý do production sẽ không cung cấp.

### Hai thứ xây nhưng không đo được

- **Nhánh RRF "query viết lại"**: mọi câu hỏi H2HMEM là single-turn, schema không có field lịch sử. Nhánh này trả về đúng câu gốc — nên trọng số cao nhất (1.3) đang áp lên cùng văn bản mà trọng số thấp nhất (0.5) áp lên. Không ablate được.
- **Field không dấu**: corpus tiếng Anh (§13).

### `SEARCH_AGAIN` đo được trên bao nhiêu

**0 / 2.236** câu có gold trải hai dialogue. **0 / 2.236** trải hai session. Nên **không câu nào buộc phải rời khỏi một document** — `SEARCH_AGAIN` không đo được như multi-hop xuyên document, và mọi tuyên bố ngược lại đều sai.

Cái đo được là **tầm xa trong cùng một document**: 133 dòng `session0` có gold xuyên session khôi phục được từ `validation_notes`, **trừ 1 dòng bỏ đi** (đáp án chỉ là `"Session 16"`) → **132**. Nhưng ở cỡ chunk của §9, mở rộng với tới một phần:

| | |
| --- | --- |
| cách 1 session — median 3,5 chunk, trong tầm `±5` | **63 (47%)** → expansion giải quyết |
| cách >= 2 session — 7+ chunk, ngoài tầm | **69 (52%)** → thật sự cần `SEARCH_AGAIN` |

**Cơ sở đo của bước [5] là 69 câu, không phải 132.** 63 câu kia là kết quả của expansion và không được ghi công cho nó. Hai con số báo cáo riêng.

Đây là **thu hẹp so với luận điểm ban đầu của dự án** và được ghi nhận như vậy: Search Jump từng được định nghĩa là một retrieval mới trên toàn corpus xuất phát từ manh mối mới; cái còn lại ở đây là một retrieval thứ hai trong cùng document.

### Trần của công cụ đo

Corpus có **25 conversation**, và split nhóm theo dialogue nên các dòng trong một dialogue phụ thuộc nhau. Thiết kế paired trên 25 cluster phân giải được hiệu ứng **4 điểm** ở mức 95%, **không** phân giải nổi 2 điểm. Sinh thêm query từ cùng 25 dialogue mua thêm **coverage**, không mua thêm bằng chứng độc lập.

`interleaved` và `referential` chỉ tồn tại ở 5 channel multi-party: ở đó thiết kế paired đạt 4% ở 4pp và **54% ở 8pp**. Mọi ngưỡng trên hai tầng đó báo cáo **bất đối xứng** — vượt là bằng chứng, trượt thì không kết luận gì.

---

## §26b. Chấm câu trả lời

Hệ thống emit câu trả lời, nên câu trả lời được chấm — không chỉ chấm retrieval.

`gold_answer` nằm trong row schema, lấy từ `original_answer` của H2HMEM (Tier A), từ template (Tier B), hoặc do Antigravity viết (Tier C). Ngữ nghĩa theo `expected_action`: `return_result` mang câu trả lời và **được chấm**; `clarify` để rỗng vì đúng nghĩa là *hỏi lại* chứ không phải trả lời; `no_result` mang câu từ chối nhưng **không chấm ở đây** — AC11 đã đo hành vi từ chối, chấm lại là đếm hai lần.

**Mẫu số là 909 / 1.039 dòng**, và luôn ghi kèm con số đó.

### Hai lượt, không phải một judge

| | |
| --- | --- |
| **lượt 1 — deterministic** | khớp chính xác đã chuẩn hóa (fold hoa thường, dấu câu, mạo từ) |
| **lượt 2 — LLM judge** | chỉ chạy trên phần lượt 1 không quyết được |

Vì câu trả lời phần lớn ngắn: median **4 từ**, **59% ≤ 5 từ**, chỉ **20% quá 15 từ** (dài nhất 155). Đưa một câu trả lời một từ cho LLM judge thì mua thêm phương sai chứ không mua thêm tín hiệu.

### Judge phải khác họ model

Judge chạy **`z-ai/glm-5.3`**, khác họ với `google/gemini-3.5-flash-lite` của runtime. Cùng lý do `labelling_model` tồn tại: một judge cùng họ với thứ sinh ra câu trả lời sẽ chấm rộng tay cho chính cách diễn đạt của nó, và không phép kiểm nào phía sau phát hiện được.

### Judge phải qua cửa trước khi metric được dùng

150 dòng lấy mẫu phân tầng, chấm tay, **Cohen's κ ≥ 0,75**. Dưới ngưỡng thì metric báo cáo kèm κ và **bị loại khỏi mọi so sánh headline**. Một judge chưa kiểm định là một ý kiến gắn con số.

---

## §27. Baseline

Kiến trúc so với sàn, không so với một kiến trúc thứ hai.

| | |
| --- | --- |
| **B0** | visual-only |
| **B1** | text-only hybrid |
| **B2** | fusion điểm độc lập |
| **B3** | B2 + temporal expansion |
| **B4** | **retrieve-and-stuff** — hybrid → top `N ∈ {3,5,10}` section → ghép nguyên văn → **một LLM call** |

**B4 là baseline quyết định.** B0–B3 đều là *cấu hình retrieval*: chúng so retriever với nhau và không trả lời được câu hỏi liệu pipeline sáu bước có xứng với hình dạng của nó.

Con số làm B4 đáng sợ: session lớn nhất **2.602 token** nên cả một session luôn lọt context; **81%+** đáp án câu hỏi ảnh nằm sẵn trong text mà gold trỏ tới; và `bm25s` mức session đã đạt **75% ở distinct@5, 89,6% ở distinct@10**.

**Trần ~90% bằng một lời gọi LLM là con số mà 14 lời gọi của pipeline phải vượt.** Học điều đó sau khi đã xây xong thì quá muộn — nên B4 xây **trước**.
