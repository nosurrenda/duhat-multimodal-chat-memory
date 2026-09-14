# **Contextual Multimodal Retrieval for Chat Applications**

## **1. Vấn đề**

Sau một thời gian sử dụng, một ứng dụng chat có thể chứa hàng trăm nghìn tin nhắn cùng rất nhiều ảnh, screenshot, video, voice và tài liệu. Text vẫn tương đối dễ tìm bằng keyword hoặc semantic search, nhưng media khó hơn vì ý nghĩa của nó thường không nằm hoàn toàn trong chính file đó.

Một người có thể gửi vài tấm ảnh chỉ kèm câu:

> “ảnh hôm đấy đây”

Nhìn riêng từng ảnh, model có thể nhận ra chó, ô tô, căn hộ hay đồ ăn. Nhưng nó không biết “hôm đấy” là hôm nào, ảnh thuộc sự kiện nào, ai đang được nhắc tới hay tại sao ảnh được gửi.

Ngược lại, text search có thể tìm được đoạn mọi người bàn về một chuyến đi, một lần build PC hay một căn hộ đang cân nhắc, nhưng lại không biết ảnh nào trong lịch sử thực sự thuộc context đó.

Người dùng thường cũng không nhớ filename, timestamp hay caption chính xác. Họ chỉ nhớ một vài đặc điểm:

> “Ảnh con chó hồi đi Đà Lạt đâu?”
>
> “Tìm ảnh con 3060 hôm build máy.”
>
> “Ảnh căn hộ có ban công mình xem tháng trước.”
>
> “Screenshot B gửi lúc team debug deploy.”

Bài toán vì vậy không đơn giản là image search hay text search. Hệ thống phải hiểu đồng thời:

**media chứa gì** và **media đó có ý nghĩa gì trong cuộc trò chuyện**.

## **2. Ý tưởng sản phẩm**

Contextual Multimodal Retrieval là một lớp search cho chat history cho phép người dùng tìm lại media bằng ngôn ngữ tự nhiên, dựa trên cả nội dung trực quan của media và bối cảnh conversation liên quan tới nó.

Một query có thể chứa nhiều loại thông tin cùng lúc.

Ví dụ:

> “Tìm ảnh con chó B gửi trong chuyến Đà Lạt.”

Người dùng đang mô tả ít nhất ba constraint:

Target: image

Visual content: dog

Sender: B

Context: Dalat trip

Không constraint nào riêng lẻ đủ để xác định kết quả.

Visual search có thể tìm ra rất nhiều ảnh chó. Search theo B có thể tìm ra hàng nghìn ảnh do B gửi. Search “Đà Lạt” có thể tìm được conversation liên quan tới chuyến đi.

Sản phẩm phải tìm được **ảnh thỏa đồng thời tất cả các thông tin mà người dùng nhớ**.

## **3. Context là một phần của media**

Trong chat, media không tồn tại độc lập.

Một image luôn được gửi thông qua một message và message đó nằm trong một conversation lớn hơn:

Image

↓

Message

├── sender

├── timestamp

├── reply / thread

├── channel / group

└── surrounding conversation

Vì vậy một image có hai lớp meaning.

Image

├── Visual meaning

│ └── thứ xuất hiện trong ảnh

│

└── Conversational meaning

└── tại sao, khi nào và trong hoàn cảnh nào ảnh được gửi

Ví dụ một ảnh có thể chỉ cho thấy một căn phòng ngủ.

Visual model có thể hiểu:

bedroom

window

balcony

wooden floor

Nhưng conversation có thể cho biết:

đây là căn hộ thứ ba

được đi xem cuối tuần trước

có ban công

là căn cả nhóm đang cân nhắc thuê

Đối với người dùng, lớp context thứ hai thường quan trọng không kém nội dung ảnh.

## **4. Các kiểu tìm kiếm mà sản phẩm cần hỗ trợ**

Không phải query nào cũng phụ thuộc context ở cùng một mức độ.

Một số query gần như thuần visual:

> “Tìm ảnh có chó.”

Một số chủ yếu dựa vào metadata:

> “Tìm ảnh B gửi hôm qua.”

Một số chủ yếu dựa vào conversation:

> “Ảnh căn hộ mình định thuê tháng trước.”

Và nhóm quan trọng nhất là query kết hợp:

