from collections import defaultdict

from schemas import ParserContent

## 조문 내용에서 정규표현식으로 가져올 것
'''
1. 동법 내 타 조항
2. 동법 내 별표
3. 동법 내 부칙
4. 타법법령명 및 조문번호

'''
# 조문 <-> 부칙 연결  
def map_article_addenda(article_list: list[ParserContent], addendum_list: list[ParserContent]) \
    -> tuple[list[ParserContent], list[ParserContent]]:
    '''
        공포일자를 기준으로 부칙과 조문의 ID를 양방향 연결 
    '''
    # 조문 데이터를 공포일자를 키로 하는 해시 테이블로 변환 (일대다 관계 허용)
    article_dict: dict[str, list[ParserContent]] = defaultdict(list)
    for article in article_list:
        article_dict[article.metadata.announce_date].append(article)
 
    # 부칙 데이터를 역순으로 순회하면서 관련 조문 ID 추가
    for addendum in reversed(addendum_list):
        announce_date = addendum.metadata.announce_date

        if announce_date in article_dict:
            for article in article_dict[announce_date]:
                article_id = article.metadata.article_id

                # 부칙에 관련 조문 ID 추가
                addendum.metadata.related_articles = addendum.metadata.related_articles or []
                if article_id not in addendum.metadata.related_articles:
                    addendum.metadata.related_articles.append(article_id)

                # 조문에 관련 부칙 ID 추가
                article.metadata.related_addenda = article.metadata.related_addenda or []
                if addendum.metadata.addendum_id not in article.metadata.related_addenda:
                    article.metadata.related_addenda.append(addendum.metadata.addendum_id)
    
    return article_list, addendum_list

# 조문 <- 별표 연결 # TODO 조문에서 별표를 언급하는 것을 검사해야함... 지금 별표에서만 관련 조문 검사중. 
def map_article_appendix(article_list: list[ParserContent], appendix_list: list[ParserContent]) \
    -> list[ParserContent]:
    '''
        별표의 조문 ID 값에 해당하는 조문 메타데이터에 관련 별표 ID 추가
    '''
    # 조문 ID를 기준으로 별표 ID를 매핑할 딕셔너리 생성 (article_id -> appendices)
    # key: article_id, value: set of appendix_ids
    appendix_lookup = defaultdict(set) 

    # appendices에서 각 별표의 관련된 조문 ID들을 찾아서 저장
    for appendix in appendix_list:
        if appendix.metadata.related_articles:
            for article_id in appendix.metadata.related_articles:
                appendix_lookup[article_id].add(appendix.metadata.appendix_id)
    
    # articles에서 각 조문에 대해 연결된 별표 ID들을 추가
    for article in article_list:
        article.metadata.related_appendices = article.metadata.related_appendices or []    
        if article.metadata.article_id in appendix_lookup:
            article.metadata.related_appendices.extend(appendix_lookup[article.metadata.article_id])

    return article_list

# TODO 조문에서 언급된 타 법령명 가져오기. 