아래 카드 상품 페이지 문서에서 다음 12개 필드를 추출하라.

- issuer_name (발급사명)
- product_name_norm (상품명 정규화) — 필수
- annual_fee_amount (연회비, 정수 원)
- family_annual_fee_amount (가족연회비, 정수 원)
- overseas_payment_yn (해외결제 가능, Y/N)
- transit_card_yn (교통카드 가능, Y/N)
- joinable_yn (가입 가능 여부, Y/N)
- product_name (상품명) — 필수
- benefit_text (혜택 원문)
- join_condition_text (가입조건 원문)
- family_annual_fee_text (가족연회비 원문)
- annual_fee_text (연회비 원문)

반드시 위 12개 키를 모두 가진 하나의 JSON 객체로만 반환하라.

<document>
{{raw_text}}
</document>