> “Ảnh con chó hồi đi Đà Lạt.”
>
> “Ảnh con 3060 hôm build máy.”
>
> “Screenshot lỗi checkout trong incident tuần trước.”

Ở nhóm cuối, search engine không thể chỉ tìm image giống query nhất. Nó còn phải xác định image nào thuộc **đúng sự kiện hoặc conversation mà user đang ám chỉ**.

## **5. Context–Media Association**

Đây là vấn đề trung tâm của sản phẩm.

Giả sử hệ thống tìm được:

Text evidence:

Dalat trip ✓

Visual evidence:

dog.jpg ✓

Điều đó vẫn chưa chứng minh:

dog.jpg ∈ Dalat trip

Ảnh chó có thể đến từ một conversation hoàn toàn khác.

Kết quả đúng phải thỏa:

VisualMatch(dog)

AND

ContextMatch(Dalat trip)

AND

SenderMatch(B)

Vì vậy hệ thống phải có khả năng xác định mối liên hệ giữa media và conversation context thay vì chỉ cộng score của hai search engine độc lập.

Có thể xem đây là **Context–Media Bridge**:

Conversation Context

↕

Media

Đây là capability phân biệt sản phẩm với một image search engine thông thường.

## **6. Long-range context**

Context không phải lúc nào cũng nằm ngay cạnh media.

Một conversation có thể kéo dài từ lúc bắt đầu một việc cho tới lúc kết thúc:

lên kế hoạch

→ lựa chọn

→ chuẩn bị

→ thực hiện

→ kết thúc

→ gửi ảnh

Ví dụ trong một chuyến đi Đà Lạt:

Ngày 1:  
bàn kế hoạch đi Đà Lạt

Ngày 3:  
chốt chỗ ở và lịch đi

Ngày 6:  
chuẩn bị rồi xuất phát

Ngày 7:  
đi chơi, ăn uống và tham quan

Ngày 8:  
gửi một loạt ảnh của chuyến đi, trong đó có ảnh một con chó

User sau đó chỉ hỏi:

“Tìm ảnh con chó hồi đi Đà Lạt.”

Từ góc nhìn người dùng, toàn bộ chuỗi trên là một context duy nhất dù những message liên quan có thể cách nhau khá xa.

Một sản phẩm contextual retrieval tốt phải có khả năng tận dụng loại continuity này thay vì yêu cầu keyword như “Đà Lạt” phải xuất hiện ngay cạnh image.

## **7. Interleaved conversation**

Group chat còn khó hơn vì nhiều conversation thường diễn ra đồng thời.

Ví dụ:

A: prod lại chết rồi

B: Pine Hill còn phòng đấy

C: Redis timeout

B: book luôn nhé

A: check DB connection đi

B: thứ sáu 6h xuất phát

Trong cùng một message stream có ít nhất hai context:

production incident

trip planning

Nếu user hỏi:

> “Screenshot lúc prod chết đâu?”

thì production context quan trọng.

Nếu user hỏi:

> “Ảnh hồi chuyến đi đâu?”

thì trip context mới quan trọng.

Sản phẩm vì vậy không thể coi tất cả message gần nhau về thời gian là cùng một context. Context phải phụ thuộc vào **query mà user đang hỏi**.

## **8. User không cần nhớ chính xác**

Một mục tiêu quan trọng của sản phẩm là giảm yêu cầu người dùng phải nhớ metadata.

Người dùng không nên cần biết:

filename

message ID

ngày chính xác

channel chính xác

caption chính xác

Họ chỉ cần nhớ những clue tự nhiên như:

ai gửi

ảnh có gì

đang làm gì lúc đó

sự kiện nào

vấn đề nào đang được bàn

khoảng thời gian tương đối

Ví dụ:

> “cái ảnh trần bị thấm hôm trước”
>
> “ảnh scoreboard trận B đổi sang sniper”
>
> “ảnh whiteboard lúc team chốt auth flow”
>
> “ảnh backdrop xanh sinh nhật Linh”

Những query như vậy gần với cách con người nhớ lịch sử hơn là cách dữ liệu được lưu trong database.

## **9. Image-first V1**

Sản phẩm có thể mở rộng thành multimodal retrieval, nhưng phiên bản đầu chỉ tập trung vào **image retrieval**.

V1 cần giải tốt ba nhóm use case:

Visual search

"tìm ảnh có chó"

Context / metadata search

"tìm ảnh B gửi hôm qua"

Visual + conversational context

