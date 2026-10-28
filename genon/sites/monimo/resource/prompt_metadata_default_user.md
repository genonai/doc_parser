다음은 한국어로 작성된 금융 보고서 문서의 일부입니다. 이 문서에서 아래 정보를 정확히 추출해주세요:

1. 최초 작성일(created_date) (날짜가 여러 개 있으면 '보고자료' 또는 '회의자료'에 가까운 날짜 사용)
2. 작성자 이름(name)과 소속(affiliation) 또는 직책(position) (예:  ○○팀 홍길동 과장)
3. 작성자 전화번호(phone) (내선번호 포함 시 함께 출력)

문서에서 찾을 수 없는 항목은 추측하지 말고 created_date는 null, authors는 빈 배열 []로 출력하세요.

문서:
---
{{raw_text}}
---

출력 형식은 반드시 아래와 같이 맞춰주세요 (JSON 형식):

{
  "created_date": "YYYY-MM-DD",
  "authors": [
    {"name": "<이름1>", "affiliation": "<소속1>", "position": "<직급1>", "phone": "<연락처1>"},
    {"name": "<이름2>", "affiliation": "<소속2>", "position": "<직급2>", "phone": "<연락처2>"}
  ]
}