"tìm ảnh chó hồi chuyến Đà Lạt"

Trong V1, mỗi image được hiểu thông qua hai nguồn:

Image

├── visual representation

└── original chat context

Screenshot vẫn được coi là image. Những query phụ thuộc text nằm bên trong screenshot như:

> “Screenshot có lỗi Connection refused.”

có thể cần OCR.

Những query yêu cầu hiểu sâu hơn nội dung ảnh có thể sử dụng VLM trên một tập candidate nhỏ.

Không cần chạy OCR hoặc VLM đắt tiền trên toàn bộ image corpus nếu retrieval thông thường đã đủ để thu hẹp candidate.

## **10. Trải nghiệm tìm kiếm**

User interaction nên đơn giản như search bình thường.

User nhập:

> “Ảnh con chó hồi Đà Lạt.”

Kết quả không chỉ nên trả một image mà còn giữ provenance để người dùng hiểu tại sao nó được chọn.

Ví dụ:

DOG_IMG.jpg

Sent by: B

Conversation: trip discussion

Relevant context:

"...ảnh hôm đó đây..."

Matched because:

\- visual: dog

\- contextual evidence: Dalat trip

Provenance cũng giúp hệ thống xử lý trường hợp evidence không đủ.

Nếu có nhiều candidate giống nhau nhưng không thể xác định chắc chắn image nào thuộc context user muốn, hệ thống nên hỏi clarification thay vì tự chọn:

> “Bạn đang nói chuyến Đà Lạt tháng 6 hay tháng 10?”

## **11. Evaluation**

Evaluation phải đo **khả năng tìm đúng media**, không chỉ chất lượng câu trả lời cuối cùng của LLM.

Metric quan trọng nhất là:

**Exact Image Accuracy** — hệ thống có trả đúng image mà user muốn hay không.

Ngoài ra cần đo:

- Image Recall@K: đúng image có lọt vào candidate set không.

- Context Evidence Recall: hệ thống có tìm được những message cần thiết để hiểu image không.

- Context Precision: evidence retrieve về có thuộc đúng conversation hay bị lẫn conversation khác.

- Context–Image Bridge Recall: hệ thống có xác định đúng relation giữa image và context hay không.

- Constraint Satisfaction Accuracy: image cuối cùng có thỏa đầy đủ visual, sender, time và context constraint không.

Ví dụ:

dog ✓

sender B ✓

Dalat trip ✗

vẫn phải tính là sai.

Ngoài accuracy cần theo dõi latency, cost/query và số lần model/search engine phải được gọi để đảm bảo khả năng triển khai thực tế.

Benchmark nên đặc biệt có những case:

short context

long context

interleaved conversations

sparse contextual anchors

high vocabulary drift

multiple similar images

multiple similar events

để kiểm tra hệ thống có thực sự hiểu context hay chỉ hoạt động khi keyword nằm gần media.

## **12. Hướng mở rộng**

Sau khi image retrieval ổn định, cùng product model có thể mở rộng sang:

video

voice

documents

other media

Nguyên tắc không thay đổi.

Video có nội dung visual và transcript nhưng cũng cần biết nó được gửi trong context nào.

Voice có transcript và speaker information nhưng meaning vẫn phụ thuộc conversation.

Document có nội dung riêng nhưng user vẫn có thể nhớ nó bằng hoàn cảnh:

> “file B gửi lúc team đang bàn pricing.”

Vì vậy image-first không phải một sản phẩm khác mà là bước đầu của một hệ thống contextual multimodal retrieval rộng hơn.

## **13. Giá trị chính**

Text search trả lời:

> **Mọi người đã nói gì?**

Visual search trả lời:

> **Trong ảnh có gì?**

Contextual Multimodal Retrieval kết nối hai phía:

> **What the media contains + What was happening when it was shared.**

Nhờ đó người dùng có thể tìm lại media theo cách họ thực sự nhớ nó, thay vì phải nhớ cách hệ thống lưu nó.

“Ảnh con chó hồi Đà Lạt.”

“Ảnh con 3060 hôm build máy.”

“Screenshot B gửi lúc debug deploy.”

“Ảnh căn hộ mà mình định thuê tháng trước.”

Mục tiêu của sản phẩm là biến toàn bộ chat history thành một **searchable contextual media memory**, nơi media có thể được tìm lại bằng cả nội dung của chính nó và câu chuyện xung quanh nó.
